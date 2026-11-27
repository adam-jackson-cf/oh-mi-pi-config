"""The words of an identifier, for every reader that compares names by their words."""

from __future__ import annotations

import re

WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def words(name: str) -> list[str]:
    """The lowercase words of an identifier, split at underscores, hyphens, dots, case
    changes and digit runs: snake_case, camelCase, PascalCase and HTTPServer2 alike."""
    return [word.lower() for word in WORD.findall(name)]
