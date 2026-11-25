from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from guardrails.context.config.profile import ProfileError
from guardrails.context.config.repo_config import ConfigError, load_config
from guardrails.foundation import decision_store
from guardrails.foundation.findings import (
    Finding,
    catalogue_rules,
    exit_code,
    format_findings,
)
from guardrails.toolkit.dispatch import (
    SUBCOMMANDS,
    adapter_module,
    checker_module,
    config_finding,
    honours_exclusions,
    module_names,
    outside_exclusions,
    run_modules,
)


def module_rules(module: str) -> list[tuple[str, str]]:
    return [
        (rule["id"], rule["summary"])
        for rule in catalogue_rules()
        if any(reference["module"] == module for reference in rule.get("reference", []))
    ]


def usage() -> str:
    width = max(len(name) for name in SUBCOMMANDS)
    lines = [
        "usage: guardrails <subcommand> [--repo DIR] [args...]",
        "",
        "subcommands:",
    ]
    lines.extend(
        f"  {name.ljust(width)}  {item.summary}" for name, item in SUBCOMMANDS.items()
    )
    return "\n".join(lines)


def wants_help(argv: list[str]) -> bool:
    options = argv[: argv.index("--")] if "--" in argv else argv
    return any(arg in {"-h", "--help"} for arg in options)


def subcommand_help(name: str) -> str:
    command = SUBCOMMANDS[name]
    lines = [
        f"usage: guardrails {name} [--repo DIR] [args...]",
        "",
        command.summary,
    ]
    for module_name in command.modules:
        lines.extend(["", f"{module_name}:"])
        lines.extend(
            f"  {rule_id}: {summary}" for rule_id, summary in module_rules(module_name)
        )
    return "\n".join(lines)


class UsageError(Exception):
    pass


@dataclass(frozen=True)
class Target:
    """The checkout a subcommand runs on and its repo config (None for init)."""

    repo: Path
    config: dict[str, Any] | None


REPO_OPTION = "--repo"


def resolve_repo(argv: list[str]) -> tuple[Path, list[str]]:
    """A leading `--repo DIR` (or --repo=DIR); the working directory without one."""
    if not argv:
        return Path.cwd().resolve(), argv
    option, _, value = argv[0].partition("=")
    if option != REPO_OPTION:
        return Path.cwd().resolve(), argv
    rest = argv[1:]
    if not value:
        if not rest:
            raise UsageError(f"{REPO_OPTION} needs a directory")
        value, rest = rest[0], rest[1:]
    return Path(value).expanduser().resolve(), rest


def resolve_target(argv: list[str], needs_config: bool) -> tuple[Target, list[str]]:
    """The repo and, when the subcommand reads settings, its .agentrails/config.json.

    A missing, invalid or unresolvable config raises ConfigError; there is no default.
    """
    repo, rest = resolve_repo(argv)
    return Target(repo, load_config(repo) if needs_config else None), rest


DECISION_LOG_OPTION = "--log-decisions"
# The line prefix a checker prints when it cannot log; the harness reports it without changing a verdict.
STORE_ERROR = "decision store: "
DECISION_TRIGGERS = ("commit", "prove", "pr", "manual")


def split_decision_log(argv: list[str]) -> tuple[str | None, list[str]]:
    """A leading `--log-decisions TRIGGER`: log this run's rule results to the decision store."""
    if not argv or argv[0] != DECISION_LOG_OPTION:
        return None, argv
    if len(argv) < 2 or argv[1] not in DECISION_TRIGGERS:
        raise UsageError(
            f"{DECISION_LOG_OPTION} needs a trigger: {', '.join(DECISION_TRIGGERS)}"
        )
    return argv[1], argv[2:]


def option_value(argv: list[str], option: str) -> str | None:
    for index, arg in enumerate(argv):
        if arg == option and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith(f"{option}="):
            return arg.partition("=")[2]
    return None


def check_rows(
    checker: str,
    rules: list[str],
    findings: list[Finding],
    context: decision_store.DecisionContext,
) -> list[dict[str, object]]:
    """One decision store row (source=check) per distinct finding, and a pass row per clean rule."""
    base: dict[str, object] = {
        "call_id": decision_store.new_id(),
        "ts": decision_store.now(),
        "source": "check",
        "question": checker,
        "mode": "enforced",
        "status": "ok",
        "machine": decision_store.machine_name(),
        "state_kept": 0,
        **context.columns(),
    }
    flagged = sorted({(item.rule_id, item.path) for item in findings})
    rows: list[dict[str, object]] = [
        {
            **base,
            "id": decision_store.new_id(),
            "judgment": rule,
            "outcome": "flag",
            "item_kind": "path",
            "item": path,
        }
        for rule, path in flagged
    ]
    passed = sorted(set(rules) - {rule for rule, _ in flagged})
    rows.extend(
        {**base, "id": decision_store.new_id(), "judgment": rule, "outcome": "pass"}
        for rule in passed
    )
    return rows


def log_decisions(
    name: str, target: Target, argv: list[str], findings: list[Finding], trigger: str
) -> None:
    """Log the run's rule results; a store failure is printed and never changes the exit code."""
    config = target.config
    context = decision_store.DecisionContext(
        trigger,
        repo=None if config is None else str(config["repo"]["slug"]),
        commit=option_value(argv, "--head"),
    )
    rules = [
        rule for module in SUBCOMMANDS[name].modules for rule, _ in module_rules(module)
    ]
    try:
        decision_store.record_decisions(check_rows(name, rules, findings, context))
    except decision_store.StoreError as error:
        print(f"{STORE_ERROR}{error}", file=sys.stderr)


def run_subcommand(name: str, target: Target, argv: list[str]) -> list[Finding]:
    if name == "init":
        init_command = checker_module(SUBCOMMANDS[name].modules[0])
        drafted: list[Finding] = init_command.run_init(target.repo, argv)
        return drafted
    config = target.config
    assert config is not None
    try:
        if name == "adapter":
            module, argv = adapter_module(config, argv)
            modules: tuple[str, ...] = (module,)
        else:
            modules = module_names(name, config)
    except ProfileError as error:
        return [config_finding(error)]
    findings = run_modules(modules, target.repo, config, argv)
    if honours_exclusions(name, argv):
        return outside_exclusions(findings, config)
    return findings


TARGETLESS = frozenset({"shadow-review", "config-check"})


def early_exit(args: list[str]) -> int | None:
    """Usage, an unknown subcommand, shadow-review (no repository target) and subcommand help."""
    if not args or args[0] in {"-h", "--help", "help"}:
        print(usage())
        return 0 if args else 2
    name, rest = args[0], args[1:]
    if name not in SUBCOMMANDS:
        print(f"unknown subcommand: {name}\n\n{usage()}", file=sys.stderr)
        return 2
    if name in TARGETLESS:
        handled: int = checker_module(SUBCOMMANDS[name].modules[0]).main(rest)
        return handled
    if wants_help(rest) and name != "adapter" and SUBCOMMANDS[name].needs_config:
        print(subcommand_help(name))
        return 0
    return None


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    early = early_exit(args)
    if early is not None:
        return early
    name, rest = args[0], args[1:]
    try:
        target, rest = resolve_target(rest, needs_config=SUBCOMMANDS[name].needs_config)
        trigger, rest = split_decision_log(rest)
    except UsageError as error:
        print(f"{name}: {error}", file=sys.stderr)
        return 2
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    try:
        findings = run_subcommand(name, target, rest)
    except ModuleNotFoundError as error:
        print(
            f"{name}: cannot import {error.name}; run guardrails with the extra this "
            "subcommand needs (uv run --project <guardrails> --extra pytest ...)",
            file=sys.stderr,
        )
        return 2
    if trigger is not None:
        log_decisions(name, target, rest, findings, trigger)
    if findings:
        print(format_findings(findings))
    else:
        print(
            f"{name}: ok",
            file=sys.stderr if SUBCOMMANDS[name].prints_data else sys.stdout,
        )
    return exit_code(findings)


if __name__ == "__main__":
    raise SystemExit(main())
