# 0002: The harness owns path checks

## Status

Accepted, 2026-11-16.

## Context

Per-edit path checks need one consistent contract across repositories. Repo scripts varied from
checking the full structure, dependency-direction, naming and test-placement contract to checking
only that two files existed. Delegating through a record command made the same harness event mean
different checks in different repositories.

The dynaflows design directive's principles 1–2 normally keep repositories independent and grow
repo-owned controls rather than run a harness copy. This decision explicitly makes harness-owned
per-edit path checks an exception to those two principles, including where repo checks overlap.
It does not move repository build, test, commit or CI commands into the harness.

## Decision

Rails invoke `omp-guardrails path-check --repo <worktree> --record <record dir>` from the harness
after edits and before agent commits, appending selected changed paths as separate arguments.
The harness owns the implementation and its tests. It enforces dependency direction, folder
ownership, naming and test placement with actionable file, rule and fix diagnostics.

The record profile must contain complete `guardrails.structure`, `guardrails.naming` and
`guardrails.paths` settings. They remain required even when `guardrails.check.repo` delegates
whole-repo checks to repository checkers; that delegation never replaces the per-edit check.
At commit, the harness path check is followed by the repo's `checks.commit` and whole-repo harness
checks. The repository's own CI commands and required-context contract remain unchanged.

Remove `pathCheck` completely from the record schema, examples, discovery and execution. There is
no compatibility field, command override, shim or fallback. Strict schema validation rejects old
records carrying `pathCheck` until the field is deleted; missing required profile settings also
fail closed.

## Consequences

Every repository receives the same per-edit checks, configured by its record rather than a local
script. Repositories still build, test and run their own checks independently of the harness;
no harness package, script or configuration is installed into them.

Immediately after this change merges, perform the owner-approved migration of live records:
delete `pathCheck`, complete the required profile settings, and preserve existing repo commit and
CI commands. Records are gitignored personal configuration and are not migrated in this PR.
Until migrated, old records fail; there is no temporary acceptance period or automatic fallback.

## Deciders

Adam Jackson (owner, in chat 2026-11-16).
