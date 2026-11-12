"""Environments for the repository commands omp-guardrails starts.

The harness runs omp-guardrails with its own variables set (``AGENT`` names the running agent).
A repository command must behave the same with or without the harness, so every child process
that runs repository code starts without the harness variables (``HARNESS_VARIABLES``). Nothing
in the repository needs to unset them itself.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping

HARNESS_VARIABLES = frozenset({"AGENT"})


def repo_environment(
    base: Mapping[str, str] | None = None, *, unset: Iterable[str] = ()
) -> dict[str, str]:
    """``base`` (default: this process's environment) without harness and ``unset`` variables."""
    removed = HARNESS_VARIABLES | set(unset)
    source = os.environ if base is None else base
    return {key: value for key, value in source.items() if key not in removed}


def clean_environment(unset: Iterable[str] = ()) -> dict[str, str]:
    """``repo_environment`` without git context either (a hook's ``GIT_*`` redirect git)."""
    return {
        key: value
        for key, value in repo_environment(unset=unset).items()
        if not key.startswith("GIT_")
    }
