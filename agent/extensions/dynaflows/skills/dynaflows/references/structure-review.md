# Structure review contract

## Signature

```python
proof = prove(repo, parent, head, manifest_path, digest, objective, target_ref=target_ref)
structure = await structure_review(repo, parent, head, target_ref=target_ref)
# Rails require both exact-head files before git-tool's merge contract.
```

Main calls this after prove on the same exact head and merge-base range. The extractor runs
isolated Python (`-I -S`) from the harness reference:
`structure-state --repo R --base SHA --head SHA`. It reads Git objects only, never imports
or executes target-repository code, and emits `{unit_path: unit_state}` for affected units.
See [ADR 0003](../../../../../../docs/adr/0003-structure-checks-at-pr-diff-scope.md).

The frozen questions and per-question thresholds live in
`judgments/structure-naming/{questions,thresholds}.json`. Ask all seven general questions
for every affected unit and `prefix_should_be_folder` only with eligible clusters. Independent
questions share a request; multiple units sharing those questions use eval `judge_batch`.
Threshold versions hash questions, state shape and the recorded model; stale or invalid
configuration is an infrastructure error, not a clean review. Eval currently selects the
runtime model and does not expose its reported model in `judge` answers; results explicitly
record this limitation rather than claiming a pinned model or calibrated confidence.

The JSON result at `<git-common-dir>/omp-structure/<head>.json` contains `head`, `status`,
unit-state digests, raw scores, findings, full thresholds and version key, `halt`, and
infrastructure `errors`.
One marked PR comment is updated across reruns/heads. Provisional findings (initial threshold
0.7 for every question) are shadow-only: no prove file, proof status or merge signal changes.
Infrastructure failures are reported in the result/comment and never block shadow operation.
An incomplete review has `status: incomplete`, not a clean result.

A finding for a decision explicitly marked `ready` sets `halt: true` and `status: blocked`.
With dynaflows on, rails require this exact-head file alongside prove pass and CI green.
A missing, corrupt or wrong-head file blocks even when all decisions are provisional.
`halt: true` blocks via rails: fix the named ready findings and rerun `prove()` and
`structure_review()` on the new head. Never write or edit a result file by hand.
Promoting thresholds requires jev-actions labeling, tuning and calibration evidence; raw
probabilities are not calibrated confidence. No automatic promotion or infrastructure-to-finding
conversion occurs.
