"""What every adapter's lint reads source through: one loader that reads and parses a test
or owned production file with tree.read and tree.parse, and turns a file it cannot read or
parse into the adapter's finding (TEST.no-evasion for a test file, TEST.no-test-hooks for
production), and the findings for a manifest or config the adapters cannot resolve owned
code from. No adapter skips an unreadable file silently."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from guardrails.context.config.entry_points import EntryPoint, entry_points
from guardrails.context.config.section_schema import ConfigValueError
from guardrails.foundation import conventions
from guardrails.foundation.findings import Finding, config_label
from guardrails.foundation.inventory import normalized_path, repo_paths
from guardrails.foundation.source import tree

if TYPE_CHECKING:
    from ast_grep_py import SgNode

EVASION = "TEST.no-evasion"
HOOKS = "TEST.no-test-hooks"
CONFIG_RULE = "CONFIG.complete"
UNREADABLE_FIX = (
    "restore readable UTF-8 source that parses, then run adapter lint again"
)
MANIFEST_FIX = (
    "repair the file so adapter lint can resolve owned code, then run it again"
)
CONFIG_FIX = (
    "map each owned package to a list of '<module>:<symbol>' entries in "
    "tests.public_entry_points"
)
READ_ERRORS: tuple[type[Exception], ...] = (OSError, UnicodeError, tree.SourceError)
EntryPoints = tuple[EntryPoint, ...]


@dataclass(frozen=True)
class Source:
    path: str
    text: str
    root: SgNode


def unreadable_rule(*, test: bool) -> str:
    return EVASION if test else HOOKS


def unparseable(path: str, error: Exception, *, test: bool) -> Finding:
    return Finding(
        unreadable_rule(test=test),
        path,
        f"cannot parse source for AST analysis: {error}",
        UNREADABLE_FIX,
    )


def load(repo: Path, path: str, *, test: bool) -> Source | Finding:
    """A file's text and syntax tree, or the finding that says why it has neither."""
    try:
        text = tree.read(repo, path)
    except READ_ERRORS as error:
        return Finding(
            unreadable_rule(test=test),
            path,
            f"cannot read source for AST analysis: {error}",
            UNREADABLE_FIX,
        )
    try:
        root = tree.parse(path, text)
    except tree.SourceError as error:
        return unparseable(path, error, test=test)
    return Source(path, text, root)


def load_all(
    repo: Path, paths: Iterable[str], is_test: Callable[[str], bool]
) -> tuple[dict[str, Source], list[Finding]]:
    """The files that read and parse, by path, and a finding for each that does not."""
    sources: dict[str, Source] = {}
    findings: list[Finding] = []
    for path in sorted(paths):
        loaded = load(repo, path, test=is_test(path))
        if isinstance(loaded, Finding):
            findings.append(loaded)
        else:
            sources[path] = loaded
    return sources, findings


def manifest_finding(path: str, error: Exception) -> Finding:
    """A manifest (package.json, tsconfig.json, go.mod) the adapter needs to resolve owned
    code, which cannot be read."""
    name = PurePosixPath(path).name
    return Finding(
        HOOKS, path, f"cannot read {name} to resolve owned code: {error}", MANIFEST_FIX
    )


def configured_entry_points(config: dict[str, Any]) -> EntryPoints | Finding:
    """The config's public entry points, or the finding for a section in another form."""
    try:
        return entry_points(config)
    except ConfigValueError as error:
        return Finding(CONFIG_RULE, config_label("tests"), str(error), CONFIG_FIX)


def repository_paths(repo: Path, paths: Iterable[str] | None = None) -> list[str]:
    """Repository-relative paths, the given ones or every tracked file, outside every
    folder no scan reads (conventions.ignored_path: node_modules, vendor, .venv, ...)."""
    chosen = repo_paths(repo) if paths is None else paths
    return [
        path
        for raw in chosen
        if (path := normalized_path(repo, raw)) and not conventions.ignored_path(path)
    ]
