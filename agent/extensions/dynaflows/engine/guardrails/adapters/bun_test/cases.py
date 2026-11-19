"""Static ``bun:test`` collection: the shared JavaScript/TypeScript collector (adapter item 2).

``bun:test`` registers tests with Vitest's ``test``/``it``/``describe`` API, so the collector in
``guardrails.adapters.vitest.cases`` reads both; it strips the ``bun:test`` module from imported
names. It never imports or executes the target's code.
"""

from __future__ import annotations

from guardrails.adapters.vitest.cases import SUFFIXES, Collected, collect

__all__ = ["SUFFIXES", "Collected", "collect"]
