# 0013: Dynaflows bundles the skills it calls

Status: accepted (owner-approved, 2026-11-19). Supersedes the skill-location decision in
[ADR 0008](0008-generic-setup-skill-and-record-docs.md).

## Context

Dynaflows linked five skills outside its package. One, `codify-omp-workflow-corrections`, lived in
`~/.agents/by-request`, which omp does not load, so its link resolved nowhere.

## Decision

The package's `skills/` directory holds every skill dynaflows calls:

- `agent/extensions/dynaflows/skills/`: `dynaflows`, `test-guidance`, `setup-repo-proofs`,
  `jev-actions` and `codify-omp-workflow-corrections`.
- `agent/skills/`: `git-tool`, which also serves dynaflows-off sessions.

Skill names and `skill://` URLs do not change. The configured package directory loads its skills
whether dynaflows is on or off, so the generic rails' `test-guidance` reads still resolve.
`test-guidance` and `setup-repo-proofs` keep their generic wording; their tests still forbid
harness terms. The by-request original of the corrections skill belongs to another checkout and is
not edited here.

## Consequences

The extension works with a plain omp install plus `git-tool`. Moved skills are not kept at their
old paths.
