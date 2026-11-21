# 0011: Reject child names that repeat their parent by token

Status: Superseded in part by [ADR 0012](0012-file-parent-repeats-and-scoped-exceptions.md) (token
matching extends to files; checks judge only new names).

## Context

`NAME.conventions` rejected only exact child/parent name equality. Adding a prefix or suffix
(`guardrails/omp_guardrails`, `guardrails/guardrails-core`) retained the redundant owner name
without triggering the rule.

## Decision

Compare each adjacent directory pair by tokens. Reject the first child directory whose tokens
contain the parent's entire nonempty token sequence contiguously and in the same order, anywhere
in the child. Split on `_`, `-`, `.`, ASCII lowercase-or-digit to uppercase transitions, and the
boundary between an uppercase acronym and a capitalized word (`HTTPServer` becomes `http`,
`server`). Discard empty tokens and lowercase the resulting tokens. Digits otherwise stay
attached to their word; no stemming, substring matching or synonym inference applies.

Files retain the previous exact, case-sensitive comparison between their house-style stem
(before the first dot, except dotfiles) and their full parent name. Thus
`guardrails/guardrails.test.py` repeats, but `foo/foo_test.go`, `init/__init__.py` and
`guardrails/omp_guardrails.py` do not. In a tracked file path, the final segment is the file and
all preceding segments are directories.

A single-token parent appearing as a whole word in a longer child directory **does count**:
`guardrails/omp_guardrails/` repeats. The prefix does not remove the redundant owner word.
A substring inside a word does not count (`rail/guardrails/`), avoiding incidental spelling
matches. A multi-token parent must appear in full: `audit-log/omp_audit_log_core/` repeats,
but `audit-log/audit/`, `audit-log/log_audit/` and `audit-log/audit_event_log/` do not.

The initial all-segment token scan added 4 findings in oh-mi-pi-config, 6 in raidar, 26 in scout
and 42 in enaible-analyzers, including ecosystem test names and Python package sentinels.
The owner narrowed the extension to directory pairs to avoid changing file naming conventions.
These scan counts are observations of those working trees, not a permanent baseline or exemption.

## Consequences

The catalogue contract and structure-and-naming guidance use the same semantics. Existing
house-style enablement, finding identity, first-match behavior, path scope and exceptions are
unchanged. Regression cases cover file stems, separator and camel/acronym boundaries,
whole-token matches and incomplete, reordered or noncontiguous parent sequences.
