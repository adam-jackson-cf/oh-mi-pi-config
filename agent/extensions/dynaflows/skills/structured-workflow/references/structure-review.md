# Structure review contract

## Signature

```python
proof = prove(
    repo, parent, head, (manifest_path, digest), objective, target_ref=target_ref
)
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
The questions load through the [judgment registry](judgments.md): stale version keys, a judge role
other than the tuned model, or `TYPESAFE_BASE_URL` set (the Jev Inspector route) are
infrastructure errors, not a clean review. Every unit's answers are appended to the judgment call
log. Never claim calibrated confidence: raw probabilities are not confidence.

The JSON result at `<git-common-dir>/omp-structure/<head>.json` contains `head`, `status`,
unit-state digests, raw scores, per-question outcomes (`yes`, `no`, `escalate`), findings,
full thresholds and version key, `halt`, and infrastructure `errors`.
One marked PR comment is updated across reruns/heads. A finding is a `yes` outcome (score at or
above the decision's `yes_at_least`). Provisional findings are shadow-only: no prove file, proof
status or merge signal changes.
Infrastructure failures are reported in the result/comment and never block shadow operation.
An incomplete review has `status: incomplete`, not a clean result.

A finding for a decision explicitly marked `ready` sets `halt: true` and `status: blocked`;
[Verify](step-5-verify-workflow.md) and [Integrate](step-6-integrate-workflow.md) own the response.
Promoting thresholds requires jev-actions labeling, tuning and calibration evidence; raw
probabilities are not calibrated confidence. Nothing promotes thresholds or turns infrastructure
errors into findings automatically.
