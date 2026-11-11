"""The dependency line that pins this reference to one commit of its source repository.

Format (plan §1.8)::

    omp-guardrails @ git+<repository url>@<commit>#subdirectory=<package directory>

Init takes the repository, commit and subdirectory from, in order:

1. ``--pin-url``, ``--pin-commit`` and ``--pin-subdirectory``;
2. the installed distribution's PEP 610 ``direct_url.json``, which pip and uv write when the
   package was installed from git (``url``, ``vcs_info.commit_id``, ``subdirectory``).

The subdirectory defaults to this package's home in its source repository. No repository URL
or commit is built into the code; a URL or commit neither source provides is a setup gap,
unless ``pyproject.toml`` already pins the reference.

Init writes the line into ``pyproject.toml`` ``[dependency-groups] dev`` (PEP 735) and leaves
locking to the repo's own lock command. An existing pin is kept and reported, never replaced:
upgrading is a one-line bump in a normal PR.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from omp_guardrails.findings import Finding

DISTRIBUTION = "omp-guardrails"
COMMIT = re.compile(r"^[0-9a-f]{40}$")
RULE = "PARITY.lock-version"
HOME_SUBDIRECTORY = "agent/skills/setup-repository-guardrails/assets/python-reference"


@dataclass(frozen=True)
class PinSource:
    url: str | None = None
    commit: str | None = None
    subdirectory: str | None = None


def installed_source() -> PinSource:
    """Read the PEP 610 record of a git install; empty when installed another way."""
    try:
        text = metadata.distribution(DISTRIBUTION).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return PinSource()
    return parse_direct_url(text)


def parse_direct_url(text: str | None) -> PinSource:
    if not text:
        return PinSource()
    try:
        record = json.loads(text)
    except ValueError:
        return PinSource()
    vcs = record.get("vcs_info") if isinstance(record, dict) else None
    if not isinstance(vcs, dict) or vcs.get("vcs") != "git":
        return PinSource()
    return PinSource(record.get("url"), vcs.get("commit_id"), record.get("subdirectory"))


def resolve(explicit: PinSource, installed: PinSource) -> PinSource:
    return PinSource(
        explicit.url or installed.url,
        explicit.commit or installed.commit,
        explicit.subdirectory or installed.subdirectory or HOME_SUBDIRECTORY,
    )


def pin_line(source: PinSource) -> tuple[str | None, list[Finding]]:
    gaps = []
    if not source.url:
        gaps.append("the reference repository URL is unknown; pass --pin-url")
    if not source.commit or not COMMIT.match(source.commit):
        gaps.append("the reference commit is unknown or not a full SHA; pass --pin-commit")
    if gaps:
        fix = "install the reference from git or pass the --pin-* values"
        return None, [Finding(RULE, "omp-guardrails pin", gap, fix) for gap in gaps]
    url = source.url.removeprefix("git+")
    line = f"{DISTRIBUTION} @ git+{url}@{source.commit}#subdirectory={source.subdirectory}"
    return line, []


DEV_GROUP = "dev"
TABLE = re.compile(r"^\s*\[\s*dependency-groups\s*\]\s*$", re.MULTILINE)
NEXT_TABLE = re.compile(r"^\s*\[", re.MULTILINE)
DEV_KEY = re.compile(rf"^(?P<indent>\s*){DEV_GROUP}\s*=\s*\[", re.MULTILINE)


def declared_pins(project: dict) -> list[str]:
    """Every ``omp-guardrails`` requirement in the dev dependency group."""
    groups = project.get("dependency-groups", {})
    entries = groups.get(DEV_GROUP, []) if isinstance(groups, dict) else []
    return [
        entry
        for entry in entries
        if isinstance(entry, str) and re.match(rf"{DISTRIBUTION}\s*(?:@|\[|=|$)", entry)
    ]


def with_pin(text: str, line: str) -> str:
    """``pyproject.toml`` text with ``line`` added to ``[dependency-groups] dev`` (PEP 735)."""
    entry = json.dumps(line)
    table = TABLE.search(text)
    if table is None:
        separator = "" if text.endswith("\n") or not text else "\n"
        return f"{text}{separator}\n[dependency-groups]\n{DEV_GROUP} = [\n    {entry},\n]\n"
    following = NEXT_TABLE.search(text, table.end())
    end = following.start() if following else len(text)
    dev = DEV_KEY.search(text, table.end(), end)
    if dev is None:
        insert = f"\n{DEV_GROUP} = [\n    {entry},\n]"
        return text[: table.end()] + insert + text[table.end() :]
    close = closing_bracket(text, dev.end())
    head = text[:close].rstrip()
    joiner = "" if head.endswith(("[", ",")) else ","
    return f"{head}{joiner}\n    {entry},\n{text[close:]}"


def closing_bracket(text: str, start: int) -> int:
    depth, quote = 1, None
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            quote = None if char == quote else quote
        elif char in "\"'":
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("unterminated dev dependency array")


def planned_pyproject(text: str, line: str) -> tuple[str | None, str]:
    """New ``pyproject.toml`` text (None when unchanged) and a report line."""
    import tomllib

    try:
        project = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        return None, f"pin: pyproject.toml is not valid TOML ({error}); add by hand: {line}"
    pins = declared_pins(project)
    if line in pins:
        return None, "pin: the dev dependency group already pins this reference"
    if pins:
        return None, f"pin: kept the existing pin {pins[0]!r}; bump it in a PR to {line!r}"
    try:
        updated = with_pin(text, line)
        written = declared_pins(tomllib.loads(updated))
    except (ValueError, tomllib.TOMLDecodeError):
        written = []
    if written != [line]:
        return None, f"pin: cannot place the pin safely; add it to [dependency-groups] dev: {line}"
    return updated, "pin: added to [dependency-groups] dev; run the repo's lock command (uv lock)"


def existing_pins(pyproject: Path) -> list[str]:
    """The ``omp-guardrails`` pins a repo's ``pyproject.toml`` already declares."""
    import tomllib

    try:
        return declared_pins(tomllib.loads(pyproject.read_text(encoding="utf-8")))
    except (OSError, tomllib.TOMLDecodeError):
        return []
