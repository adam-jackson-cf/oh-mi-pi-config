from __future__ import annotations

from pathlib import Path
from typing import Any

from guardrails.commands.adapters.javascript.lint import Vocabulary, lint as shared_lint
from guardrails.foundation.findings import Finding

VITEST = Vocabulary(
    adapter="vitest",
    mock_calls=frozenset({
        "vi.mock",
        "vi.doMock",
        "vi.spyOn",
        "vi.mockObject",
        "vi.mocked",
    }),
    evasion_flags=frozenset({
        "skip",
        "only",
        "fails",
        "todo",
        "retry",
    }),
)


def lint(repo: Path, config: dict[str, Any], paths: list[str]) -> list[Finding]:
    return shared_lint(repo, config, paths, VITEST)
