from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from guardrails.context.config import repo_config
from guardrails.context.config.repo_config import ConfigError
from guardrails.foundation.findings import catalogue_rules

DESCRIPTION = """Validate an agent-rails config (<main checkout>/.agentrails/config.json).

Each problem is a CONFIG.complete problem: a value missing, malformed or not owner-approved.

  --repo DIR                     the main checkout's config, checked against DIR's origin
  --file PATH [--slug OWNER/NAME]  any config file; --slug checks repo.slug against it
  --section NAME --file PATH     one section value
  --list-rules                   the code-check rule ids gates.rules may switch off
  --list-questions               the judgment questions the judgments section may override

A check prints one JSON line {ok, kind, path, problems}; it exits 0 when ok and 1 otherwise.
kind is ok, missing, invalid, unresolved or outside. A list prints one JSON line,
{rules: [{id, summary}]} or {questions: [{key, mode, shadowable}]}, and exits 0.
"""


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="guardrails config-check",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    target = root.add_mutually_exclusive_group(required=True)
    target.add_argument("--repo", type=Path, help="a directory in the repository")
    target.add_argument("--file", type=Path, help="a config or section file")
    target.add_argument(
        "--list-rules", action="store_true", help="list the switchable code rules"
    )
    target.add_argument(
        "--list-questions",
        action="store_true",
        help="list the configurable judgment questions",
    )
    root.add_argument("--slug", help="the lowercase GitHub owner/name of the origin")
    root.add_argument("--section", choices=repo_config.CHECKED_SECTIONS)
    return root


def outcome(kind: str, path: Path | None, problems: list[str]) -> dict[str, Any]:
    return {
        "ok": kind == "ok",
        "kind": kind,
        "path": None if path is None else str(path),
        "problems": problems,
    }


def checked_file(path: Path, slug: str | None, section: str | None) -> dict[str, Any]:
    value = repo_config.read_config(path)
    problems = (
        repo_config.validate(value, origin=slug)
        if section is None
        else repo_config.validate_section(section, value)
    )
    return outcome("invalid" if problems else "ok", path, problems)


def listed_rules() -> dict[str, Any]:
    summaries = {rule["id"]: rule.get("summary", "") for rule in catalogue_rules()}
    return {
        "rules": [
            {"id": ident, "summary": summaries[ident]}
            for ident in sorted(repo_config.code_check_rules())
        ]
    }


def listed_questions() -> dict[str, Any]:
    return {
        "questions": [
            {"key": key, "mode": question.mode, "shadowable": question.shadowable}
            for key, question in sorted(repo_config.offered_questions().items())
        ]
    }


def checked(args: argparse.Namespace) -> dict[str, Any]:
    try:
        if args.repo is not None:
            repo_config.load_config(args.repo)
            return outcome("ok", repo_config.config_path(args.repo), [])
        return checked_file(args.file, args.slug, args.section)
    except ConfigError as error:
        return outcome(error.kind, error.path, error.problems)


def main(argv: list[str]) -> int:
    root = parser()
    args = root.parse_args(argv)
    listing = args.list_rules or args.list_questions
    if listing and (args.slug is not None or args.section is not None):
        root.error("--list-rules and --list-questions take no other option")
    if listing:
        print(json.dumps(listed_rules() if args.list_rules else listed_questions()))
        return 0
    if args.repo is not None and (args.slug is not None or args.section is not None):
        root.error("--slug and --section go with --file, not --repo")
    if args.slug is not None and args.section is not None:
        root.error("--slug checks a whole config; drop it with --section")
    if args.slug is not None and not repo_config.SLUG.fullmatch(args.slug):
        root.error(f"--slug {args.slug!r} is not a lowercase owner/name")
    result = checked(args)
    print(json.dumps(result))
    return 0 if result["ok"] else 1
