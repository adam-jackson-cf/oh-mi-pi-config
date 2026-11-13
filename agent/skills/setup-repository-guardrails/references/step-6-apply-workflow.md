# Approved application

## Objective

Write the confirmed record and land only the approved repo fixes, keeping unrelated target work
intact.

## Guidance

1. Recheck target Git status and approved base ref. Use an isolated worktree or
   equivalent reversible checkout when unrelated work or overlapping edits
   exist.
2. Apply only approved paths. Merge surgically or replace only by the owner's
   decision. Remove approved superseded runner/hook policies and any
   omp-specific files instead of retaining duplicate authorities; where
   nothing can be removed, record why the added contract has no predecessor.
   Repo changes land through a normal PR and never name omp, dynaflows or the
   record.
3. Draft the confirmed record in a directory outside the live records root
   and ask the owner to save it by hand as `repos/<owner>/<name>/` under the
   oh-mi-pi-config root (Step 0's path). Agents never write the live record:
   rails refuse every agent edit-tool write there.
   `/repos/` is gitignored: never commit a record, stage it or copy it into
   the target. The device rollout copies records to every device. Where the
   stack has a skill reference, run `omp-guardrails init --repo <worktree>
   --record <draft dir>` from the harness, then write the confirmed profile
   values into the draft; compare it with the approved matrix before handing
   it to the owner. Install no harness
   package into the target. Pin tools the repo itself runs through the target
   package manager. Keep target roots, scope exclusions, allowlists,
   naming/ownership maps, and CI wiring explicit.
4. The record's `gate.json` uses `schemaVersion: 2` and the approved command
   arrays. This example illustrates the exact schema; substitute the repo's
   real commands and test globs (the generic
   [example record](../assets/example-record/README.md) shows the same
   layout):

   ```json
   {
     "schemaVersion": 2,
     "checks": {
       "commit": ["make", "gate-commit"],
       "ci": ["make", "gate-ci"]
     },
     "pathCheck": ["make", "check-paths"],
     "testGlobs": ["**/*.test.ts", "tests/**"],
     "agentMerge": "allowed",
     "ci": { "requiredContext": "gate", "runner": "github-actions" },
     "dynaflows": { "enabled": false }
   }
   ```

   `schemaVersion`, `checks.commit`, `pathCheck` and `dynaflows` are
   required; a record without one fails to load. A field outside this schema,
   at the top level or in `checks`, is rejected by name, never ignored. A docs
   repo still declares both commands; its `pathCheck` just has fewer paths to
   check. Commands are nonempty argv arrays, not shell
   strings, and name the repo's own commands, never an omp path. Checks follow
   the test tiers and run only at these events:

   1. Commit: at the agent's commit, rails run the record's `pathCheck`, its
      `checks.commit` (static checks, every T1 test, and affected T2
      boundary tests: seams and journeys) and then the harness checks over the
      whole repo. Any finding
      blocks the commit until fixed, including drift from contributors who do
      not use omp. No baseline and no diff scoping apply.
   2. Push and pull request: the repo's CI runs `checks.ci`, the full T1 and T2
      suite.
   3. Merge: the declared required context (`ci.requiredContext`, normally
      `gate`) from `checks.ci` must be green on the exact head, whoever posts
      it; with dynaflows on, `prove()` must also have passed on that head.
   4. Schedule: T3 runs on a schedule, outside the record's checks.

   No pre-push hook and no handoff check run exist. CI runs `checks.ci` as one
   job named by `ci.requiredContext` or as parallel tier jobs of the same runner
   whose union equals it. Parallel CI requires an aggregate job/status named by
   `ci.requiredContext` depending on every tier job. Run the aggregate
   unconditionally after dependencies finish, including on failure, and fail
   unless every tier result is `success` (not failed, cancelled, or skipped).
   Append changed paths to `pathCheck` as arguments. It exits 0 when clean;
   nonzero output has one error per line, each naming the file and fix for
   structure, naming, or test placement. Rails run it with one fixed timeout;
   `testGlobs` lists strings. `agentMerge`
   accepts only `allowed` or `disabled`; an absent field or record defaults to
   `allowed`. Do not infer merge permission from this setup skill; honor the
   governing Git authorization. Optional `checks.profile` names the
   profile completeness command (normally `omp-guardrails profile-check`);
   `prove()` does not run it.
   Optional `checks.format` is the formatter `freeze()` runs before hashing
   proofs.

   `dynaflows.enabled` is the repo's dynaflows switch; no `origin` remote or
   no record means off. Write `false` at setup; the owner turns it on once
   the harness checks report zero findings over the whole repo.

   Optional `ci` declares the merge-time CI requirement: `requiredContext` is
   the commit status context or check-run name that must be `success` on the
   exact PR head, and `runner` is a role name. Neither field names a host or an
   account. Each machine resolves the role to submit, wait, status and logs
   commands in its machine-level runner config (`ci-runners.json`, written by
   the device setup step, not by this skill); agents drive it with
   `ci-runner.py <submit|wait> <runner> <OWNER/REPO> <SHA>`. Use the role the
   owner chooses. The `github-actions` role means a GitHub Actions workflow
   runs `checks.ci` on every push and pull request in a job whose name is the
   required context (push triggers it, so there is nothing to submit). Any
   other role is a runner that runs the record's `checks.ci` on the exact SHA
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

5. The record holds every approval and exception. Approval fields
   (`approved_skips`, any `approval`, `approved_*` or `*_approval` field,
   `user_approval`, `house_style.exceptions`, `fast_tier_budget` machine
   approvals and `coverage_ratchet` approvals) are the owner's: the owner
   writes them by hand, and that edit is the approval. Rails refuse every
   agent edit-tool write to the record. Draft them for the owner; never write them.
6. A repo hook is the repo's own control. When the project wants one, run
   `checks.commit` through the repo's tracked hook manager or a native
   `.githooks/pre-commit` enabled with `git config core.hooksPath .githooks`,
   propagating nonzero status. Budget timing blocks only at the agent's commit,
   using the record's approved machine limit; `checks.ci` never runs the
   budget checker, since CI never measures or reports test timing. Missing,
   invalid or stale reports fail. First limits and raises need owner approval;
   unapproved machines report only and prompt for a baseline. Keep auto-fix
   explicit and outside check-only CI.
7. Do not configure host branch protection. Rails check that
   `ci.requiredContext` is green on the exact head before merge; a server-side
   ruleset is a later, optional owner step outside this skill.
8. Tests for the repo's own checkers live near their owner as ordinary repo
   code; harness rules need no repo canaries. Update contributor and
   application-user documentation separately. Inspect for out-of-scope edits,
   secrets, and generated output before publication; follow existing Git/PR
   authorization.

## Done when

- The record matches the confirmed draft; repo changes match the approved
  matrix, preserve unrelated work, and remove superseded authorities.
- The target holds no omp-specific file, pin or mention.
