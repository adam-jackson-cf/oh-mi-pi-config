"""Which repository paths the structure-naming scans read: not vendored ones, and for the
candidate proposers only source that runs in a language they propose for. Test source is
suite_files.is_test_source's to say."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import PurePosixPath

from guardrails.foundation import conventions
from guardrails.foundation.source import tree


def is_vendored_path(path: str) -> bool:
    """A folder on the path is hidden or one no scan reads (conventions.ignored_path:
    installed, generated or tool-state code)."""
    return conventions.ignored_path(path) or any(
        part.startswith(".") for part in PurePosixPath(path).parts[:-1]
    )


def proposed(reads: Collection[str]) -> tuple[str, ...]:
    """The languages the candidate proposers propose for (structure-languages.json
    proposers); ValueError when it names one a proposer that reads only `reads` cannot."""
    languages = tree.proposed_languages()
    unread = sorted(set(languages) - set(reads))
    if unread:
        raise ValueError(
            f"structure-languages.json proposers names {', '.join(unread)}, which "
            "this proposer cannot read"
        )
    return languages


def runs(path: str, languages: Collection[str]) -> bool:
    """A file of one of the languages that runs. A stub only declares its module's
    interface, so the proposers read the module it describes, not the stub."""
    language = tree.language_of(path)
    return (
        language is not None
        and language in languages
        and path.endswith(conventions.runtime_extensions(language))
    )


def proposed_source(path: str, reads: Collection[str]) -> bool:
    """A file that runs in a language the proposers propose for (proposed)."""
    return runs(path, proposed(reads))
