from __future__ import annotations

import os
from collections.abc import Mapping

HARNESS_VARIABLES = frozenset({"AGENT"})


def repo_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if base is None else base
    return {key: value for key, value in source.items() if key not in HARNESS_VARIABLES}


def clean_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in repo_environment().items()
        if not key.startswith("GIT_")
    }
