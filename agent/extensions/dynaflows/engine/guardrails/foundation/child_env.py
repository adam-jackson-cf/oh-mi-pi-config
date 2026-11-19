from __future__ import annotations

import os
from collections.abc import Iterable, Mapping

HARNESS_VARIABLES = frozenset({"AGENT"})


def repo_environment(
    base: Mapping[str, str] | None = None, *, unset: Iterable[str] = ()
) -> dict[str, str]:
    removed = HARNESS_VARIABLES | set(unset)
    source = os.environ if base is None else base
    return {key: value for key, value in source.items() if key not in removed}


def clean_environment(unset: Iterable[str] = ()) -> dict[str, str]:
    return {
        key: value
        for key, value in repo_environment(unset=unset).items()
        if not key.startswith("GIT_")
    }
