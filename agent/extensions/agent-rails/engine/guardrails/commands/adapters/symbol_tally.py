"""TEST.no-test-hooks for production symbols only tests use, tallied once for every adapter.

The tally keys every owned production definition and every use by (file, symbol). Each
adapter resolves its language's references with the shared readers in foundation/source
(Python imports and module attributes through modules.PythonIndex, Go package import paths
and same-package names, TypeScript specifiers through the JavaScript adapter's resolver)
and records each use as a test or a production one. A definition is reported when tests
use it, production does not, and it is not a declared public entry point.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass, field

from guardrails.foundation.findings import Finding
from guardrails.foundation.source.modules import Reexport, SymbolUse

RULE = "TEST.no-test-hooks"
FIX = (
    "drive the behaviour through a public entry point and delete the symbol, or give it a "
    "production caller; production code never carries hooks only tests use"
)
DEFAULT_EXPORT = "default"

Key = tuple[str, str]


@dataclass(frozen=True)
class Defined:
    """Where a production definition sits: its first line and its byte span."""

    line: int
    span: tuple[int, int]

    def holds(self, at: int) -> bool:
        return self.span[0] <= at <= self.span[1]


@dataclass
class Tally:
    """Owned production definitions, the files that re-export names, and which
    definitions production and tests use (each test use with the first test file)."""

    definitions: dict[Key, Defined]
    reexports: dict[str, list[Reexport]] = field(default_factory=dict)
    production: set[Key] = field(default_factory=set)
    tests: dict[Key, str] = field(default_factory=dict)

    def defined(
        self, path: str, symbol: str, seen: frozenset[Key] = frozenset()
    ) -> Key | None:
        """The definition a file's top-level name stands for, through re-exports."""
        key = (path, symbol)
        if key in self.definitions:
            return key
        if key in seen:
            return None
        for item in self.reexports.get(path, []):
            if item.local == symbol:
                imported = item.imported or symbol
            elif item.local is None and symbol != DEFAULT_EXPORT:
                imported = symbol
            else:
                continue
            found = self.defined(item.target, imported, seen | {key})
            if found is not None:
                return found
        return None

    def record(self, user: str, uses: Iterable[SymbolUse], *, test: bool) -> None:
        """Count a file's uses; a definition's use of its own name is not a use."""
        for use in uses:
            key = self.defined(use.path, use.symbol)
            if key is None or (key[0] == user and self.definitions[key].holds(use.at)):
                continue
            if test:
                self.tests.setdefault(key, user)
            else:
                self.production.add(key)

    def public(self, entries: Iterable[Key]) -> set[Key]:
        return {key for path, symbol in entries if (key := self.defined(path, symbol))}

    def findings(
        self, reported: Collection[str], public: Collection[Key]
    ) -> list[Finding]:
        return [
            Finding(
                RULE,
                path,
                f"line {defined.line}: production symbol '{name}' is used only by "
                f"tests ({self.tests[path, name]})",
                FIX,
            )
            for (path, name), defined in self.definitions.items()
            if path in reported
            and (path, name) in self.tests
            and (path, name) not in self.production
            and (path, name) not in public
        ]
