from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any, cast

from guardrails.context.config import profile
from guardrails.foundation.child_env import clean_environment
from guardrails.foundation.decision_store import shadow_capable
from guardrails.foundation.findings import catalogue_rules
from guardrails.host.process import run_text

CONFIG_DIR = ".agentrails"
CONFIG_FILE = "config.json"
SCHEMA_VERSION = 1
QUESTIONNAIRE_VERSION = 1
SECTIONS = (
    "schemaVersion",
    "repo",
    "onboarding",
    "autonomy",
    "ci",
    "gates",
    "judgments",
    "mergePolicies",
    "afterMerge",
    "commands",
    "checkers",
    "structure",
    "houseStyle",
    "tests",
    "exceptions",
    "approvals",
)
OPTIONAL_SECTIONS = frozenset({"afterMerge"})
CHECKED_SECTIONS = (
    "commands",
    "checkers",
    "structure",
    "houseStyle",
    "tests",
    "autonomy",
    "ci",
    "gates",
    "judgments",
    "mergePolicies",
    "afterMerge",
    "exceptions",
    "approvals",
)
PERMISSIONS = ("allowed", "ask")
AUTONOMY_ACTIONS = ("branch", "pr", "merge")
CI_RUNNERS = ("github-actions", "ci-gate")
GATE_KEYS = ("commitChecks", "pathCheck", "prove", "structureReview", "audit", "rules")
EXEMPTION_TARGETS = (
    "tiers.t3",
    "verification_surface",
    "identity_artefacts",
    "run_events.nightly_scheduler",
)
APPROVAL_KINDS = ("budget", "ratchet", "skip", "exemption")
NOT_CONFIGURABLE = frozenset({"in-scope/ask_first", "dependency-severity/impact"})
MERGE_METHODS = ("rebase", "merge")
SKILLS = ("setup-repo-proofs", "test-guidance")
CHECK_STEPS = (
    "python-quality",
    "python-fanout",
    "python-scalability",
    "surface-lint",
    "adapter lint",
    "python-test-hygiene",
)
CODE_RULE_KINDS = frozenset({"tool", "bespoke"})
TEST_KEYS = (
    "tiers",
    "adapters",
    "public_entry_points",
    "run_events",
    "fast_tier_budget",
    "coverage",
    "guards",
    "environments",
    "verification_surface",
    "identity_artefacts",
    "layout",
    "command_notes",
)
TESTS_SCHEMA: dict[str, Any] = {key: profile.SCHEMA[key] for key in TEST_KEYS}
STRUCTURE_SCHEMA: dict[str, Any] = {
    **profile.SCHEMA["structure"],
    "ownership_areas": profile.SCHEMA["ownership_areas"],
}
CHECKERS_SCHEMA = {
    f"{section}{profile.OPTIONAL_MARK}": "object"
    for section in profile.GUARDRAILS_SECTIONS
}
MOVED_CHECKER_KEYS = {
    ("coverage_ratchet", "approvals"): "ratchet",
    ("test_evidence", "approved_skips"): "skip",
}
NOT_CONFIGURABLE_REASONS = {
    "in-scope/ask_first": "is not configurable: the ask-first rail is always on and enforced",
    "dependency-severity/impact": "is not configurable: the severity rubric needs it",
}
JUDGMENTS_DIR = Path(__file__).resolve().parents[4] / "judgments"
GIT_TIMEOUT_S = 20
SLUG = re.compile(r"[a-z0-9_.-]+/[a-z0-9_.-]+")
SHA256 = re.compile(r"[0-9a-f]{64}")
ISO_TIME = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
)
ORIGIN = re.compile(
    r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?"
)

Problems = list[tuple[str, str]]
Validator = Callable[[object, str, Problems], None]
LeafCheck = Callable[[object], "str | None"]


class ConfigError(Exception):
    def __init__(self, kind: str, path: Path | None, problems: Sequence[str]) -> None:
        super().__init__(f"config {kind}: {'; '.join(problems)}")
        self.kind = kind
        self.path = path
        self.problems = list(problems)


@dataclass(frozen=True)
class OfferedQuestion:
    mode: str
    shadowable: bool


def _git(cwd: str | os.PathLike[str], *args: str) -> subprocess.CompletedProcess[str]:
    command = f"git {' '.join(args)} in {cwd}"
    environment = {**clean_environment(), "LC_ALL": "C", "LANGUAGE": "C"}
    try:
        return run_text(
            ["git", "-C", os.fspath(cwd), *args],
            env=environment,
            errors="replace",
            timeout=GIT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as error:
        raise ConfigError("unresolved", None, [f"{command} timed out"]) from error
    except OSError as error:
        raise ConfigError(
            "unresolved", None, [f"{command} could not run: {error}"]
        ) from error


def _failed(command: str, result: subprocess.CompletedProcess[str]) -> ConfigError:
    stderr = result.stderr.strip()
    detail = f": {stderr}" if stderr else ""
    return ConfigError(
        "unresolved", None, [f"{command} exited {result.returncode}{detail}"]
    )


def main_checkout(cwd: str | os.PathLike[str]) -> Path:
    result = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if result.returncode == 128 and "not a git repository" in result.stderr:
        raise ConfigError("outside", None, [f"{cwd} is not in a git repository"])
    if result.returncode != 0:
        raise _failed(f"git rev-parse --git-common-dir in {cwd}", result)
    common = Path(os.path.realpath(result.stdout.strip()))
    if common.name != ".git":
        raise ConfigError(
            "unresolved",
            None,
            [
                f"git common directory {common} is not a checkout's .git directory "
                "(a bare repository or a separate git dir has no main checkout)"
            ],
        )
    return common.parent


def parse_origin(url: str) -> str | None:
    match = ORIGIN.fullmatch(url.strip())
    if not match or {match[1], match[2]} & {".", ".."}:
        return None
    return f"{match[1]}/{match[2]}".lower()


def origin_slug(cwd: str | os.PathLike[str]) -> str | None:
    result = _git(cwd, "remote", "get-url", "origin")
    if result.returncode == 0:
        return parse_origin(result.stdout)
    if (result.returncode == 2 and "No such remote" in result.stderr) or (
        result.returncode == 128 and "not a git repository" in result.stderr
    ):
        return None
    raise _failed(f"git remote get-url origin in {cwd}", result)


def config_path(cwd: str | os.PathLike[str]) -> Path:
    return main_checkout(cwd) / CONFIG_DIR / CONFIG_FILE


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for key, value in pairs:
        if key in found:
            raise ValueError(f"duplicate key {key!r}")
        found[key] = value
    return found


def read_config(path: Path) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ConfigError("missing", path, [f"{path} does not exist"]) from error
    except (OSError, UnicodeError) as error:
        raise ConfigError(
            "invalid", path, [f"{path} cannot be read: {error}"]
        ) from error
    try:
        return cast(object, json.loads(text, object_pairs_hook=_unique_keys))
    except ValueError as error:
        raise ConfigError(
            "invalid", path, [f"config: is not valid JSON: {error}"]
        ) from error


def load_config(cwd: str | os.PathLike[str]) -> dict[str, Any]:
    path = config_path(cwd)
    origin = origin_slug(cwd)
    if origin is None:
        raise ConfigError(
            "outside",
            path,
            [
                f"{cwd} has no GitHub origin remote; agent rails configure GitHub repositories only"
            ],
        )
    config = read_config(path)
    problems = validate(config, origin=origin)
    if problems:
        raise ConfigError("invalid", path, problems)
    return cast(dict[str, Any], config)


@cache
def rule_ids() -> frozenset[str]:
    return frozenset(rule["id"] for rule in catalogue_rules())


@cache
def code_check_rules() -> frozenset[str]:
    return frozenset(
        rule["id"] for rule in catalogue_rules() if rule.get("kind") in CODE_RULE_KINDS
    )


def _judgment_questions(directory: Path) -> dict[str, OfferedQuestion]:
    thresholds = json.loads((directory / "thresholds.json").read_text(encoding="utf-8"))
    questions = json.loads((directory / "questions.json").read_text(encoding="utf-8"))
    offered: dict[str, OfferedQuestion] = {}
    for ident, decision in thresholds["decisions"].items():
        kind = questions["questions"][ident]["type"]
        offered[f"{directory.name}/{ident}"] = OfferedQuestion(
            decision["mode"], shadow_capable(kind, decision)
        )
    return offered


@cache
def judgment_questions() -> dict[str, OfferedQuestion]:
    """Every shipped judgment question as <judgment>/<question>."""
    found: dict[str, OfferedQuestion] = {}
    for directory in sorted(JUDGMENTS_DIR.iterdir()):
        if (directory / "thresholds.json").is_file():
            found.update(_judgment_questions(directory))
    return found


@cache
def offered_questions() -> dict[str, OfferedQuestion]:
    return {
        key: value
        for key, value in judgment_questions().items()
        if key not in NOT_CONFIGURABLE
    }


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return None
    return "must be a non-blank string"


def _string(value: object) -> str | None:
    return None if isinstance(value, str) else "must be a string"


def _boolean(value: object) -> str | None:
    return None if isinstance(value, bool) else "must be true or false"


def _argv(value: object) -> str | None:
    if (
        isinstance(value, list)
        and value
        and all(isinstance(item, str) and item.strip() for item in value)
    ):
        return None
    return "must be a non-empty argv array of non-blank strings"


def _texts(value: object) -> str | None:
    if isinstance(value, list) and value and all(_text(item) is None for item in value):
        return None
    return "must be a non-empty list of non-blank strings"


def _iso_time(value: object) -> str | None:
    if isinstance(value, str) and ISO_TIME.fullmatch(value):
        try:
            datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            return None
    return "must be an ISO 8601 date-time with a UTC offset, e.g. 2026-11-24T09:00:00Z"


def _sha256(value: object) -> str | None:
    if isinstance(value, str) and SHA256.fullmatch(value):
        return None
    return "must be a lowercase hex sha256 digest"


def _slug(value: object) -> str | None:
    if isinstance(value, str) and SLUG.fullmatch(value):
        return None
    return "must be a lowercase GitHub owner/name"


def _rule_id(value: object) -> str | None:
    if isinstance(value, str) and value in rule_ids():
        return None
    return f"{json.dumps(value)} is not a rule id in the guardrail catalogue"


def _exception_rule(value: object) -> str | None:
    """Exceptions name a catalogue rule or a judgment question (whose proposals they drop)."""
    if isinstance(value, str) and value in judgment_questions():
        return None
    problem = _rule_id(value)
    return None if problem is None else f"{problem} or a judgment question"


def _questionnaire(value: object) -> str | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        return "must be an integer of at least 1"
    if value != QUESTIONNAIRE_VERSION:
        return (
            f"is {value} but the extension's questionnaire is version "
            f"{QUESTIONNAIRE_VERSION}; ask the owner to run /agentrails configure"
        )
    return None


def _one_of(options: Sequence[str]) -> LeafCheck:
    def check(value: object) -> str | None:
        if isinstance(value, str) and value in options:
            return None
        return f"must be one of {list(options)}"

    return check


def _literal(expected: object) -> LeafCheck:
    def check(value: object) -> str | None:
        if type(value) is type(expected) and value == expected:
            return None
        return f"must be {json.dumps(expected)}"

    return check


def _leaf(check: LeafCheck) -> Validator:
    def validate_leaf(value: object, path: str, problems: Problems) -> None:
        problem = check(value)
        if problem:
            problems.append((path, problem))

    return validate_leaf


UNKNOWN_KEY_PROBLEM = "is not a config key; remove it or fix its spelling"


def _strict(
    required: Mapping[str, Validator], optional: Mapping[str, Validator] | None = None
) -> Validator:
    allowed = {**required, **(optional or {})}

    def validate_object(value: object, path: str, problems: Problems) -> None:
        if not isinstance(value, dict):
            problems.append((path, "must be an object"))
            return
        for extra in sorted(set(value) - set(allowed)):
            problems.append((f"{path}.{extra}", UNKNOWN_KEY_PROBLEM))
        for name, validator in allowed.items():
            if name in value:
                validator(value[name], f"{path}.{name}", problems)
            elif name in required:
                problems.append((f"{path}.{name}", "is missing"))

    return validate_object


def _union(tag: str, variants: Sequence[tuple[object, Validator]]) -> Validator:
    def validate_union(value: object, path: str, problems: Problems) -> None:
        if not isinstance(value, dict):
            problems.append((path, "must be an object"))
            return
        found = value.get(tag)
        for expected, validator in variants:
            if type(found) is type(expected) and found == expected:
                validator(value, path, problems)
                return
        options = json.dumps([expected for expected, _ in variants])
        problems.append((f"{path}.{tag}", f"must be one of {options}"))

    return validate_union


def _list_of(item: Validator) -> Validator:
    def validate_list(value: object, path: str, problems: Problems) -> None:
        if not isinstance(value, list):
            problems.append((path, "must be a list"))
            return
        for index, entry in enumerate(value):
            item(entry, f"{path}[{index}]", problems)

    return validate_list


def _unique(
    value: object, path: str, fields: Sequence[str], problems: Problems
) -> None:
    seen: set[tuple[object, ...]] = set()
    for index, entry in enumerate(value if isinstance(value, list) else []):
        if not isinstance(entry, dict):
            continue
        key = tuple(json.dumps(entry.get(field)) for field in fields)
        if key in seen:
            names = ", ".join(fields)
            problems.append((f"{path}[{index}]", f"repeats an earlier ({names})"))
        seen.add(key)


def check_config_exemption(value: dict[str, Any], key: str, problems: Problems) -> None:
    for extra in sorted(set(value) - {"none"}):
        problems.append((f"{key}.{extra}", UNKNOWN_KEY_PROBLEM))
    if value.get("none") is not True:
        problems.append((
            f"{key}.none",
            "must be true; the reason lives in the exemption approval",
        ))


CONFIG_RULES = profile.SectionRules(check_config_exemption, UNKNOWN_KEY_PROBLEM)


def _with_prefix(prefix: str, found: Problems) -> Problems:
    return [(f"{prefix}.{key}" if key else prefix, message) for key, message in found]


def _tests(value: object, path: str, problems: Problems) -> None:
    if not isinstance(value, dict):
        problems.append((path, "must be an object"))
        return
    found: Problems = []
    profile.check_section(value, TESTS_SCHEMA, "", found, CONFIG_RULES)
    found.extend(profile.adapter_problems(value))
    problems.extend(_with_prefix(path, found))


def _structure(value: object, path: str, problems: Problems) -> None:
    profile.check_section(value, STRUCTURE_SCHEMA, path, problems, CONFIG_RULES)


def _house_style(value: object, path: str, problems: Problems) -> None:
    profile.check_section(
        value, profile.SCHEMA["house_style"], path, problems, CONFIG_RULES
    )
    version = value.get("version") if isinstance(value, dict) else None
    problem = profile.house_style_version_problem(version)
    if problem:
        problems.append((f"{path}.version", problem))


def _check_run(value: object, path: str, problems: Problems) -> None:
    if not isinstance(value, list) or not value:
        problems.append((
            path,
            f"must be a non-empty list of steps from {list(CHECK_STEPS)}",
        ))
        return
    unknown = [step for step in value if step not in CHECK_STEPS]
    if unknown:
        problems.append((
            path,
            f"names unknown steps {unknown}; choose from {list(CHECK_STEPS)}",
        ))


def _checkers(value: object, path: str, problems: Problems) -> None:
    profile.check_section(value, CHECKERS_SCHEMA, path, problems, CONFIG_RULES)
    if not isinstance(value, dict):
        return
    for (section, key), kind in MOVED_CHECKER_KEYS.items():
        settings = value.get(section)
        if isinstance(settings, dict) and key in settings:
            problems.append((
                f"{path}.{section}.{key}",
                f"is refused: record each entry in approvals as kind {kind!r}",
            ))
    check = value.get("check")
    if isinstance(check, dict) and "run" in check:
        _check_run(check["run"], f"{path}.check.run", problems)


def _code_rule_key(key: str) -> str | None:
    if key in code_check_rules():
        return None
    return "is not a code-check rule id (catalogue kind tool or bespoke)"


def _rules(value: object, path: str, problems: Problems) -> None:
    if not isinstance(value, dict):
        problems.append((path, "must be an object"))
        return
    for key, enabled in value.items():
        problem = _code_rule_key(key)
        if problem:
            problems.append((f"{path}.{key}", problem))
        _leaf(_boolean)(enabled, f"{path}.{key}", problems)


JUDGMENT_SETTING = _union(
    "enabled",
    (
        (False, _strict({"enabled": _leaf(_literal(False))})),
        (
            True,
            _strict({
                "enabled": _leaf(_literal(True)),
                "mode": _leaf(_one_of(("enforced", "shadow"))),
            }),
        ),
    ),
)


def _mode_problem(question: OfferedQuestion, setting: dict[str, Any]) -> str | None:
    if setting.get("enabled") is not True:
        return None
    if setting.get("mode") == "enforced" and question.mode == "shadow":
        return (
            "is enforced but the question runs in shadow mode globally; only "
            "measurement promotes a shadow question"
        )
    if setting.get("mode") == "shadow" and not question.shadowable:
        return (
            "cannot run in shadow mode: only a bool answer or a choice option threshold "
            "has a non-blocking outcome"
        )
    return None


def _judgments(value: object, path: str, problems: Problems) -> None:
    if not isinstance(value, dict):
        problems.append((path, "must be an object"))
        return
    offered = offered_questions()
    for key, setting in value.items():
        where = f"{path}.{key}"
        if key in NOT_CONFIGURABLE_REASONS:
            problems.append((where, NOT_CONFIGURABLE_REASONS[key]))
            continue
        question = offered.get(key)
        if question is None:
            problems.append((
                where,
                f"is not a judgment question; choose from {sorted(offered)}",
            ))
            continue
        found: Problems = []
        JUDGMENT_SETTING(setting, where, found)
        problem = (
            None if found else _mode_problem(question, cast(dict[str, Any], setting))
        )
        problems.extend(found if problem is None else [(where, problem)])


TEXT = _leaf(_text)
ARGV = _leaf(_argv)
BOOLEAN = _leaf(_boolean)
ISO = _leaf(_iso_time)
SKILL_RUN = _strict({
    "version": _leaf(_sha256),
    "ranAt": ISO,
    "scope": _union(
        "kind",
        (
            ("whole", _strict({"kind": _leaf(_literal("whole"))})),
            ("changed", _strict({"kind": _leaf(_literal("changed"))})),
            (
                "paths",
                _strict({"kind": _leaf(_literal("paths")), "paths": _leaf(_texts)}),
            ),
        ),
    ),
})
ONBOARDING = _strict({
    "completedAt": ISO,
    "questionnaireVersion": _leaf(_questionnaire),
    "skills": _strict(dict.fromkeys(SKILLS, SKILL_RUN)),
})
CI = _union(
    "enabled",
    (
        (False, _strict({"enabled": _leaf(_literal(False))})),
        (
            True,
            _strict({
                "enabled": _leaf(_literal(True)),
                "runner": _leaf(_one_of(CI_RUNNERS)),
                "requiredContext": TEXT,
            }),
        ),
    ),
)
EXCEPTION = _strict(
    {
        "rule": _leaf(_exception_rule),
        "path": TEXT,
        "reason": TEXT,
        "approvedBy": TEXT,
        "approvedAt": ISO,
    },
    {"check": TEXT},
)
APPROVAL = _strict({
    "kind": _leaf(_one_of(APPROVAL_KINDS)),
    "target": TEXT,
    "reason": TEXT,
    "approvedBy": TEXT,
    "approvedAt": ISO,
})


def _exceptions(value: object, path: str, problems: Problems) -> None:
    _list_of(EXCEPTION)(value, path, problems)
    _unique(value, path, ("rule", "path", "check"), problems)


def _approvals(value: object, path: str, problems: Problems) -> None:
    _list_of(APPROVAL)(value, path, problems)
    _unique(value, path, ("kind", "target"), problems)
    for index, entry in enumerate(value if isinstance(value, list) else []):
        if (
            isinstance(entry, dict)
            and entry.get("kind") == "exemption"
            and entry.get("target") not in EXEMPTION_TARGETS
        ):
            problems.append((
                f"{path}[{index}].target",
                f"must name an exemptable field {list(EXEMPTION_TARGETS)}",
            ))


SECTION_VALIDATORS: dict[str, Validator] = {
    "schemaVersion": _leaf(_literal(SCHEMA_VERSION)),
    "repo": _strict({"slug": _leaf(_slug), "defaultBranch": TEXT}),
    "onboarding": ONBOARDING,
    "autonomy": _strict({
        action: _leaf(_one_of(PERMISSIONS)) for action in AUTONOMY_ACTIONS
    }),
    "ci": CI,
    "gates": _strict({
        **{key: BOOLEAN for key in GATE_KEYS if key != "rules"},
        "rules": _rules,
    }),
    "judgments": _judgments,
    "mergePolicies": _list_of(
        _strict({
            "base": TEXT,
            "headPrefix": _leaf(_string),
            "method": _leaf(_one_of(MERGE_METHODS)),
            "proof": BOOLEAN,
        })
    ),
    "afterMerge": _strict({"argv": ARGV}, {"paths": _leaf(_texts)}),
    "commands": _strict({"commit": ARGV}, {"ci": ARGV, "audit": ARGV, "format": ARGV}),
    "checkers": _checkers,
    "structure": _structure,
    "houseStyle": _house_style,
    "tests": _tests,
    "exceptions": _exceptions,
    "approvals": _approvals,
}


def _section(config: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name)
    return value if isinstance(value, dict) else {}


def _exempt_fields(tests: Mapping[str, Any]) -> set[str]:
    exempt: set[str] = set()
    for target in EXEMPTION_TARGETS:
        parent, _, leaf = target.rpartition(".")
        holder = tests.get(parent) if parent else tests
        if isinstance(holder, dict) and profile.is_exemption(holder.get(leaf)):
            exempt.add(target)
    return exempt


def _machine_ids(tests: Mapping[str, Any]) -> list[str]:
    budget = tests.get("fast_tier_budget")
    machines = budget.get("machines") if isinstance(budget, dict) else None
    return [
        machine["id"]
        for machine in (machines if isinstance(machines, list) else [])
        if isinstance(machine, dict) and isinstance(machine.get("id"), str)
    ]


def _approved(config: Mapping[str, Any], kind: str) -> list[tuple[int, str]]:
    entries = config.get("approvals")
    return [
        (index, entry["target"])
        for index, entry in enumerate(entries if isinstance(entries, list) else [])
        if isinstance(entry, dict)
        and entry.get("kind") == kind
        and isinstance(entry.get("target"), str)
    ]


def _approval_problems(config: Mapping[str, Any]) -> Problems:
    tests = _section(config, "tests")
    exempt, machines = _exempt_fields(tests), _machine_ids(tests)
    exemptions, budgets = _approved(config, "exemption"), _approved(config, "budget")
    problems: Problems = [
        (f"tests.{target}", f"is exempt without an exemption approval for {target!r}")
        for target in EXEMPTION_TARGETS
        if target in exempt and target not in {name for _, name in exemptions}
    ]
    problems += [
        (
            f"tests.fast_tier_budget.machines[{index}]",
            f"machine {machine!r} has no budget approval",
        )
        for index, machine in enumerate(machines)
        if machine not in {name for _, name in budgets}
    ]
    problems += [
        (f"approvals[{index}].target", f"tests.{target} is not exempt ({{none: true}})")
        for index, target in exemptions
        if target in EXEMPTION_TARGETS and target not in exempt
    ]
    problems += [
        (
            f"approvals[{index}].target",
            f"{target!r} is not a tests.fast_tier_budget.machines[].id",
        )
        for index, target in budgets
        if target not in machines
    ]
    return problems


def _cross_problems(config: Mapping[str, Any], origin: str | None) -> Problems:
    problems: Problems = []
    slug = _section(config, "repo").get("slug")
    if origin is not None and isinstance(slug, str) and slug != origin:
        problems.append((
            "repo.slug",
            f"is {slug!r} but the origin remote names {origin!r}",
        ))
    ci, commands, gates = (
        _section(config, name) for name in ("ci", "commands", "gates")
    )
    if (
        ci.get("enabled") is True
        and ci.get("runner") == "ci-gate"
        and "ci" not in commands
    ):
        problems.append(("commands.ci", "is missing: the ci-gate runner runs it"))
    audit = gates.get("audit")
    if (
        isinstance(audit, bool)
        and "commands" in config
        and audit != ("audit" in commands)
    ):
        problems.append((
            "gates.audit",
            "must be true exactly when commands.audit is present",
        ))
    return problems + _approval_problems(config)


def _rendered(problems: Problems) -> list[str]:
    return [f"{path}: {message}" for path, message in problems]


def validate(config: object, *, origin: str | None) -> list[str]:
    if not isinstance(config, dict):
        return ["config: must be a JSON object"]
    problems: Problems = [
        (extra, "is not a config section; remove it or fix its spelling")
        for extra in sorted(set(config) - set(SECTIONS))
    ]
    for name in SECTIONS:
        if name in config:
            SECTION_VALIDATORS[name](config[name], name, problems)
        elif name not in OPTIONAL_SECTIONS:
            problems.append((name, "is missing"))
    problems += _cross_problems(config, origin)
    return _rendered(problems)


def validate_section(name: str, value: object) -> list[str]:
    if name not in CHECKED_SECTIONS:
        raise ValueError(f"{name!r} is not a section validate_section checks")
    problems: Problems = []
    SECTION_VALIDATORS[name](value, name, problems)
    return _rendered(problems)
