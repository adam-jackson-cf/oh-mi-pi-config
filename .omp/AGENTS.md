# oh-mi-pi-config project context

Repository-specific CI, fallback and sync rules for `adam-jackson-cf/oh-mi-pi-config`.
Generic Git, CI and merge authority stays in `skill://git-tool`; this file only adds
what is true of this repository.

## CI runner

This repository's record `repos/adam-jackson-cf/oh-mi-pi-config/gate.json` (personal,
gitignored) declares `"ci": {"requiredContext": "gate", "runner": "ci-gate"}`.
The `ci-gate` role is `scripts/ci-gate.py` on the owner's NUC. Each device's
`~/.omp/ci-runners.json`, written by `scripts/device-rollout.py`, maps that role to
local or ssh-wrapped commands. Use the repository and exact 40-hex SHA:

```sh
python3 ~/.omp/scripts/ci-runner.py submit ci-gate adam-jackson-cf/oh-mi-pi-config SHA
python3 ~/.omp/scripts/ci-runner.py wait ci-gate adam-jackson-cf/oh-mi-pi-config SHA
python3 ~/.omp/scripts/ci-runner.py status ci-gate adam-jackson-cf/oh-mi-pi-config SHA
python3 ~/.omp/scripts/ci-runner.py logs ci-gate adam-jackson-cf/oh-mi-pi-config SHA --tail 100
```

`ci-gate` executes `checks.ci` and publishes the required GitHub commit status `gate`
on the exact head. Rails read `ci` from the repo's record `gate.json` and require
that context to be `SUCCESS` on the exact head before merge.
Submit returns immediately; cached success/failure is reused and infrastructure
`error` retries on resubmission. After a publication error, resubmitting republishes
the stored result; resubmit once and wait, then stop/escalate under the CI table in
`skill://git-tool/references/merge-and-recover.md`. The `ci-gate` wait exits 0 on
success, 2 on timeout, and 1 on failure, error or publication failure. Exit 1 alone
is ambiguous: inspect status/logs, result state and `publicationError` before choosing
the table action. Require successful publication of every required status, not just
a local successful result. A missing entry in `scripts/ci-gate-repos.json` is a setup
gap requiring its own PR, never a CI waiver.

This repository never qualifies for the no-CI exception: it declares a required context.

## GitHub Actions fallback

The documented fallback is the [CI runners section of the readme](../readme.md#ci-runners)
and the actual [ci-quality-gates.yml](https://github.com/adam-jackson-cf/oh-mi-pi-config/blob/main/.github/workflows/ci-quality-gates.yml):
`gh workflow run ci-quality-gates.yml --repo adam-jackson-cf/oh-mi-pi-config --ref <branch>`.
Validate the dispatched run's exact head SHA. Its passing Actions run alone does not
satisfy a missing `gate` status, so it is not autonomous merge authority; escalate the
incident for owner decision.

## nuc-config sync

A `nuc-config` sync is an ordinary PR from `agent/nuc-sync-<date>` into `nuc-config`,
merged with `--merge` (no proof).
