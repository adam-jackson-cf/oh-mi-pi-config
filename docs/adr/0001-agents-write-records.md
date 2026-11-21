# 0001: Agents write per-repo records

## Status

Superseded in part by [ADR 0014](0014-remove-owner-only-paths.md) (the `ownerOnlyPaths` commit
block) and [ADR 0015](0015-dynaflows-minimal-and-structured-modes.md) (`--enable-dynaflows` and
`dynaflows.enabled` gave way to `--dynaflows-mode` and `dynaflows.mode`). Record writes are not
superseded: agents write records at any time. [ADR 0018](0018-rails-trust-only-what-they-saw.md)
briefly limited them to owner-asked setup; the owner removed that limit on 2026-11-21.

## Context

Per-repo records are personal harness configuration under `repos/<owner>/<name>/`, gitignored and
never committed. Rails blocked edit-tool writes unless the owner had requested dynaflows in the
session, but did not guard bash writes. This added friction without preventing record changes.

## Decision

Agents write and repair per-repo records directly, at any time, independently of the dynaflows switch.
Remove the record-write refusal and session grant state. `omp-guardrails init` accepts live record
destinations without `--enable-dynaflows`; that flag only drafts `dynaflows.enabled` true.

Records are never committed or included in a PR. Approval fields are written only as the owner
stated them in chat. Setup Step 5 remains an owner-approval STOP before applying repository fixes.
The `ownerOnlyPaths` commit block remains unchanged.

## Consequences

Setup and repair no longer require the owner to copy or save a drafted record. Instructional
approval boundaries and repository commit protection remain, without claiming edit-tool enforcement
of record ownership.

## Deciders

Adam Jackson (owner, in chat 2026-11-16).
