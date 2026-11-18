"""SURFACE.feature-map: one document per feature, four fixed H2s, links that resolve.

The map lives at the profile's ``verification_surface.feature_map_path``: a directory holding
one Markdown document per feature (searched recursively), or a single document for a one-feature
map. Each feature document has:

1. exactly one H1, the feature name, unique across the map;
2. exactly these H2s, in this order, each with a non-empty body (backticks are ignored):
   ``Sub-features``, ``How to get to it (user POV)``, ``Driving it with <lever>`` (``<lever>``
   is the profile's ``lever_command``) and ``Gotchas``;
3. relative links and reference definitions that resolve to repo files, and ``#anchors`` that
   name a heading in the target Markdown file.

An approved ``verification_surface`` exemption disables the rule. Profile
``guardrails.feature_map.index_files`` names map files (e.g. ``README.md``) that are link-checked
but are not feature documents.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from omp_guardrails.config.profile import PROFILE_PATH
from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import as_list, settings

RULE = "SURFACE.feature-map"
CHECKER = "feature_map"
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
INLINE_LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'(][^)]*)?\)")
REFERENCE_LINK = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s+.*)?$")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
SECTIONS = ("Sub-features", "How to get to it (user POV)", "Driving it with {lever}", "Gotchas")


@dataclass
class Document:
    """A Markdown file's headings, section bodies and link targets, outside code fences."""

    headings: list[tuple[int, str]] = field(default_factory=list)
    bodies: dict[int, list[str]] = field(default_factory=dict)
    links: list[str] = field(default_factory=list)


def surface(profile: dict) -> dict | None:
    """The verification-surface section, or None when it is exempt or absent."""
    value = profile.get("verification_surface")
    if not isinstance(value, dict) or "none" in value:
        return None
    return value


def normalise(text: str) -> str:
    return " ".join(text.replace("`", "").split()).casefold()


def parse(text: str) -> Document:
    document = Document()
    in_fence = False
    current = -1
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            if current >= 0:
                document.bodies[current].append(line)
            continue
        heading = HEADING.match(line)
        if heading:
            document.headings.append((len(heading.group(1)), heading.group(2)))
            current = len(document.headings) - 1
            document.bodies[current] = []
            continue
        if current >= 0:
            document.bodies[current].append(line)
        document.links.extend(INLINE_LINK.findall(line))
        reference = REFERENCE_LINK.match(line)
        if reference:
            document.links.append(reference.group(1))
    return document


def slug(heading: str) -> str:
    """GitHub-style heading anchor."""
    text = heading.replace("`", "").strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def resolve_link(repo: Path, doc: str, target: str) -> tuple[str | None, str]:
    """The repo-relative file a relative link names (None for same-file) and its anchor."""
    path_part, _, anchor = target.partition("#")
    if not path_part:
        return None, anchor
    path_part = unquote(path_part.split("?", 1)[0])
    if path_part.startswith("/"):
        joined = PurePosixPath(path_part.lstrip("/"))
    else:
        joined = PurePosixPath(doc).parent / path_part
    parts: list[str] = []
    for part in joined.parts:
        if part == "..":
            if not parts:
                return "..", anchor
            parts.pop()
        elif part != ".":
            parts.append(part)
    return "/".join(parts) or ".", anchor


def linked_files(repo: Path, doc: str, text: str) -> set[str]:
    """Repo-relative files that a map document's relative links name, whether or not they exist."""
    linked = set()
    for target in parse(text).links:
        if SCHEME.match(target):
            continue
        resolved, _ = resolve_link(repo, doc, target)
        if resolved and resolved != "..":
            linked.add(resolved)
    return linked


def index_files(profile: dict) -> set[str]:
    """Map file names (``guardrails.feature_map.index_files``) that are not feature documents."""
    return set(as_list(settings(profile, CHECKER).get("index_files")))


def link_findings(repo: Path, doc: str, document: Document) -> list[Finding]:
    found = []
    own_slugs = {slug(text) for _, text in document.headings}
    for target in document.links:
        if SCHEME.match(target):
            continue
        resolved, anchor = resolve_link(repo, doc, target)
        if resolved == ".." or (resolved is not None and not (repo / resolved).exists()):
            found.append(Finding(RULE, doc, f"link '{target}' does not resolve", "fix the link"))
            continue
        if not anchor:
            continue
        if resolved is None:
            slugs = own_slugs
        elif resolved.endswith(".md") and (repo / resolved).is_file():
            slugs = {slug(text) for _, text in parse(read(repo / resolved)).headings}
        else:
            continue
        if anchor.lower() not in slugs:
            found.append(
                Finding(RULE, doc, f"link '{target}' names a missing heading", "fix the anchor")
            )
    return found


def section_findings(doc: str, document: Document, lever: str) -> list[Finding]:
    expected = [section.format(lever=lever) for section in SECTIONS]
    found = []
    titles = [text for level, text in document.headings if level == 1]
    if len(titles) != 1:
        found.append(
            Finding(
                RULE,
                doc,
                f"has {len(titles)} H1 headings; a feature document has one, the feature name",
                "give the document exactly one H1 naming its feature",
            )
        )
    h2 = [(index, text) for index, (level, text) in enumerate(document.headings) if level == 2]
    if [normalise(text) for _, text in h2] != [normalise(text) for text in expected]:
        found.append(
            Finding(
                RULE,
                doc,
                f"H2 headings are {[text for _, text in h2]}; expected exactly {expected}",
                "use exactly the four feature-map H2s, in order",
            )
        )
        return found
    for index, text in h2:
        if not any(line.strip() for line in section_lines(document, index)):
            found.append(Finding(RULE, doc, f"section '{text}' is empty", "fill the section"))
    return found


def section_lines(document: Document, index: int) -> list[str]:
    """A heading's body including deeper sub-headings, up to the next heading at its level."""
    level = document.headings[index][0]
    lines = list(document.bodies[index])
    for later in range(index + 1, len(document.headings)):
        if document.headings[later][0] <= level:
            break
        lines.append(document.headings[later][1])
        lines.extend(document.bodies[later])
    return lines


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def map_documents(repo: Path, map_path: str) -> list[str]:
    root = repo / map_path
    if root.is_file():
        return [map_path]
    return sorted(path.relative_to(repo).as_posix() for path in root.rglob("*.md"))


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    """``omp-guardrails surface-lint``."""
    del argv
    values = surface(profile)
    if values is None:
        return []
    map_path = str(values.get("feature_map_path") or "").strip().rstrip("/")
    lever = str(values.get("lever_command") or "").strip()
    if not map_path or not (repo / map_path).exists():
        return [
            Finding(
                RULE,
                map_path or PROFILE_PATH,
                "the feature map named by verification_surface.feature_map_path does not exist",
                "write one document per feature at the feature map path",
            )
        ]
    indexes = index_files(profile)
    found: list[Finding] = []
    owners: dict[str, str] = {}
    for doc in map_documents(repo, map_path):
        document = parse(read(repo / doc))
        found.extend(link_findings(repo, doc, document))
        if PurePosixPath(doc).name in indexes:
            continue
        found.extend(section_findings(doc, document, lever))
        for level, text in document.headings:
            if level != 1:
                continue
            key = normalise(text)
            if key in owners:
                found.append(
                    Finding(
                        RULE,
                        doc,
                        f"feature '{text}' is also documented in {owners[key]}",
                        "keep one document per feature",
                    )
                )
            else:
                owners[key] = doc
    return found
