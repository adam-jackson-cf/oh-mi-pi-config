#!/usr/bin/env python3
"""One-off agent-rails cutover: repo records into config.json, the decision store to schema v2.

The owner runs it once, in the live cutover after the agent/agent-rails merge, and a follow-up PR
deletes it with its tests. Nothing else runs it, and no test touches the live records or store.

records: convert the ~/.omp/repos records into <main checkout>/.agentrails/config.json.

    python3 scripts/cutover_agent_rails.py records --records-root ~/.omp/repos \
        --repo owner/name=/abs/clone [--repo ...] [--delete owner/name ...] \
        --approved-by LOGIN --approved-at ISO [--archive DIR --apply]

Only the slugs passed with --repo are converted; --delete names the records the owner retires
without migration. Without --apply the command is a dry run: it prints each config, the fields
the conversion drops and the records it would delete, and writes nothing. Every migrated
exception and approval is stamped with --approved-by and --approved-at, which the owner supplies;
the original text stays in its reason. Any problem in any repo aborts the run before a write.

With --apply it archives the records root (less the deleted records) as a tarball in --archive,
writes each config.json (mode 0644, never over an existing file), adds /.agentrails/ to the
clone's <git common dir>/info/exclude once, and then deletes the --delete records. It never
deletes the records root; a later cutover step removes it after verification.

PATH_REWRITES moves the paths a record names to where the agent/agent-rails branch put them.
Steps that rename or delete tracked paths named by a migrated record extend it. Before --apply,
the owner proves a rewritten config with guardrails check --repo on the merged tree.

store: migrate one decision store from schema version 1 to 2 (rail_events.mode dropped).

    python3 scripts/cutover_agent_rails.py store --store <agent dir>/dynaflows/dynaflows.db \
        [--backup DIR --apply]

Both store readers refuse a version 1 store once the branch lands, so every device whose agent
dir holds a store (the Mac, the NUC and any other device that ran omp with the rails) runs this
once after it pulls, with its omp sessions stopped. The command refuses while lsof lists any
process holding the store, its -wal or its -shm. Without --apply it is a dry run: it copies the
store files to a temporary directory, migrates the copy, prints the row counts it would keep and
leaves the store untouched. With --apply it also refuses while an omp session runs; it then runs
PRAGMA wal_checkpoint(TRUNCATE), copies the store files into a new directory under --backup, and
migrates the store in place in one transaction: rail_events is rebuilt from decision-store.sql
without mode, every row keeps its other columns, and user_version becomes 2. Row counts of every
table must be equal before and after, and PRAGMA integrity_check must pass, or nothing changes.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# The engine's config authority, located from this file as ci_gate.py locates the extension.
sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[1]
        / "agent"
        / "extensions"
        / "dynaflows"
        / "engine"
    ),
)
from guardrails.context.config import repo_config
from guardrails.context.config.repo_config import ConfigError
from guardrails.foundation import decision_store
from guardrails.host.process import run_text

GIT_TIMEOUT_S = 20
GATE_FILE = "gate.json"
PROFILE_FILE = "profile.md"
GATE_SCHEMA = 2
GATE_KEYS = frozenset({
    "schemaVersion",
    "checks",
    "ci",
    "agentMerge",
    "afterMerge",
    "mergePolicies",
})
AUTONOMY = {"allowed": "allowed", "disabled": "ask"}
PROFILE_KEYS = frozenset({
    *repo_config.TEST_KEYS,
    "ownership_areas",
    "structure",
    "house_style",
    "guardrails",
})
RETIRED_STEP = "profile-check"
EXCLUDE_LINE = "/.agentrails/"
CONFIG_MODE = 0o644


class CutoverError(Exception):
    pass


@dataclass(frozen=True)
class Rewrite:
    """Path renames (old prefix, new prefix) and the paths the branch deleted (full matches)."""

    renames: tuple[tuple[str, str], ...]
    deleted: tuple[str, ...]

    def _pattern(self) -> str:
        return "(?:" + "|".join(self.deleted) + ")"

    def is_deleted(self, text: str) -> bool:
        return re.fullmatch(self._pattern(), text.removeprefix("./")) is not None

    def deleted_in(self, text: str) -> str | None:
        found = re.search(rf"(?<![\w.-])(?:\./)?{self._pattern()}(?![\w.-])", text)
        return found[0] if found else None

    def rename(self, text: str) -> str:
        for old, new in self.renames:
            text = text.replace(old, new)
        return text


AGENT_RAILS_REWRITE = Rewrite(
    renames=(
        ("agent/skills/jev-actions", "agent/extensions/agent-rails/skills/jev-actions"),
        ("agent/extensions/dynaflows", "agent/extensions/agent-rails"),
        ("tests/dynaflows-", "tests/agent-rails-"),
        ("rules/dynaflows-", "rules/agent-rails-"),
    ),
    deleted=(
        r"agent/extensions/dynaflows/lib/repo-record\.ts",
        r"agent/extensions/dynaflows/lib/repo_record\.py",
        r"repo_record",
        r"tests/repo-record(?:-seams)?\.test\.ts",
        r"tests/test_repo_record(?:_seams)?\.py",
        r"agent/extensions/dynaflows/record(?:/\S*)?",
        r"agent/extensions/dynaflows/skills/structured-workflow(?:/\S*)?",
        r"agent/extensions/dynaflows/skills/codify-omp-workflow-corrections(?:/\S*)?",
        r"scripts/migrate_judgment_calls\.py",
        r"tests/test_migrate_judgment_calls\.py",
    ),
)
PATH_REWRITES: dict[str, Rewrite] = {
    "adam-jackson-cf/oh-mi-pi-config": AGENT_RAILS_REWRITE,
}
REWRITTEN_SECTIONS = ("commands", "afterMerge", "checkers", "structure", "tests")


@dataclass(frozen=True)
class Target:
    slug: str
    clone: Path


@dataclass(frozen=True)
class Clone:
    main: Path
    origin: str | None
    default_branch: str

    @property
    def exclude(self) -> Path:
        return self.main / ".git" / "info" / "exclude"

    @property
    def config(self) -> Path:
        return self.main / repo_config.CONFIG_DIR / repo_config.CONFIG_FILE


@dataclass(frozen=True)
class Attestation:
    by: str | None
    at: str | None


@dataclass
class Draft:
    """One record's conversion: owner-attested entries are kept apart until they are stamped."""

    sections: dict[str, Any] = field(default_factory=dict)
    exceptions: list[dict[str, Any]] = field(default_factory=list)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


@dataclass
class Migration:
    target: Target
    clone: Clone
    config: dict[str, Any]
    dropped: list[str]


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for key, value in pairs:
        if key in found:
            raise ValueError(f"duplicate key {key!r}")
        found[key] = value
    return found


def _json(text: str, where: str) -> dict[str, Any]:
    try:
        value = json.loads(text, object_pairs_hook=_unique_keys)
    except ValueError as error:
        raise CutoverError(f"{where} is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise CutoverError(f"{where} must hold one JSON object")
    return value


def read_front_matter(path: Path) -> tuple[dict[str, Any], str]:
    """The JSON front matter between the opening and closing '---' lines, and the prose after it."""
    lines = path.read_text(encoding="utf-8").split("\n")
    if not lines or lines[0].strip() != "---":
        raise CutoverError(f"{path} must start with a '---' front-matter line")
    closing = next(
        (index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"),
        None,
    )
    if closing is None:
        raise CutoverError(f"{path} front matter has no closing '---' line")
    front = _json("\n".join(lines[1:closing]), f"{path} front matter")
    return front, "\n".join(lines[closing + 1 :]).strip()


def read_gate(path: Path) -> dict[str, Any]:
    return _json(path.read_text(encoding="utf-8"), str(path))


def parse_target(text: str) -> Target:
    slug, separator, clone = text.partition("=")
    slug = slug.strip().lower()
    if not separator or not repo_config.SLUG.fullmatch(slug):
        raise argparse.ArgumentTypeError(f"{text!r} is not owner/name=/abs/clone/path")
    if not Path(clone).is_absolute():
        raise argparse.ArgumentTypeError(f"{text!r}: the clone path must be absolute")
    return Target(slug, Path(clone))


def parse_slug(text: str) -> str:
    slug = text.strip().lower()
    if not repo_config.SLUG.fullmatch(slug):
        raise argparse.ArgumentTypeError(f"{text!r} is not a GitHub owner/name")
    return slug


def _git(clone: Path, *args: str) -> str:
    result = run_text(["git", "-C", str(clone), *args], timeout=GIT_TIMEOUT_S)
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit {result.returncode}"
        raise CutoverError(f"git {' '.join(args)} in {clone} failed: {detail}")
    return result.stdout.strip()


def inspect_clone(clone: Path) -> Clone:
    """The clone's main checkout, lowercased origin slug and default branch, read from git."""
    try:
        main = repo_config.main_checkout(clone)
        origin = repo_config.origin_slug(clone)
    except ConfigError as error:
        raise CutoverError("; ".join(error.problems)) from error
    try:
        head = _git(clone, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    except CutoverError as error:
        raise CutoverError(
            f"{error}; set it with 'git -C {clone} remote set-head origin --auto'"
        ) from error
    return Clone(main, origin, head.removeprefix("origin/"))


def _gate_sections(gate: dict[str, Any], draft: Draft) -> None:
    for key in sorted(set(gate) - GATE_KEYS):
        draft.problems.append(f"gate.json {key} has no config mapping")
    if gate.get("schemaVersion") != GATE_SCHEMA:
        draft.problems.append(f"gate.json schemaVersion must be {GATE_SCHEMA}")
    draft.dropped.append(
        f"gate.json schemaVersion {GATE_SCHEMA} (the config carries schemaVersion 1)"
    )
    merge = gate.get("agentMerge", "allowed")
    permission = AUTONOMY.get(merge) if isinstance(merge, str) else None
    if permission is None:
        draft.problems.append(
            f"gate.json agentMerge {merge!r} is not allowed or disabled"
        )
    draft.sections["autonomy"] = dict.fromkeys(
        repo_config.AUTONOMY_ACTIONS, permission or "ask"
    )
    ci = gate.get("ci")
    draft.sections["ci"] = (
        {"enabled": True, **ci} if isinstance(ci, dict) else {"enabled": False}
    )
    checks = gate.get("checks")
    commands = dict(checks) if isinstance(checks, dict) else {}
    if commands.pop("profile", None) is not None:
        draft.dropped.append("gate.json checks.profile (profile.md no longer exists)")
    draft.sections["gates"] = {
        "commitChecks": True,
        "pathCheck": True,
        "prove": True,
        "structureReview": True,
        "audit": "audit" in commands,
        "rules": {},
    }
    draft.sections["judgments"] = {}
    draft.sections["mergePolicies"] = gate.get("mergePolicies", [])
    if "afterMerge" in gate:
        draft.sections["afterMerge"] = gate["afterMerge"]
    draft.sections["commands"] = commands


def _approval(kind: str, target: object, *texts: object) -> dict[str, Any]:
    reasons = [text for text in texts if isinstance(text, str) and text.strip()]
    if len(reasons) == 2:
        return {
            "kind": kind,
            "target": target,
            "reason": f"{reasons[0]} ({reasons[1]})",
        }
    return {"kind": kind, "target": target, "reason": reasons[0] if reasons else ""}


def _exemptions(tests: dict[str, Any], draft: Draft) -> None:
    for target in repo_config.EXEMPTION_TARGETS:
        parent, _, leaf = target.rpartition(".")
        holder = tests.get(parent) if parent else tests
        value = holder.get(leaf) if isinstance(holder, dict) else None
        if not (
            isinstance(holder, dict) and isinstance(value, dict) and "none" in value
        ):
            continue
        extra = sorted(set(value) - {"none", "approval"})
        if extra or not all(
            isinstance(value.get(key), str) and value[key].strip()
            for key in ("none", "approval")
        ):
            draft.problems.append(
                f"profile.md {target} must be {{none: <reason>, approval: <reference>}}"
            )
            continue
        draft.approvals.append(
            _approval("exemption", target, value["none"], value["approval"])
        )
        holder[leaf] = {"none": True}


def _budgets(tests: dict[str, Any], draft: Draft) -> None:
    budget = tests.get("fast_tier_budget")
    machines = budget.get("machines") if isinstance(budget, dict) else None
    for machine in machines if isinstance(machines, list) else []:
        if isinstance(machine, dict) and "approval" in machine:
            draft.approvals.append(
                _approval("budget", machine.get("id"), machine.pop("approval"))
            )


def _moved(
    settings: dict[str, Any],
    key: str,
    draft: Draft,
    entry: Callable[[Any], dict[str, Any]],
) -> None:
    entries = settings.pop(key, [])
    if not isinstance(entries, list):
        draft.problems.append(f"profile.md guardrails {key} must be a list")
        return
    draft.approvals.extend(entry(item) for item in entries)


def _ratchet(item: Any) -> dict[str, Any]:
    item = item if isinstance(item, dict) else {}
    return _approval("ratchet", item.get("path"), item.get("approval"))


def _skip(item: Any) -> dict[str, Any]:
    item = item if isinstance(item, dict) else {}
    return _approval("skip", item.get("id"), item.get("reason"), item.get("approval"))


def _checkers(guardrails: dict[str, Any], draft: Draft) -> None:
    exceptions = guardrails.pop("exceptions", [])
    if not isinstance(exceptions, list):
        draft.problems.append("profile.md guardrails.exceptions must be a list")
        exceptions = []
    draft.exceptions.extend(exceptions)
    ratchet, evidence = (
        guardrails.get(name) for name in ("coverage_ratchet", "test_evidence")
    )
    if isinstance(ratchet, dict):
        _moved(ratchet, "approvals", draft, _ratchet)
    if isinstance(evidence, dict):
        _moved(evidence, "approved_skips", draft, _skip)
    check = guardrails.get("check")
    run = check.get("run") if isinstance(check, dict) else None
    if isinstance(check, dict) and isinstance(run, list) and RETIRED_STEP in run:
        draft.dropped.append(f"guardrails.check.run step {RETIRED_STEP!r} (retired)")
        kept = [step for step in run if step != RETIRED_STEP]
        if kept:
            check["run"] = kept
        else:
            del check["run"]
    draft.sections["checkers"] = guardrails


def _profile_sections(profile: dict[str, Any], draft: Draft) -> None:
    for key in sorted(set(profile) - PROFILE_KEYS):
        draft.problems.append(f"profile.md {key} has no config mapping")
    tests = {key: profile[key] for key in repo_config.TEST_KEYS if key in profile}
    _exemptions(tests, draft)
    _budgets(tests, draft)
    draft.sections["tests"] = tests
    structure = profile.get("structure")
    if isinstance(structure, dict) and "ownership_areas" in profile:
        structure = {**structure, "ownership_areas": profile["ownership_areas"]}
    if structure is not None:
        draft.sections["structure"] = structure
    if "house_style" in profile:
        draft.sections["houseStyle"] = profile["house_style"]
    guardrails = profile.get("guardrails", {})
    if isinstance(guardrails, dict):
        _checkers(guardrails, draft)
    else:
        draft.problems.append("profile.md guardrails must be an object")


def convert(gate: dict[str, Any], profile: dict[str, Any]) -> Draft:
    """The record's config sections per the field mapping, before repo, onboarding and stamps."""
    draft = Draft()
    _gate_sections(copy.deepcopy(gate), draft)
    _profile_sections(copy.deepcopy(profile), draft)
    return draft


def _rewrite(value: Any, rewrite: Rewrite, where: str, dropped: list[str]) -> Any:
    if isinstance(value, dict):
        kept: dict[str, Any] = {}
        for key, item in value.items():
            if rewrite.is_deleted(key):
                dropped.append(f"{where}.{key} (the agent-rails branch deletes it)")
            else:
                kept[rewrite.rename(key)] = _rewrite(
                    item, rewrite, f"{where}.{key}", dropped
                )
        return kept
    if isinstance(value, list):
        items = []
        for index, item in enumerate(value):
            if isinstance(item, str) and rewrite.is_deleted(item):
                dropped.append(
                    f"{where}[{index}] {item} (the agent-rails branch deletes it)"
                )
            else:
                items.append(_rewrite(item, rewrite, f"{where}[{index}]", dropped))
        return items
    if isinstance(value, str):
        words = value.split(" ")
        kept_words = [word for word in words if not rewrite.is_deleted(word)]
        if len(words) > 1 and len(kept_words) < len(words):
            gone = [word for word in words if rewrite.is_deleted(word)]
            dropped.append(
                f"{where} words {gone} (the agent-rails branch deletes them)"
            )
            value = " ".join(kept_words)
        return rewrite.rename(value)
    return value


def _stale(value: Any, rewrite: Rewrite, where: str, problems: list[str]) -> None:
    texts: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        texts = [(key, item) for key, item in value.items()]
    elif isinstance(value, list):
        texts = [(f"[{index}]", item) for index, item in enumerate(value)]
    for key, item in texts:
        path = f"{where}{key}" if key.startswith("[") else f"{where}.{key}"
        name = None if key.startswith("[") else rewrite.deleted_in(key)
        if name is None and isinstance(item, str):
            name = rewrite.deleted_in(item)
        if name is not None:
            problems.append(
                f"{path} still names {name}, which the agent-rails branch deletes; "
                "fix the record"
            )
        _stale(item, rewrite, path, problems)


def apply_rewrite(draft: Draft, rewrite: Rewrite) -> None:
    for name in REWRITTEN_SECTIONS:
        if name in draft.sections:
            draft.sections[name] = _rewrite(
                draft.sections[name], rewrite, name, draft.dropped
            )
            _stale(draft.sections[name], rewrite, name, draft.problems)
    for entry in [*draft.exceptions, *draft.approvals]:
        for key in ("path", "target"):
            if isinstance(entry, dict) and isinstance(entry.get(key), str):
                entry[key] = rewrite.rename(entry[key])


def _stamped(entries: list[dict[str, Any]], attestation: Attestation) -> list[Any]:
    return [
        {**entry, "approvedBy": attestation.by, "approvedAt": attestation.at}
        if isinstance(entry, dict)
        else entry
        for entry in entries
    ]


def assemble(
    target: Target, clone: Clone, draft: Draft, attestation: Attestation, now: str
) -> dict[str, Any]:
    sections = {
        "schemaVersion": repo_config.SCHEMA_VERSION,
        "repo": {"slug": target.slug, "defaultBranch": clone.default_branch},
        "onboarding": {
            "origin": "migration",
            "completedAt": now,
            "questionnaireVersion": repo_config.QUESTIONNAIRE_VERSION,
            "migratedFrom": f"~/.omp/repos/{target.slug}",
            "skills": {},
        },
        **draft.sections,
        "exceptions": _stamped(draft.exceptions, attestation),
        "approvals": _stamped(draft.approvals, attestation),
    }
    return {name: sections[name] for name in repo_config.SECTIONS if name in sections}


@dataclass
class Plan:
    migrations: list[Migration]
    problems: list[str]
    unattested: list[str]


def _record_files(record: Path) -> list[str]:
    return sorted(
        str(path.relative_to(record))
        for path in record.rglob("*")
        if path.is_file() and path.name not in {GATE_FILE, PROFILE_FILE}
    )


def _draft(record: Path, slug: str) -> Draft:
    gate = read_gate(record / GATE_FILE)
    profile, prose = read_front_matter(record / PROFILE_FILE)
    draft = convert(gate, profile)
    if prose:
        draft.dropped.append(
            f"profile.md prose after the front matter ({len(prose)} characters; archived)"
        )
    draft.dropped += [f"{name} (archived)" for name in _record_files(record)]
    rewrite = PATH_REWRITES.get(slug)
    if rewrite is not None:
        apply_rewrite(draft, rewrite)
    return draft


def _clone_problems(target: Target, clone: Clone) -> list[str]:
    problems = []
    if clone.origin != target.slug:
        problems.append(
            f"the clone's origin remote names {clone.origin!r}, not the record slug"
        )
    if clone.config.exists():
        problems.append(
            f"{clone.config} already exists; the cutover never overwrites it"
        )
    return problems


def plan(
    root: Path, targets: Sequence[Target], attestation: Attestation, now: str
) -> Plan:
    result = Plan([], [], [])
    for target in targets:
        record = root / target.slug
        try:
            draft = _draft(record, target.slug)
            clone = inspect_clone(target.clone)
        except (CutoverError, OSError) as error:
            result.problems.append(f"{target.slug}: {error}")
            continue
        owned = len(draft.exceptions) + len(draft.approvals)
        if owned and not (attestation.by and attestation.at):
            result.unattested.append(
                f"{target.slug}: {len(draft.exceptions)} exceptions and "
                f"{len(draft.approvals)} approvals need --approved-by and --approved-at"
            )
        config = assemble(target, clone, draft, attestation, now)
        found = draft.problems + _clone_problems(target, clone)
        if not result.unattested:
            found += repo_config.validate(config, origin=target.slug)
        result.problems += [f"{target.slug}: {problem}" for problem in found]
        result.migrations.append(Migration(target, clone, config, draft.dropped))
    return result


def archive_records(
    root: Path, archive: Path, deleted: Sequence[str], now: str
) -> Path:
    """Tar the records root, less the deleted records, and check every file reached the tarball."""
    archive.mkdir(parents=True, exist_ok=True)
    tarball = archive / f"repos-{now.replace(':', '')}.tar.gz"
    skipped = {root / slug for slug in deleted}
    files = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and not any(path.is_relative_to(gone) for gone in skipped)
    ]
    with tarball.open("xb") as handle, tarfile.open(fileobj=handle, mode="w:gz") as tar:
        for path in files:
            tar.add(path, arcname=str(Path(root.name) / path.relative_to(root)))
    with tarfile.open(tarball) as tar:
        archived = {member.name for member in tar.getmembers() if member.isfile()}
    missing = [
        str(path)
        for path in files
        if str(Path(root.name) / path.relative_to(root)) not in archived
    ]
    if missing:
        raise CutoverError(f"{tarball} is missing {missing}")
    return tarball


def write_config(path: Path, config: dict[str, Any]) -> None:
    """Write through a temp file and a hard link, so an existing config is never replaced."""
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=".config-", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
        os.chmod(temporary, CONFIG_MODE)
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise CutoverError(f"{path} already exists; nothing overwritten") from error
    finally:
        Path(temporary).unlink()


def ensure_excluded(exclude: Path) -> bool:
    """Add the config directory to the git exclude file once; True when the line was added."""
    text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if EXCLUDE_LINE in (line.strip() for line in text.splitlines()):
        return False
    exclude.parent.mkdir(parents=True, exist_ok=True)
    separator = "" if not text or text.endswith("\n") else "\n"
    exclude.write_text(f"{text}{separator}{EXCLUDE_LINE}\n", encoding="utf-8")
    return True


def delete_record(root: Path, slug: str) -> None:
    record = root / slug
    shutil.rmtree(record)
    if not any(record.parent.iterdir()):
        record.parent.rmdir()


def _print_plan(found: Plan, root: Path, deleted: Sequence[str]) -> None:
    for migration in found.migrations:
        print(f"== {migration.target.slug} -> {migration.clone.config}")
        print(json.dumps(migration.config, indent=2, ensure_ascii=False))
        print("dropped:")
        for line in migration.dropped:
            print(f"  - {line}")
        if migration.target.slug in PATH_REWRITES:
            print(
                "paths rewritten for the agent-rails branch: prove the config with "
                f"'guardrails check --repo {migration.target.clone}' on the merged tree "
                "before --apply"
            )
    for slug in deleted:
        print(f"== {slug}: would delete {root / slug} (not archived, not migrated)")
    print("dry run: nothing written")


def _apply(
    found: Plan, root: Path, archive: Path, deleted: Sequence[str], now: str
) -> None:
    tarball = archive_records(root, archive, deleted, now)
    print(f"archived {root} (less {len(deleted)} deleted records) to {tarball}")
    for migration in found.migrations:
        write_config(migration.clone.config, migration.config)
        added = ensure_excluded(migration.clone.exclude)
        config = migration.config
        print(
            f"{migration.target.slug}: wrote {migration.clone.config} "
            f"({len(config['exceptions'])} exceptions, {len(config['approvals'])} approvals); "
            f"{migration.clone.exclude} {'gained' if added else 'already had'} {EXCLUDE_LINE}"
        )
    for slug in deleted:
        delete_record(root, slug)
        print(f"{slug}: deleted {root / slug}")


def _attestation_problem(attestation: Attestation) -> str | None:
    if attestation.by is not None and not attestation.by.strip():
        return "--approved-by must be the owner's GitHub login"
    if attestation.at is not None and not repo_config.ISO_TIME.fullmatch(
        attestation.at
    ):
        return "--approved-at must be an ISO 8601 date-time with an offset, e.g. 2026-11-24T09:00:00Z"
    return None


def _selection_problems(
    root: Path, targets: Sequence[Target], deleted: Sequence[str]
) -> list[str]:
    slugs = [target.slug for target in targets]
    problems = [
        f"{slug} is passed more than once"
        for slug in sorted({
            slug for slug in [*slugs, *deleted] if [*slugs, *deleted].count(slug) > 1
        })
    ]
    problems += [
        f"{slug}: no record at {root / slug}"
        for slug in [*slugs, *deleted]
        if not (root / slug).is_dir()
    ]
    return problems


def records(args: argparse.Namespace) -> int:
    root: Path = args.records_root.expanduser()
    attestation = Attestation(args.approved_by, args.approved_at)
    usage = _attestation_problem(attestation)
    if args.apply and args.archive is None:
        usage = "--apply needs --archive DIR"
    if usage:
        print(f"cutover refused: {usage}", file=sys.stderr)
        return 2
    selection = _selection_problems(root, args.repo, args.delete)
    if selection:
        print(
            "cutover refused; nothing written:", *selection, sep="\n  ", file=sys.stderr
        )
        return 1
    now = utc_now()
    found = plan(root, args.repo, attestation, now)
    if found.unattested:
        print(
            "cutover refused: owner attestation missing; nothing written:",
            *found.unattested,
            sep="\n  ",
            file=sys.stderr,
        )
        return 2
    if found.problems:
        print(
            "cutover refused; nothing written:",
            *found.problems,
            sep="\n  ",
            file=sys.stderr,
        )
        return 1
    if not args.apply:
        _print_plan(found, root, args.delete)
        return 0
    _apply(found, root, args.archive.expanduser(), args.delete, now)
    return 0


# The decision store migration (the store subcommand).
STORE_FROM_VERSION = 1
STORE_TABLES = ("questions", "decisions", "payloads", "reviews", "rail_events")
STORE_SIDECARS = ("-wal", "-shm")
DROPPED_COLUMN = "mode"
PROCESS_TIMEOUT_S = 30
OMP_WORKER = "__omp_worker_"


@dataclass(frozen=True)
class StoreReport:
    before: dict[str, int]
    after: dict[str, int]
    integrity: str


def store_files(store: Path) -> list[Path]:
    """The store and whichever of its -wal and -shm files exist."""
    sidecars = [store.with_name(store.name + suffix) for suffix in STORE_SIDECARS]
    return [store, *(path for path in sidecars if path.exists())]


def holders(store: Path) -> list[str]:
    """The PIDs lsof lists as holding the store, its -wal or its -shm open."""
    try:
        result = run_text(
            ["lsof", "-t", "--", *map(str, store_files(store))],
            timeout=PROCESS_TIMEOUT_S,
        )
    except FileNotFoundError as error:
        raise CutoverError(
            "lsof is not installed; the store cutover cannot prove no process holds the store"
        ) from error
    if result.returncode not in (0, 1):
        raise CutoverError(
            f"lsof failed: {result.stderr.strip() or f'exit {result.returncode}'}"
        )
    return sorted({line.strip() for line in result.stdout.splitlines() if line.strip()})


def omp_sessions() -> list[str]:
    """Running omp processes other than omp's own worker daemons, as 'PID ARGS'."""
    try:
        result = run_text(
            ["ps", "-A", "-o", "pid=", "-o", "args="], timeout=PROCESS_TIMEOUT_S
        )
    except FileNotFoundError as error:
        raise CutoverError(
            "ps is not installed; the store cutover cannot prove no omp session runs"
        ) from error
    if result.returncode != 0:
        raise CutoverError(
            f"ps failed: {result.stderr.strip() or f'exit {result.returncode}'}"
        )
    sessions = []
    for line in result.stdout.splitlines():
        pid, _, args = line.strip().partition(" ")
        args = args.strip()
        if Path(args.split(" ", 1)[0]).name == "omp" and OMP_WORKER not in args:
            sessions.append(f"{pid} {args}")
    return sessions


def _rail_events_schema() -> tuple[str, list[str]]:
    """The v2 rail_events CREATE TABLE and CREATE INDEX statements, from decision-store.sql."""
    text = decision_store.schema_text()
    table = re.search(
        r"^CREATE TABLE IF NOT EXISTS rail_events \(.*?^\);$",
        text,
        re.MULTILINE | re.DOTALL,
    )
    indexes = re.findall(
        r"^CREATE INDEX IF NOT EXISTS rail_events_\w+ ON rail_events \([^)]*\);$",
        text,
        re.MULTILINE,
    )
    if table is None or not indexes:
        raise CutoverError(
            f"{decision_store.SCHEMA_FILE} has no rail_events table or index"
        )
    return table[0], indexes


def _version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def _require_from_version(connection: sqlite3.Connection, store: Path) -> None:
    version = _version(connection)
    if version != STORE_FROM_VERSION:
        raise CutoverError(
            f"{store} is at schema version {version}; this migration reads version "
            f"{STORE_FROM_VERSION} only"
        )


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]


def table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in STORE_TABLES
    }


def _schema_rows(
    connection: sqlite3.Connection, rail_events: bool
) -> list[tuple[str, str, str]]:
    """sqlite_master (type, name, sql) rows of rail_events and its indexes, or of everything else."""
    rows = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    return [
        (kind, name, sql)
        for kind, name, table, sql in rows
        if (table == "rail_events") == rail_events
    ]


def _open_store(store: Path) -> sqlite3.Connection:
    try:
        return sqlite3.connect(
            store, timeout=decision_store.BUSY_TIMEOUT_SECONDS, isolation_level=None
        )
    except sqlite3.Error as error:
        raise CutoverError(f"cannot open {store}: {error}") from error


def checkpoint(store: Path) -> None:
    """Require schema version 1, then fold the WAL into the store file and truncate it."""
    connection = _open_store(store)
    try:
        _require_from_version(connection, store)
        busy, _, _ = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy:
            raise CutoverError(
                f"PRAGMA wal_checkpoint(TRUNCATE) on {store} was blocked by another connection"
            )
    except sqlite3.Error as error:
        raise CutoverError(f"cannot checkpoint {store}: {error}") from error
    finally:
        connection.close()


def _rebuild_rail_events(connection: sqlite3.Connection, store: Path) -> None:
    table, indexes = _rail_events_schema()
    old = _columns(connection, "rail_events")
    connection.execute("ALTER TABLE rail_events RENAME TO rail_events_v1")
    connection.execute(table)
    kept = _columns(connection, "rail_events")
    if [name for name in old if name != DROPPED_COLUMN] != kept:
        raise CutoverError(
            f"{store} rail_events has columns {old}; the migration expects {kept} plus "
            f"{DROPPED_COLUMN!r}"
        )
    names = ", ".join(kept)
    connection.execute(
        f"INSERT INTO rail_events ({names}) SELECT {names} FROM rail_events_v1"
    )
    connection.execute("DROP TABLE rail_events_v1")
    for index in indexes:
        connection.execute(index)
    connection.execute(f"PRAGMA user_version = {decision_store.schema_version()}")


def _expected_rail_events() -> list[tuple[str, str, str]]:
    fresh = sqlite3.connect(":memory:", isolation_level=None)
    try:
        fresh.executescript(decision_store.schema_text())
        return _schema_rows(fresh, rail_events=True)
    finally:
        fresh.close()


def migrate_store(store: Path) -> StoreReport:
    """Migrate the store from schema version 1 to 2 in one transaction; any failed check rolls it back."""
    connection = _open_store(store)
    try:
        _require_from_version(connection, store)
        connection.execute("BEGIN IMMEDIATE")
        try:
            before = table_counts(connection)
            others = _schema_rows(connection, rail_events=False)
            _rebuild_rail_events(connection, store)
            after = table_counts(connection)
            integrity = "; ".join(
                str(row[0]) for row in connection.execute("PRAGMA integrity_check")
            )
            problems = []
            if after != before:
                problems.append(f"row counts changed: {before} before, {after} after")
            if integrity != "ok":
                problems.append(f"PRAGMA integrity_check: {integrity}")
            if _schema_rows(connection, rail_events=False) != others:
                problems.append("tables other than rail_events changed")
            if _schema_rows(connection, rail_events=True) != _expected_rail_events():
                problems.append(
                    f"rail_events differs from {decision_store.SCHEMA_FILE}"
                )
            if problems:
                raise CutoverError(
                    f"{store} migration rolled back: " + "; ".join(problems)
                )
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error as error:
        raise CutoverError(f"cannot migrate {store}: {error}") from error
    finally:
        connection.close()
    return StoreReport(before, after, integrity)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def backup_store(store: Path, backup: Path, now: str) -> Path:
    """Copy the store files into a new directory under backup and check each copy's digest."""
    target = backup / f"decision-store-{now.replace(':', '')}"
    target.mkdir(parents=True, exist_ok=False)
    for path in store_files(store):
        copied = Path(shutil.copy2(path, target / path.name))
        if _digest(copied) != _digest(path):
            raise CutoverError(f"the backup {copied} differs from {path}")
    return target


def _print_store_report(report: StoreReport, heading: str) -> None:
    print(heading)
    width = max(len(table) for table in STORE_TABLES)
    print(f"  {'table'.ljust(width)}  before  after")
    for table in STORE_TABLES:
        print(
            f"  {table.ljust(width)}  {report.before[table]:>6}  {report.after[table]:>5}"
        )
    print(f"  integrity_check: {report.integrity}")


def _store_refusals(store: Path, apply: bool) -> list[str]:
    if not store.is_file():
        return [f"no decision store at {store}"]
    problems = []
    held = holders(store)
    if held:
        problems.append(
            f"processes {', '.join(held)} hold {store} open; stop them and retry"
        )
    if apply:
        problems += [f"omp session running: {session}" for session in omp_sessions()]
    return problems


def store(args: argparse.Namespace) -> int:
    path: Path = args.store.expanduser()
    if args.apply and args.backup is None:
        print("cutover refused: --apply needs --backup DIR", file=sys.stderr)
        return 2
    try:
        refusals = _store_refusals(path, args.apply)
        if refusals:
            print(
                "cutover refused; nothing written:",
                *refusals,
                sep="\n  ",
                file=sys.stderr,
            )
            return 1
        if not args.apply:
            with tempfile.TemporaryDirectory(prefix="agent-rails-store-") as scratch:
                copies = [
                    Path(shutil.copy2(file, Path(scratch) / file.name))
                    for file in store_files(path)
                ]
                checkpoint(copies[0])
                report = migrate_store(copies[0])
            _print_store_report(
                report, f"dry run on a copy of {path}: schema version 1 -> 2 planned"
            )
            print(f"dry run: {path} untouched; nothing written")
            return 0
        now = utc_now()
        checkpoint(path)
        saved = backup_store(path, args.backup.expanduser(), now)
        print(f"backed up {path} to {saved}")
        report = migrate_store(path)
    except (CutoverError, OSError) as error:
        print(f"cutover refused: {error}", file=sys.stderr)
        return 1
    _print_store_report(report, f"{path}: migrated to schema version 2")
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = top.add_subparsers(dest="command", required=True)
    convert_records = commands.add_parser(
        "records", help="convert the named records into .agentrails/config.json"
    )
    convert_records.add_argument(
        "--records-root",
        type=Path,
        default=Path("~/.omp/repos"),
        help="the records root",
    )
    convert_records.add_argument(
        "--repo",
        type=parse_target,
        action="append",
        default=[],
        required=True,
        help="owner/name=/abs/clone/path; only these records are converted",
    )
    convert_records.add_argument(
        "--delete",
        type=parse_slug,
        action="append",
        default=[],
        help="owner/name of a record to delete with --apply, without archive or migration",
    )
    convert_records.add_argument("--approved-by", help="the owner's GitHub login")
    convert_records.add_argument("--approved-at", help="the owner-chosen ISO 8601 time")
    convert_records.add_argument("--archive", type=Path, help="tarball directory")
    convert_records.add_argument(
        "--apply", action="store_true", help="write the configs"
    )
    convert_records.set_defaults(run=records)
    migrate = commands.add_parser(
        "store", help="migrate a decision store from schema version 1 to 2"
    )
    migrate.add_argument(
        "--store", type=Path, required=True, help="the decision store file"
    )
    migrate.add_argument(
        "--backup", type=Path, help="the directory --apply copies the store files into"
    )
    migrate.add_argument(
        "--apply", action="store_true", help="migrate the store in place"
    )
    migrate.set_defaults(run=store)
    return top


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    run: Callable[[argparse.Namespace], int] = args.run
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
