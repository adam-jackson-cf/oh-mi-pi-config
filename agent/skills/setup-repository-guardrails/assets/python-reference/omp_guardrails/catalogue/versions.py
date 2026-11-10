"""Standard-library subset of PEP 508 requirements and PEP 440 version matching.

The parity checks only ask "does every locked version satisfy the declared specifier?", so
this module supports names, extras, URL requirements, environment markers (ignored) and the
operators ``== != <= >= < > ~= ===``, including ``==X.*`` prefixes. Ordering follows PEP 440:
epoch, release, pre-release, post-release, dev-release; local labels are ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NAME = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(\[[^\]]*\])?\s*")
CLAUSE = re.compile(r"^\s*(===|==|!=|<=|>=|~=|<|>)\s*([^\s,]+)\s*$")
VERSION = re.compile(
    r"^v?(?:(?P<epoch>\d+)!)?(?P<release>\d+(?:\.\d+)*)"
    r"(?:[-_.]?(?P<pre>a|alpha|b|beta|c|rc|pre|preview)[-_.]?(?P<pre_n>\d*))?"
    r"(?:-(?P<post_implicit>\d+)|[-_.]?(?P<post>post|rev|r)[-_.]?(?P<post_n>\d*))?"
    r"(?:[-_.]?(?P<dev>dev)[-_.]?(?P<dev_n>\d*))?"
    r"(?:\+[a-z0-9.]+)?$",
    re.IGNORECASE,
)
PRE_RANK = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "c": 2, "rc": 2, "pre": 2, "preview": 2}


class VersionError(ValueError):
    """A requirement or version is outside the supported subset."""


@dataclass(frozen=True)
class Requirement:
    name: str
    clauses: tuple[tuple[str, str], ...]
    text: str

    def admits(self, version: str) -> bool:
        return all(clause_admits(op, wanted, version) for op, wanted in self.clauses)


def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirement(text: str) -> Requirement:
    body = text.split(";", 1)[0]
    match = NAME.match(body)
    if match is None:
        raise VersionError(f"cannot parse requirement {text!r}")
    rest = body[match.end() :].strip()
    if rest.startswith("@"):
        return Requirement(canonical_name(match.group(1)), (), text)
    rest = rest.strip("()").strip()
    clauses = []
    for part in filter(None, (item.strip() for item in rest.split(","))):
        clause = CLAUSE.match(part)
        if clause is None:
            raise VersionError(f"cannot parse specifier {part!r} in {text!r}")
        clauses.append((clause.group(1), clause.group(2)))
    return Requirement(canonical_name(match.group(1)), tuple(clauses), text)


def release_of(version: str) -> tuple[int, ...]:
    match = VERSION.match(version)
    if match is None:
        raise VersionError(f"unsupported version {version!r}")
    return tuple(int(part) for part in match.group("release").split("."))


def version_key(version: str) -> tuple:
    match = VERSION.match(version.strip())
    if match is None:
        raise VersionError(f"unsupported version {version!r}")
    release = list(release_of(version.strip()))
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    post = match.group("post_implicit") or match.group("post_n")
    has_post = match.group("post_implicit") is not None or match.group("post") is not None
    if match.group("pre"):
        pre = (1, PRE_RANK[match.group("pre").lower()], int(match.group("pre_n") or 0))
    elif match.group("dev") and not has_post:
        pre = (0, 0, 0)
    else:
        pre = (2, 0, 0)
    post_key = (1, int(post or 0)) if has_post else (0, 0)
    dev_key = (0, int(match.group("dev_n") or 0)) if match.group("dev") else (1, 0)
    return (int(match.group("epoch") or 0), tuple(release), pre, post_key, dev_key)


def prefix_admits(wanted: str, version: str) -> bool:
    prefix = release_of(wanted[:-2])
    release = release_of(version.split("+")[0])
    padded = release + (0,) * max(0, len(prefix) - len(release))
    return padded[: len(prefix)] == prefix


def clause_admits(op: str, wanted: str, version: str) -> bool:
    if op == "===":
        return version == wanted
    if op in {"==", "!="} and wanted.endswith(".*"):
        return prefix_admits(wanted, version) == (op == "==")
    if op == "~=":
        release = release_of(wanted)
        if len(release) < 2:
            raise VersionError(f"~={wanted} needs at least two release segments")
        prefix = ".".join(str(part) for part in release[:-1]) + ".*"
        return version_key(version) >= version_key(wanted) and prefix_admits(prefix, version)
    left, right = version_key(version), version_key(wanted)
    return {
        "==": left == right,
        "!=": left != right,
        "<=": left <= right,
        ">=": left >= right,
        "<": left < right,
        ">": left > right,
    }[op]
