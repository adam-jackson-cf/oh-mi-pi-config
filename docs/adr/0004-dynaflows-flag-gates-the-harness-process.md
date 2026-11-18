# ADR 0004: Dynaflows flag gates the harness process

- Status: accepted, 2026-11-18

## Decision

With dynaflows off, a session gets plain omp + git-tool + generic Git safety rails, nothing else.
The existing per-repo `dynaflows.enabled` switch (and machine-wide off override) governs process
additions for each edited path's repository, not merely the session cwd.

Targeted first-edit, new-code-file and test reference reads, post-edit path checks, outstanding-path
retries at handoff, verification reads after code edits, and `ownerOnlyPaths` commit refusal run
only with dynaflows on. Missing records mean off. Commit checks, workflow injection and exact-head
PR verification remain dynaflows-only.

The Git read gate, effective-cwd and publishing-policy checks, hook-bypass refusal, subagent
filesystem safety and worktree-location rails remain independent of dynaflows. The repo's own hooks
and CI remain independent too.

A per-repo record is harness configuration, not a dynaflows-only concept. git-tool may read
`agentMerge`, `ci.runner` and `ci.requiredContext`, and retains generic Git authority and
record-write policy. It neither mentions nor depends on dynaflows. Dynaflows owns its additions:
valid pathCheck-free schema-v2 record prerequisite, proof/implementation ordering, main never
committing, and `prove()` plus `structure_review()` before exact-head merge. Its verify loop lives
with that workflow.

Native omp judged reply rules (`clean-cutovers`, `flakes`, `measured-claims`, `regression-coverage`)
stay native. At `session_start`, when the session cwd's repo has dynaflows off, rails append the
explicit `dynaflowsRules` names from `agent-rails.yml` to the session setting override
`ttsr.disabledRules`, preserving existing user exclusions. On leaves the setting untouched.
The omp 18.22.2 spike established that an awaited lifecycle override suppresses matching without
a prompt rebuild and is inherited by task children. Already delivered reminders are not retracted.
Tool-trigger policy disclosure remains deterministic and targeted in rails, only with dynaflows on.

## Consequences

An off or unconfigured repo receives no harness implementation prerequisites from git-tool. Enabled
repos receive both targeted policy disclosure and deterministic path checks; a valid record no
longer substitutes path checks for the read gates. Record-based generic Git authority continues to
apply in either state. Tests assert public tool-call, edit-result and handoff outcomes for absent,
off and on records.
