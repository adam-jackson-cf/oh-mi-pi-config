"""The TEST.no-test-hooks vocabulary every adapter reads (data/test-hook-patterns.json):
the regular-expression fragments that name a test marker, spliced into the adapters'
pattern files, and the word pairs that name a test-only hook in a declaration."""

from __future__ import annotations

import re
from functools import cache
from typing import Any

from guardrails.foundation import conventions
from guardrails.foundation.source import names

VOCABULARY = "test-hook-patterns.json"
FRAGMENT = re.compile(r"%([a-z_]+)%")


@cache
def vocabulary() -> dict[str, Any]:
    found: dict[str, Any] = conventions.data(VOCABULARY)
    return found


def fragment(name: str) -> str:
    """One fragment, a regular expression, by name (marker_name, marker_value, ...)."""
    fragments: dict[str, str] = vocabulary()["fragments"]
    if name not in fragments:
        raise ValueError(f"{VOCABULARY} has no fragment {name!r}")
    return fragments[name]


def expanded(value: Any) -> Any:
    """A pattern-file value with every %name% in its strings replaced by that fragment."""
    if isinstance(value, str):
        return FRAGMENT.sub(lambda match: fragment(match[1]), value)
    if isinstance(value, list):
        return [expanded(item) for item in value]
    if isinstance(value, dict):
        return {key: expanded(item) for key, item in value.items()}
    return value


def matches(name: str, value: str) -> bool:
    """Whether a whole value matches a fragment (an environment variable name, say)."""
    return re.fullmatch(fragment(name), value) is not None


@cache
def hook_phrases() -> frozenset[tuple[str, str]]:
    return frozenset((first, second) for first, second in vocabulary()["hook_phrases"])


def hook_phrase(name: str) -> bool:
    """Whether a declared name ends with a test-hook phrase (resetForTests,
    reset_for_testing, TEST_MODE), its words split by the one identifier tokeniser
    names.words. A name that only starts with one (test_only_findings) names
    something about tests, not a hook for them."""
    words = names.words(name)
    return len(words) >= 2 and (words[-2], words[-1]) in hook_phrases()
