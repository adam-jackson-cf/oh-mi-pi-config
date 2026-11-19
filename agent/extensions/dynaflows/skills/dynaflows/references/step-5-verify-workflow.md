# Verify

## Objective

Run `prove()` and then `structure_review()` on the PR's exact current head, even with an empty manifest.

## Required actions

1. The valid pathCheck-free schema-v2 record from [Implement](step-4-implement-workflow.md) remains required.
2. Confirm the writer has no pending edits and returned a ready PR as defined in [verify loop](verify-loop.md).
3. Obtain the actual PR base branch and exact 40-hex head from GitHub, using the primary remote and default already
   resolved by `skill://git-tool/references/branch-and-integrate.md`. Fetch the actual target and record its
   remote-tracking ref as `target_ref`. Confirm GitHub's PR target matches; the helper's range check cannot
   authenticate that metadata.
4. Set `parent` to `git merge-base <target_ref> <head>`, never `HEAD~1`; it must be a strict ancestor of head.
5. Call `prove(repo, parent, head, manifest_path, manifest_sha256, objective, target_ref=target_ref)` under the
   [prove contract](prove.md). `repo` is the dedicated source worktree on its named branch at head, not main's
   possibly different checkout. It checks exact-head CI, rename residue, feature-map coverage and the frozen proofs.
6. A PR with no behaviour change (refactor, docs, project setup without new controls, a Dependabot bump) uses a manifest
   frozen with no proofs; with no changed map doc, `prove()` checks CI and rename residue.
7. The result file `<git-common-dir>/omp-prove/<head>.result.json` with `status: pass` for the exact head is the
   prove pass signal; `prove()` also posts it on the PR.
8. On `fail`, return the blocking evidence (commands, output, expected outcome) to the assigned writer.
   Infrastructure failures fail closed; they are not red proof assertions.
9. Call `await structure_review(repo, parent, head, target_ref=target_ref)` on the same range under the
   [structure review contract](structure-review.md). It writes `<git-common-dir>/omp-structure/<head>.json`.
   Provisional findings and recorded infrastructure errors are shadow-only and never affect `prove()` or merge.
   A finding from a `ready` decision sets `halt`; fix it and rerun both helpers on the new head.
10. With dynaflows on, rails' `gh pr merge` guard requires prove `status: pass`, structure `halt: false` and CI green,
    all on that exact head. A missing, corrupt or wrong-head result blocks even with all decisions provisional.
11. Never write or edit a result file by hand. Post no review agent or status; re-freeze only for a discovery or a
    changed proof file.

## Done when

- Both exact-head result files exist with prove `status: pass` and structure `halt: false`, and CI is green on that head.
