# 0011: Reject child names that repeat their parent by token

Status: accepted (owner-approved).

## Context

`NAME.conventions` rejected only exact child/parent name equality. Adding a prefix or suffix
(`guardrails/omp_guardrails`, `guardrails/guardrails-core`) retained the redundant owner name
without triggering the rule.

## Decision

Compare each adjacent parent/child pair by tokens. Reject the first child whose tokens contain
the parent's entire nonempty token sequence contiguously and in the same order, anywhere in
the child. Split on `_`, `-`, `.`, ASCII lowercase-or-digit to uppercase transitions, and the
boundary between an uppercase acronym and a capitalized word (`HTTPServer` becomes `http`,
`server`). Discard empty tokens and lowercase the resulting tokens. Digits otherwise stay
attached to their word; no stemming, substring matching or synonym inference applies.

Directory names are compared in full. The final path segment uses the existing house-style
file stem: before the first dot, except that a dotfile keeps its full name. Thus
`guardrails/guardrails.test.py` repeats, while `guardrails/role.guardrails.py` does not.

A single-token parent appearing as a whole word in a longer child **does count**:
`guardrails/omp_guardrails` repeats. The prefix does not remove the redundant owner word.
A substring inside a word does not count (`rail/guardrails`), avoiding incidental spelling
matches. A multi-token parent must appear in full: `audit-log/omp_audit_log_core` repeats,
but `audit-log/audit`, `audit-log/log_audit` and `audit-log/audit_event_log` do not.

## Consequences

The catalogue contract and structure-and-naming guidance use the same semantics. Existing
house-style enablement, finding identity, first-match behavior, path scope and exceptions are
unchanged. Regression cases cover file stems, separator and camel/acronym boundaries,
whole-token matches and incomplete, reordered or noncontiguous parent sequences.
