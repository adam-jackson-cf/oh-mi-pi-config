"""Which repository paths the structure-naming scans read: not vendored ones, and for the
Python candidate proposers only Python that runs. Test source is
suite_files.is_test_source's to say."""

from __future__ import annotations

from pathlib import PurePosixPath

from guardrails.foundation import conventions


def is_vendored_path(path: str) -> bool:
    """A folder on the path is hidden or one no scan reads (conventions.ignored_path:
    installed, generated or tool-state code)."""
    return conventions.ignored_path(path) or any(
        part.startswith(".") for part in PurePosixPath(path).parts[:-1]
    )


def runs(path: str) -> bool:
    """A Python file that runs. A stub only declares its module's interface, so the
    proposers read the module it describes, not the stub."""
    return path.endswith(conventions.runtime_extensions("python"))
