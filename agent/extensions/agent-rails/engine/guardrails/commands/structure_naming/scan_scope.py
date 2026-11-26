"""Which repository paths the structure-naming scans treat as vendored or test code."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

VENDORED_FOLDERS = frozenset({
    "__pycache__",
    "build",
    "dist",
    "example",
    "examples",
    "node_modules",
    "site-packages",
    "third_party",
    "vendor",
})
TEST_PREFIXES = ("test", "fixture")
TEST_FOLDERS = frozenset({"__tests__", "spec", "specs"})
TEST_FILE = re.compile(
    r"^(conftest\.py|.*_test\.(py|go)|.*\.(test|spec)\.(ts|tsx|js|jsx|mjs|cjs))$"
)


def is_vendored_path(path: str) -> bool:
    """A folder on the path is hidden or holds generated or third-party code."""
    return any(
        part.startswith(".") or part in VENDORED_FOLDERS
        for part in PurePosixPath(path).parts[:-1]
    )


def is_test_path(path: str) -> bool:
    """The file is a test, fixture or test configuration file by name or folder: a
    folder or file name starting with test or fixture (tests/, testdata/, testutil/,
    fixtures/, test_store.py, tests.py), a __tests__, spec or specs folder, or a
    conftest.py, *_test.py, *_test.go or *.test.ts / *.spec.ts style file."""
    pure = PurePosixPath(path)
    return (
        any(part.startswith(TEST_PREFIXES) for part in pure.parts)
        or bool(TEST_FOLDERS.intersection(pure.parts[:-1]))
        or bool(TEST_FILE.match(pure.name))
    )
