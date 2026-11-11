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
       "ci": ["pnpm", "check"],
       "profile": ["pnpm", "guardrails", "profile-check"]
     },
     "pathCheck": ["pnpm", "guardrails", "paths"],
     "pathCheckTimeoutMs": 20000,
     "testGlobs": ["**/*.test.ts", "tests/**"],
     "agentMerge": "allowed",
     "ci": { "requiredContext": "gate", "runner": "github-actions" },
     "dynaflowsContract": 2
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
   3. Merge: the declared required context (`ci.requiredContext`, normally
      `gate`) from `checks.ci` must be green on the exact head, whoever posts it.
   4. Schedule: T3 runs on a schedule, outside `.omp/gate.json`.

   No pre-push hook and no handoff check run exist. CI runs `checks.ci` as one
   job named by `ci.requiredContext` or as parallel tier jobs of the same runner
   whose union equals it. Parallel CI requires an aggregate job/status named by
   `ci.requiredContext` depending on every tier job. Run the aggregate
   unconditionally after dependencies finish, including on failure, and fail
   unless every tier result
   is `success` (not failed, cancelled, or skipped). Append changed paths
   to `pathCheck` as arguments. It exits 0 when clean; nonzero output has one
   error per line, each naming the file and fix for structure, naming, or test
   placement. `pathCheckTimeoutMs` is a positive integer timeout in
   milliseconds; `testGlobs` lists strings. `agentMerge` accepts only `allowed`
   or `disabled`; absent field or file defaults to `allowed`. Do not infer merge
   permission from this setup skill; honor the governing Git authorization.

   Optional `checks.profile` is the profile completeness command (for the
   Python reference, its `profile-check` subcommand); `prove()` runs it first
   and blocks on failure. Optional `checks.format` is the formatter `freeze()`
   runs before hashing proofs.

   Optional `dynaflowsContract` is the positive-integer dynaflows contract
   version the repo was set up against; the current version is
   `agent/agent-rails.yml` `dynaflowsContract`. `omp-guardrails init` writes it
   when absent, and `--adopt`/`--extend` raise a lower value; never lower it by
   hand. With dynaflows on, a missing or lower value blocks `freeze()` (except
   project setup's `project_setup=True` freeze) and `prove()`.

   Optional `ci` declares the merge-time CI requirement: `requiredContext` is
   the commit status context or check-run name that must be `success` on the
   exact PR head, and `runner` is a role name. Neither field names a host or an
   account. Each machine resolves the role to submit, wait, status and logs
   commands in its machine-level runner config (`ci-runners.json`, written by
   the device setup step, not by this skill); agents drive it with
   `ci-runner.py <submit|wait> <runner> <OWNER/REPO> <SHA>`. Use the role the
   owner chooses. The `github-actions` role means a GitHub Actions workflow
   runs `checks.ci` on every push and pull request in a job whose name is the
   required context (push triggers it, so there is nothing to submit). Any other role is
   a runner that reads `checks.ci` from the gate file, runs it on the exact SHA
   and posts the required context. For such a runner, add
   `.devcontainer/devcontainer.json` specifying `build.dockerfile` or `image`
   mirroring the runner's toolchain, with an optional `postCreateCommand` for
   dependency installation, and register the repository with that runner as
   its own project or machine instructions describe. A missing registration,
   credential or machine runner entry is a blocker to report for the owner to
   provision, never a reason to switch roles. A repository with a non-Actions
   runner may keep `checks.ci` in a GitHub workflow scheduled weekly plus
   manual `workflow_dispatch` as a fallback, rather than on PRs. Follow
   git-tool's verify loop to submit and wait for each head.

5. When the repository has its own checkers, add their ledger
   (`.omp/rule-coverage.json`) and `omp-guardrails rule-coverage` (or the
   stack's own proof without a reference) to the check-only `checks.ci`, never
   to `checks.commit`. Track no catalogue copy, validator copy or catalogue
   hash. Install the approved pre-commit hook
   executing `checks.commit` through the repo's tracked hook manager when it
   has one, propagating nonzero status. With no manager, commit a native
   `.githooks/pre-commit` and set `git config core.hooksPath .githooks`.
   Installing the hook is part of setup in every clone that commits, including
   agent machines; document the install command. Include path, growth and duplicate checks.
   Budget timing blocks in `checks.commit` using the profile's approved machine limit;
   `checks.ci` never runs the budget checker, since CI never measures or reports test timing.
   Missing, invalid or stale reports fail.
   First limits and raises need owner approval; unapproved machines report only and prompt for a baseline.
   Keep auto-fix explicit and outside check-only CI.
6. Do not configure host branch protection. Rails check that
   `ci.requiredContext` is green on the exact head before merge; a server-side
   ruleset is a later, optional owner step outside this skill.
7. Install positive/negative canaries and checker tests for each repository
   checker near their owner. Update
   contributor and application-user documentation separately. Inspect for
   out-of-scope edits, secrets, and generated output before publication; follow
   existing Git/PR authorization.

## Done when

- Changes match the approved matrix, preserve unrelated work, and remove
  superseded authorities.
- The gate contract, hook, checkers and proof inputs are installed or blocked.
