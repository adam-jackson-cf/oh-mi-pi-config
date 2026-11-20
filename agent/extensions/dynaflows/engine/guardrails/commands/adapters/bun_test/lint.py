from __future__ import annotations

from pathlib import Path
from typing import Any

from guardrails.commands.adapters.vitest.lint import Vocabulary, lint as shared_lint
from guardrails.foundation.findings import Finding

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
