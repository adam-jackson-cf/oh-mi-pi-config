# ADR 0006: Test guidance is generic

- Status: accepted
- Date: 2026-11-18

## Decision

Test-guidance describes generic testing practice, usable without dynaflows. The calling workflow
supplies the test profile location; repository policy may tighten the guidance. Dynaflows binding
policy, record location, task ownership, freeze/prove protocol and catalogue enforcement belong to
its own references. Plain TDD remains in generic guidance.

Keep deprecated coding-guidance files untouched until a final owner-signed-off cleanup PR. Migrate
its regression test-selection rules into case-selection; delivery's rule-naming and root-cause
repair belong to the separate delivery policy change.

The owner replaced behaviour-comment justification with descriptive test names: the comment ban
applies to tests. TEST.growth.justified's implementation changes in the integrity-checks PR.
The profile template keeps its existing structure, house_style and guardrails keys with generic
descriptions; the record-profile template split is deferred to PR 3 when record documentation moves
to the harness. Profile consumers and schema remain unchanged here.

## Consequences

The enforcement index links catalogue IDs and exact generic guidance anchors; a repository test
validates both commands and anchors. A separate test rejects workflow mechanisms returning to generic
guidance. Dynaflows callers must read proofs.md for binding rules and test-enforcement.md for checker
mappings; their caller migration is owned by the delivery-policy PR, which merges last.
