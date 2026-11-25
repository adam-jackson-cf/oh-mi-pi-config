# Approved application

## Objective

Land only the approved controls and fixes, keeping unrelated target work intact and leaving one authority per rule.

## Required actions

1. Recheck target Git status and the approved base ref. Use an isolated worktree or equivalent reversible checkout when unrelated
   work or overlapping edits exist. Re-run `validate_rule_coverage.py --structure-only` from the current skill on the approved
   candidate; when the skill changed since Step 5 and the candidate no longer passes, fix it and show the owner the difference.
2. Apply only approved paths from `state.json` `proposal` for the chosen tier. Merge surgically, or replace only by the
   owner's decision. Remove approved superseded runner and hook policies instead of retaining duplicate authorities; where nothing can
   be removed, record why the added contract has no predecessor. Never remove an owning checker in favour of a duplicate.
3. Land `grow` changes to existing checkers first, through normal PRs with green checks, each with tests for every added rule.
   A `grow` change that needs `proofs.toml` to pass (for example a new dependency audit whose fixture exclusions live there) lands
   in the `add` change instead, and `grow_changes` records that.
   A target with no hosting remote takes `grow` changes as separate local commits instead. Setup is not complete until every prerequisite
   change has landed and `proofs.toml` names every owning checker; track each in `state.json` `grow_changes`.
4. Pin tools and install only approved dependencies through the target's package manager; the manifest and lock-file changes that
   installation produces are part of the approved install. Put each value at its native home (tool, test-runner, coverage and hook
   configs, CI workflow, package manifest, `CODEOWNERS`); put only values with no native home in `proofs.toml`. No enforcement
   value lives in `.proofs/` or outside the repo.
5. Save the approved `proofs.toml` at the repo root. Write approval fields only as the owner stated them.
6. Wire the enforcement points per [gate wiring](gate-wiring.md):
   - hook: an installed hook runs the check entrypoint (static checks, structure checks on added and renamed paths, every T1 test,
     affected T2 boundary tests) through the repo's tracked hook manager, or a committed native `.githooks/pre-commit` enabled with
     `git config core.hooksPath .githooks`; document the install command for every clone;
   - CI: every push and pull request runs the same entrypoint plus the full T1 and T2 suite, as one job or parallel tier jobs with an
     always-running aggregate that fails on any failed, cancelled or skipped tier; each job's status is the claim's `status`;
   - validator: its own CI job runs `python3 <path>/validate_rule_coverage.py --root .` and posts the `[validator]` status;
     a tier with no CI point omits `[validator]` and Step 5 records `GATE.self-check` as unenforced;
   - later jobs: T3 and deferred T2 tests per [gate wiring](gate-wiring.md) event 4, as the owner approved at Step 5; none
     without such tests.
   When a tier's selector or command changes, update every instruction file and installed agent skill that states the old
   one in the same change, as an approved Step 5 path.
   The Full tier wires a hook and CI point even in a documentation-only repository. Budget timing blocks only the local hook;
   CI never measures timing.
   Keep auto-fix explicit and outside check-only points.
7. Ship the validator at an approved repo path and file name (the repo's own naming and wrapper rules decide the name; every
   reference uses it); never ship the catalogue or anything from `.proofs/`. The repo's own formatter may
   reformat the shipped copy; behaviour, not bytes, must match the skill copy, so run its `--self-test` after formatting.
   Include budget, growth and duplicate checks in the appropriate runner tiers.
8. Configure approved protection on the resolved default branch to require every claim's CI status and the validator status;
   enable auto-merge and rebase-merge where the host supports them. Record the read-back query as the `host` claim and run it live.
   Unsupported capability or permission is an explicit blocker or an evidence-backed approved exclusion, never a fabricated
   successful configuration. Never change protection to make a canary pass.
9. Draft owner review (for example CODEOWNERS entries naming the owner) for `proofs.toml`, the validator, CI workflow files and every
   owner-only path, including the dependency-audit exception file. Write it only when the owner approved those exact entries and
   the owner identity at Step 5; otherwise it stays a named blocker. Once protected, agents never edit owner-only paths or their
   protection ([dependency audit](dependency-audit.md)).
10. Install positive and negative canaries and checker tests near their owner as ordinary repo code. Edit contributor and
    application-user documentation only at the Step 5 approved paths, separately; ledgers, changelogs and decision logs
    change only where the owner approved that entry. No written file names a path outside the repository. Inspect the
    change for out-of-scope edits, secrets and generated output before publication, and follow the existing Git and PR
    authorization; do not infer merge permission from this skill.

## Done when

- Changes match the approved matrix and tier, preserve unrelated work and remove superseded authorities.
- `proofs.toml`, hooks, checkers, CI points, the validator job and host protection are installed, or each is a named blocker.
