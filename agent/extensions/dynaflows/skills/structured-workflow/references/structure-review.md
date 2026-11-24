# Structure review contract

## Signature

```python
proof = prove(
    repo, parent, head, (manifest_path, digest), objective, target_ref=target_ref
)
structure = await structure_review(repo, parent, head, target_ref=target_ref)
# Rails require both exact-head files before git-tool's merge contract.
```

Main calls this after prove on the same exact head and merge-base range. Every proposer runs
isolated Python (`-I -S`) from the harness reference, reads Git objects only and never imports or
executes target-repository code. No question judges a whole folder: each asks about one proposed
file or one proposed pair of names.

The frozen questions and per-question thresholds live in
`judgments/structure-naming/{questions,thresholds}.json`. Prefix clusters are a deterministic
prove() check (STRUCT.prefix-folder), not a question.

Grouping by technical kind is judged per file and runs in shadow mode: its flags are logged for
review and never block. `kind-candidates` (same launch and record) proposes every `.py` file with a
top-level definition in a technical-kind folder (`models`, `utils`, `services` and the like),
added, modified or renamed in the range, and drops files a record exception with rule
`structure-naming/technical_kind_grouping` covers. Each file asks the `technical_kind_grouping`
question (sent as `own_capability`) once; its score is the probability, compared with the
decision's threshold.

One name per concept is judged per name pair. `name-pairs --repo R [--record D] --base SHA
--head SHA` (same isolated launch) proposes pairs by code (alias, head noun, synonym, constant
value, clone) where at least one name is defined in an added, modified or renamed file, and drops
pairs a record `guardrails.exceptions` entry with rule `structure-naming/same_thing` covers on
either name's file. Each pair asks `same_thing` in both orders (first and second swapped) in one
`judge_batch`; its score is the pair's `det_score` times the mean probability, compared with the
`same_thing` threshold.

A finished-product consumer placed inside its package is judged per runnable file, in shadow mode.
`consumer-candidates` (same launch and record) proposes package files with a run marker that no
other file imports and no non-test file or console-script entry names by path or module string,
added, modified or renamed in the range, and drops files a record exception
with rule `structure-naming/consumer_placement` covers. Each file asks the `consumer_placement`
question (sent as the `kind` Choice) in both option orders; its score is the mean probability of
`finished_tool`, compared with the decision's threshold.
Each question runs at its effective mode for the repo's config ([judgments](judgments.md)): an off
question runs neither its proposer nor the judge and logs nothing, and a question the config sets to
shadow never blocks.
The questions load through the [judgment registry](judgments.md): stale version keys, a judge role
other than the tuned model, or `TYPESAFE_BASE_URL` set (the Jev Inspector route) are
infrastructure errors, not a clean review. Every proposal's answer is written to the decision store
as a `decisions` row (trigger `pr`, the file or pair as the item, its combined score). Never claim
calibrated confidence: raw probabilities are not confidence.

The JSON result at `<git-common-dir>/omp-structure/<head>.json` contains `head`, `status`,
per-file and per-pair state digests, scores and outcomes (`pairs`, `kind_files`, `consumers`), findings
(each naming its file or pair as `item`),
full thresholds and version key, `halt`, and infrastructure `errors`.
One marked PR comment is updated across reruns/heads. A finding is a `yes` outcome (score at or
above the decision's `threshold`) and carries its decision's `mode`. No prove file or proof status
changes.
Infrastructure failures are reported in the result/comment. An incomplete review has
`status: incomplete`, not a clean result, and rails refuse a merge on it: fix the cause and rerun.

Every enforced finding sets `halt: true` and `status: blocked`; a review with no enforced finding is
`status: pass`. A shadow finding is listed as "shadow, not blocking" in the result and the PR comment
and never halts or blocks the merge.
[Verify](step-5-verify-workflow.md) and [Integrate](step-6-integrate-workflow.md) own the response.
Retuning thresholds requires jev-actions labeling, tuning and calibration evidence; raw
probabilities are not calibrated confidence. Nothing turns infrastructure errors into findings.
