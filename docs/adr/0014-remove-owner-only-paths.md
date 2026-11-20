# 0014: Remove the record's owner-only paths

Status: accepted (owner-approved, 2026-11-20). Supersedes the `ownerOnlyPaths` commit refusal in
[ADR 0001](0001-agents-write-records.md) and [ADR 0004](0004-dynaflows-flag-gates-the-harness-process.md).

## Context

The record's `gate.json` required `ownerOnlyPaths`, a list of repo paths only the owner changes,
such as the dependency-audit exception file. With dynaflows on, rails refused an agent commit
whose staged or unstaged changes touched one of those paths.

The refusal ran only in harness sessions with dynaflows on. A plain `git` commit outside rails, a
session with dynaflows off, or an edit to the record itself passed it. The list therefore claimed
protection it did not give: it was easy to bypass and gave false assurance.

## Decision

The owner removed the mechanism completely:

- `gate.json` has no `ownerOnlyPaths` field. The TypeScript and Python record readers and
  `guardrails init` reject it as an unknown field, so a record that still holds it fails to load
  until the owner removes it.
- Rails no longer inspect commits for owner-only paths.
- Skills, the catalogue and docs no longer name the field.

The dependency-audit exception file stays the owner's to write. Agents still never add or edit its
entries; the repo's own audit checker validates every entry. That policy needs no record field.

## Consequences

Every live record must drop `ownerOnlyPaths` before rails accept it again. No harness check now
stops an agent commit that changes the exception file; the owner reviews such changes at the PR.
