# Rule verification

## Objective

Prove that no applicable rule is merely named, skipped or disconnected from execution.

## Required actions

1. Run `python3 <skill>/scripts/validate_rule_coverage.py --root <worktree> --catalog <skill>/assets/guardrail-rules.json
   --live-host-query`. Expect exit 0 printing `proofs PASS` or `proofs PASS_WITH_APPROVED_EXCLUSIONS`. It rejects a catalogue rule
   with no claim or more than one, an excluded `always` rule, a claim lacking the hook, CI, later or host point its evidence kind needs,
   unknown patterns or keys, an invalid scope, unapproved or unevidenced exclusions, missing control paths, enforcement points whose
   file lacks the `ref`, failed canaries or hook-installation checks, a host query naming another repository, and host protection
   missing any claim's CI status or the validator status. `--structure-only` is planning evidence, never final proof.
   Run the repo's shipped copy with `--root .` (no catalogue) the same way.
2. Run positive and negative canaries for every applicable `local_ci`, `git_hook` and `later_job` ID. For `STRUCT.dependency-direction`, allow a legal
   edge and reject a reversed or unmapped edge. For `STRUCT.folder-placement`, accept an owned path and reject a misplaced or unmapped
   file. For `NAME.conventions`, accept valid path and symbol names and reject invalid ones. For `TEST.placement`, accept an owner/tier
   path and reject a misplaced test. Assert that each failure names the file and the exact corrective action.
3. Feed the real budget checker valid below-limit, exactly-at-limit and over-limit run reports; confirm missing, invalid and stale reports
   and missing measurements fail. Prove machine selection against the approved limits; an unapproved machine reports timing and prompts
   for a baseline. For `TEST.growth.justified`, accept a new test with a descriptive behaviour name and reject a generic one with the
   rename-or-remove fix. For `TEST.duplicate-path`, accept distinct fingerprints and reject a duplicate, naming both tests.
4. Prove `GATE.enforced` in an isolated checkout: the hook is installed, a compliant change permits the commit and a violating one
   blocks it with the same status. Prove `GATE.parity` with the actual wiring: every point of a claim invokes the same entrypoint;
   for parallel CI, the aggregate depends on every tier and fails on a failed, cancelled or skipped tier. Prove `GATE.self-check`:
   the validator runs as its own CI job posting the `[validator]` status. Never test by pushing to a protected branch.
5. Test the `GIT.required-checks` claim with `--live-host-query` (credentials that can read branch protection): the query runs live
   and names every required status. An unsupported setting needs an approved exclusion whose `host = true` command evidence shows the host's refusal.
6. Verify tool selectors, limits, exceptions and pinned versions against [tooling families](tooling-families.md). Run focused checker tests,
   the hook entrypoint, the CI entrypoint (or its tier jobs) and the validator. In an isolated canary environment, confirm that
   disabling or corrupting a checker, or deleting its `ref` from a point file, makes the validator fail. Skipped, unavailable or
   timed-out checks are not passes. Observe actual CI on the final approved commit when approved. Run the dependency audit through
   its enforcement points; when it blocks, follow [dependency audit](dependency-audit.md) "Responding to findings" and never add an
   exception yourself.
7. For a `changed` or `paths` scope, confirm with `python3 <skill>/scripts/list_scope.py` that the gate covers exactly the approved scope,
   and report the remaining whole-repository findings as open work, not as passes.
8. Confirm `git status` shows no candidate, report or temporary output in the target, that `.proofs/` is excluded, and that nothing the
   change added requires or names this skill, its scripts' skill paths or agent tooling: every shipped file runs from the repo alone.
   Rerun the validator after repairs; compare final paths with the approved dry-run matrix. Confirm superseded contracts were removed
   or record why nothing could be removed.
9. Finish the run state ([run state](run-state.md)): no `pending` decision or placeholder remains, and every `blocked` decision is
   reported as a blocker; delete `inventory`, `dry_run` and
   `proposal`; keep the rest for the next run. Return the result format with rule IDs, scope, commands and results, change counts,
   documentation changes, kept run state and blockers.

## Done when

- Every rule the approved tier enables is implemented, invoked and proven. The `--catalog` run fails only on the rules Step 5
  recorded as left unenforced by that tier, each reported as a blocker, never as green.
- The validator passes with live host queries; hook, CI and validator points, parity, host protection and approved scope pass or have
  concrete blockers.
