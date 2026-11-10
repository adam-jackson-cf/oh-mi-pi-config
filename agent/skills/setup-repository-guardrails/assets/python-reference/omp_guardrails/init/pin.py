"""The dependency line that pins this reference to one commit of its source repository.

Format (plan §1.8)::

    omp-guardrails @ git+<repository url>@<commit>#subdirectory=<package directory>

Init takes the repository, commit and subdirectory from, in order:

1. ``--pin-url``, ``--pin-commit`` and ``--pin-subdirectory``;
2. the installed distribution's PEP 610 ``direct_url.json``, which pip and uv write when the
   package was installed from git (``url``, ``vcs_info.commit_id``, ``subdirectory``).

The subdirectory defaults to this package's home in its source repository. No repository URL
or commit is built into the code; a URL or commit neither source provides is a setup gap.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import metadata

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
