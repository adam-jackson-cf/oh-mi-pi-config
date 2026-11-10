# Approved application

## Objective

Install only approved guardrails while keeping unrelated target work intact.

## Guidance

1. Recheck target Git status and approved base ref. Use an isolated worktree or
   equivalent reversible checkout when unrelated work or overlapping edits
   exist.
2. Apply only approved paths. Merge surgically or replace only by the user's
   decision. Remove approved superseded runner/hook policies instead of
   retaining duplicate authorities; where nothing can be removed, record why the
   added contract has no predecessor.
3. Where the stack has a skill reference, run the approved
   `omp-guardrails init --greenfield|--adopt|--extend` in the isolated checkout,
   then write the approved profile values; compare what it wrote with the
   approved matrix before continuing. Pin tools and install only approved
   dependencies through the target package manager. Keep target roots, exceptions,
   allowlists, naming/ownership maps, and CI wiring explicit.
4. Write `<repo>/.omp/gate.json` with `schemaVersion: 2` and the approved
   command arrays. This example illustrates the exact schema; substitute real
   target commands and test globs:

   ```json
   {
     "schemaVersion": 2,
     "checks": {
       "commit": ["pnpm", "check", "--tier", "t1", "--affected"],
       "ci": ["pnpm", "check"]
     },
     "pathCheck": ["pnpm", "guardrails", "paths"],
     "pathCheckTimeoutMs": 20000,
     "testGlobs": ["**/*.test.ts", "tests/**"],
     "agentMerge": "allowed",
     "ciHost": "github"
   }
   ```

   Only `schemaVersion` is required by the format; all other fields are
   optional. Installed guardrails must populate `checks.commit`, `checks.ci`
   and `pathCheck` to enforce their rules. Commands are nonempty argv arrays,
   not shell strings. Checks follow the test tiers and run only at these events:

   1. Commit: the pre-commit hook runs `checks.commit`: static checks, every
      T1 test, and affected T2 boundary tests (seams and journeys). agent-rails blocks
      agent commits that bypass the hook, and commits in a repo that declares
      `checks.commit` without an installed executable hook.
   2. Push and pull request: CI runs `checks.ci`, the full T1 and T2 suite.
   3. Merge: the `gate` status from `checks.ci` must be green on the exact head.
   4. Schedule: T3 runs on a schedule, outside `.omp/gate.json`.

   No pre-push hook and no handoff check run exist. CI runs `checks.ci` as one
   job named `gate` or as parallel tier jobs of the same runner whose union
   equals it. Parallel CI requires an aggregate job/status named `gate`
   depending on every tier job. Run the aggregate unconditionally after
   dependencies finish, including on failure, and fail unless every tier result
   is `success` (not failed, cancelled, or skipped). Append changed paths
   to `pathCheck` as arguments. It exits 0 when clean; nonzero output has one
   error per line, each naming the file and fix for structure, naming, or test
   placement. `pathCheckTimeoutMs` is a positive integer timeout in
   milliseconds; `testGlobs` lists strings. `agentMerge` accepts only `allowed`
   or `disabled`; absent field or file defaults to `allowed`. Do not infer merge
   permission from this setup skill; honor the governing Git authorization.

   Optional `ciHost` accepts `github` (the default when absent) or `nuc`.
   `github` means GitHub Actions on every push and pull request. Any repository
   owned by `adam-versed` or `adam-jackson-cf` may use `nuc` when the owner
   chooses it. Other owners default to `github` unless the user says otherwise. Add
   `.devcontainer/devcontainer.json` specifying `build.dockerfile` or `image`
   mirroring the CI runner's toolchain, with an optional `postCreateCommand`
   for dependency installation. Add the repo's `owner/repo` to oh-mi-pi-config's
   `scripts/ci-gate-repos.json` via a PR to that repo, using the owner's
   `tokenEnv: NUC_GATE_GITHUB_TOKEN_<OWNER>` (owner uppercased, `-` replaced
   with `_`), for example `NUC_GATE_GITHUB_TOKEN_ADAM_JACKSON_CF` or
   `NUC_GATE_GITHUB_TOKEN_ADAM_VERSED`. A missing owner token in Infisical is
   a blocker to report for the owner to provision, never a reason to fall back
   to `github`.
   The NUC runs `checks.ci` on the exact SHA and posts the GitHub commit
   status `gate`. Keep `checks.ci` in the GitHub CI workflow, scheduled weekly
   plus manual `workflow_dispatch` as a fallback, rather than on PRs. Follow
   git-tool's verify loop to submit/wait for each head or dispatch the
   fallback.

5. Add the complete coverage ledger and bundled validator or proven equivalent
   to the check-only `checks.ci`. Install the approved pre-commit hook
   executing `checks.commit` through the repo's tracked hook manager when it
   has one, propagating nonzero status. With no manager, commit a native
   `.githooks/pre-commit` and set `git config core.hooksPath .githooks`.
   Installing the hook is part of setup in every clone that commits, including
   agent machines; document the install command. Include path, growth and duplicate checks.
   Budget timing blocks locally in `checks.commit` using the profile's approved machine limit;
   CI runs the same checker report-only for timing. Missing, invalid or stale reports fail everywhere.
   First limits and raises need owner approval; unapproved machines report only and prompt for a baseline.
   Keep auto-fix explicit and outside check-only CI.
6. Configure approved protection on the resolved default branch to require the
   `gate` status check and all other approved required checks. Enable auto-merge
   and rebase-merge where the host supports them. Read back actual settings;
   unsupported capability or permission is an explicit blocker or
   evidence-backed conditional exclusion, never a fabricated successful
   configuration.
7. Install positive/negative canaries and checker tests near their owner. Update
   contributor and application-user documentation separately. Inspect for
   out-of-scope edits, secrets, and generated output before publication; follow
   existing Git/PR authorization.

## Done when

- Changes match the approved matrix, preserve unrelated work, and remove
  superseded authorities.
- The gate contract, hook, checkers, host protection, and proof inputs are
  installed or blocked.
