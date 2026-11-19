"""Source rules for ``bun:test`` suites and fast-check (adapter item 4).

The rules are the shared JavaScript runner rules in ``guardrails.adapters.vitest.lint`` with Bun's
vocabulary:

- TEST.no-self-mocking: ``mock.module``, ``spyOn``, ``mock`` and their ``jest``/``vi`` aliases on owned
  code, and assignments to owned imports.
- TEST.no-evasion: ``skip``, ``skipIf``, ``if``, ``only``, ``failing``, ``todo``, ``todoIf``, the
  ``xtest``/``xit``/``xdescribe`` registrars and a ``retry`` option.
- TEST.property-seed and TEST.no-test-hooks: shared with Vitest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from guardrails.adapters.vitest.lint import Vocabulary, lint as shared_lint
from guardrails.findings import Finding

BUN = Vocabulary(
    adapter="bun_test",
    mock_calls=frozenset({
        "mock",
        "mock.module",
        "spyOn",
        "jest.fn",
        "jest.mock",
        "jest.spyOn",
        "vi.fn",
        "vi.mock",
        "vi.spyOn",
        "vi.mocked",
    }),
    evasion_flags=frozenset({
        "skip",
        "skipIf",
        "if",
        "only",
        "failing",
        "todo",
        "todoIf",
        "retry",
    }),
    skipping_registrars=frozenset({"xtest", "xit", "xdescribe"}),
)


def lint(repo: Path, profile: dict[str, Any], paths: list[str]) -> list[Finding]:
    return shared_lint(repo, profile, paths, BUN)
