# Gate wiring

A control is enforced only when a deterministic point runs it and blocks on failure. Agent instructions, contributor docs and a
CI job whose status nothing requires never enforce; they are `manual` at best.

| Point | Blocks | Declared in `proofs.toml` as |
| --- | --- | --- |
| Installed Git hook | The local commit (or push) it runs on | `enforced_by` `point = "hook"`, the hook file and the `ref` that invokes the control |
| CI job | A merge, only while host protection requires its status | `enforced_by` `point = "ci"`, the workflow file, the `ref` and the `status` |
| Host setting | Merges that lack required statuses | A claim with a `host` query (`GIT.required-checks`) |

`GATE.enforced` requires at least one point per claim, with a canary run through the point's entrypoint. `GATE.parity` requires every
point of one claim to invoke the same entrypoint. `GATE.self-check` runs the validator as its own CI job posting the `[validator]`
status; `GIT.required-checks` makes the host require that status and every claim's CI status, so deleting a job or the validator
blocks merges.

## Events

1. Commit: the installed hook runs the check entrypoint (static checks, structure, naming and placement checks on added and renamed
   paths, every T1 test except deferred ones, and affected T2 boundary tests); a nonzero exit blocks the commit. Budget timing
   blocks only here.
2. Push and pull request: CI runs the same entrypoint plus the full T1 and T2 suite (deferred T1 included), and the validator
   job, on every push and pull request. CI never measures or reports test timing.
3. Merge: every required status is green on the exact head, whoever posts it.
4. Later jobs: T3 (tags `live`, `exploratory`, `mutation`, `load`) and owner-approved deferred T2 tests run outside the hook
   and CI entrypoints, on a schedule, by hand or as a release gate as the repository chooses; none is wired without such
   tests. A later job blocks only what it gates (a release); its status is never a required merge status. A claim it
   enforces (`TEST.mutation-sensitivity`) names it as a `later` point.

Auto-fix stays an explicit command outside check-only points.

## Structure checks

Structure, naming and test-placement checks (`STRUCT.dependency-direction`, `STRUCT.folder-placement`, `NAME.conventions`,
`TEST.placement`) read their maps from `proofs.toml` `[structure]` and `[layout]`, judge added and renamed paths, and print one
error per line: `<file>: <RULE-ID>: <problem>; <fix>`. The hook passes staged additions and renames; CI passes merge-base-to-head
additions and renames; a run with no paths scans the whole repository.

## Hook

- Add one entry to the repo's tracked hook manager (pre-commit, lefthook, husky) that runs the check entrypoint, propagating nonzero status.
- With no manager, commit a native `.githooks/pre-commit` and enable it with `git config core.hooksPath .githooks`. A shell hook is a
  maintained shell script, so the `SHELLCHECK.*` and `SHFMT.*` rules then apply to it; a hook in the repo's main language avoids that:

```sh
#!/bin/sh
set -eu
cd "$(git rev-parse --show-toplevel)"
exec make check  # substitute the repo's check entrypoint
```

- `proofs.toml` `[hooks]` holds the installation check the validator runs; installing the hook is part of setup in every clone that
  commits, including agent machines; document the install command.
- Never disable hooks with flags, configuration or skip variables. A hook alone is bypassable; only a required CI status blocks merges.

## CI

Run the entrypoint as one job, or as parallel jobs of the same runner with tier selectors whose union equals it. Parallel CI needs
an aggregate job that depends on every tier job, runs even after a dependency fails, and succeeds only when every tier result is
`success`; failed, cancelled or skipped tiers fail it. The aggregate's status is the claim's `status`. GitHub Actions example:

```yaml
gate:
  needs: [t1, t2, static]
  if: ${{ always() }}
  runs-on: ubuntu-latest
  steps:
    - run: |
        test "${{ needs.t1.result }}" = success
        test "${{ needs.t2.result }}" = success
        test "${{ needs.static.result }}" = success
proofs:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - run: python3 tools/validate_rule_coverage.py --root .
```

For a CI runner other than GitHub Actions, the runner runs the entrypoint on the exact SHA and posts each status.
Add `.devcontainer/devcontainer.json` with `build.dockerfile` or `image` mirroring the runner toolchain and an optional
`postCreateCommand` for dependency installation, and register the repository with that runner as its own documentation describes.
A missing registration or credential is a blocker for the owner to provision, never a reason to switch runners.

## Host protection

`GIT.required-checks` (conditional on host support): protect the resolved default branch, require every claim's CI status and the
validator status, and enable auto-merge and rebase-merge where supported. The `host` claim's query (for example
`gh api repos/<owner>/<name>/branches/<default>/protection`) names the origin repository; the validator runs it with
`--live-host-query` and requires every status in its output. A host that cannot protect the branch (for example HTTP 403 on a free
private repository) is an approved `[[not_applicable]]` exclusion whose `host = true` command evidence shows the refusal, never a fabricated success.
Owner review (for example CODEOWNERS) protects `proofs.toml`, the validator and CI workflow files.
