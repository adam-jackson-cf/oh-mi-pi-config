# 0016: Record the skill moves and remove dynaflows legacy

Status: accepted, 2026-11-21. Supersedes in part
[ADR 0006](0006-test-guidance-is-generic.md), [ADR 0007](0007-delivery-principles-and-policy-homes.md),
[ADR 0009](0009-dynaflows-is-an-extension-package.md), [ADR 0010](0010-dynaflows-package-layout.md),
[ADR 0013](0013-dynaflows-bundles-its-skills.md) and
[ADR 0015](0015-dynaflows-minimal-and-structured-modes.md).

## Context

Two skill moves and one rename landed without an ADR. ADR 0013 placed `git-tool` in
`agent/skills/` and `jev-actions` in the package; the two have since swapped. ADR 0010 named the
bundled workflow skill `dynaflows`, the same name as the extension. Earlier ADRs also kept
deprecated skill files until a final cleanup, and ADR 0015 kept a by-name refusal of the removed
`enabled` key. No deprecated skill folder remains, and the owner allows no legacy tolerance.

## Decision

- `git-tool` lives at `agent/extensions/dynaflows/skills/git-tool/` and serves both modes; its
  merge, CI and recovery runbook applies in structured mode only.
- `jev-actions` is a standalone skill at `agent/skills/jev-actions/`.
- The bundled workflow skill is `structured-workflow`, at
  `agent/extensions/dynaflows/skills/structured-workflow/`; `skill://dynaflows` does not resolve.
  The extension keeps the name dynaflows.
- The deprecated-skill retention exception is withdrawn from the design directive and delivery
  principles.
- The record and machine-file readers and `guardrails init` reject `enabled` like any other
  unknown field, with no message naming it.

## Consequences

Callers use `skill://git-tool`, `skill://jev-actions` and `skill://structured-workflow`. A record
or machine file that still holds `enabled` fails to load with the generic unknown-field or mode
error.
