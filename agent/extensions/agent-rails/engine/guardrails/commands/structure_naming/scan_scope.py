"""Which repository paths the structure-naming scans read: not vendored ones, and for the
candidate proposers only source that runs in a language they are asked for (--languages). Test
source is suite_files.is_test_source's to say."""

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


def checked(languages: Collection[str], reads: Collection[str]) -> tuple[str, ...]:
    """The languages a proposer is asked for, in order; ValueError when it is asked for
    one it cannot read (a measurement may ask for any language a proposer reads)."""
    unread = sorted(set(languages) - set(reads))
    if unread:
        raise ValueError(f"this proposer cannot read {', '.join(unread)}")
    return tuple(languages)


def language_list(value: str) -> tuple[str, ...]:
    """A proposer's --languages argument: comma-separated language names. The kernel passes
    the languages its judgment question has settings for (thresholds.json decisions.<question>
    .languages); a measurement passes any language the proposer reads."""
    return tuple(name for name in value.split(",") if name)


def runs(path: str, languages: Collection[str]) -> bool:
    """A file of one of the languages that runs. A stub only declares its module's
    interface, so the proposers read the module it describes, not the stub."""
    language = tree.language_of(path)
    return (
        language is not None
        and language in languages
        and path.endswith(conventions.runtime_extensions(language))
    )
