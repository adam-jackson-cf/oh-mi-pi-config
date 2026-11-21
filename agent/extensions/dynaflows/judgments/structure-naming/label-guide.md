# Structure and naming label guide

Labels answer the one question named in each case file, from `questions.json`. A labeler sees the
case's `unit_state` and the question; never Jev's answer, a threshold or an earlier label. Label
1 when the condition the question asks about holds (the violation is present), else 0. Cite the
rule that decided the label and give a one-sentence reason.

## Rules per question

Label 1 when the rule holds; the second line names what is not a violation.

- T1 `technical_kind_grouping`: a technical bucket collects unrelated capability owners, or splits
  one capability's code into a technical bucket.
  Not: one capability owner; a coherent shared contract tier; a real adapter boundary.
- F1 `folder_name_coverage`: a substantive responsibility in the files and symbols lies outside
  the folder's named concept.
  Not: internal implementation roles; reasonable domain breadth.
- O1 `overlapping_responsibilities`: two units implement or own the same substantive
  responsibility.
  Not: provider/consumer, stage or interface/implementation splits; shared imports or words alone.
- N1 `one_name_per_concept`: two terms name one concept, shown by definitions, docstrings,
  constants or relationships.
  Not: mechanism vs metric, product vs contract, adapter vs adaptee, third-party names; residue of
  a retired term inside the renamed folder; a word left from a retired compound.
- S1 `sibling_naming_pattern`: comparable sibling roles are named at conflicting semantic levels
  or patterns.
  Not: unrelated roles; differences that express real role differences.
- P1 `prefix_should_be_folder`: the `cluster` prefix names a domain concept and members belong to
  it and change together (imports, a record one writes and another reads, shared readers,
  co-change).
  Not: modifier or coincidental prefixes (`new_`, `base_`, `common_`); members serving different
  owners; a shared prefix or one import edge alone.
- R1 `runtime_data_in_code`: runtime-loaded definitions, configuration, templates, generated
  output or run artifacts sit inside product source.
  Not: source implementations; legitimate test fixtures; file extension alone.
- E1 `entry_point_placement`: a product CLI or API entry point sits below its package top tier,
  or a finished-product consumer sits inside the package it reads.
  Not: independently packaged nested tools; helpers, adapters, test executables.

## Shared rules

- X1. Deterministic checks own banned words, versions or dates, ecosystem casing, a child repeating
  its parent, plurals, test placement and import direction: these never make a label 1.
- X2. Judge only the supplied revision; a rename or commit message is not proof of either name.
- X3. Missing or truncated evidence (`evidence_limits`) is not evidence of absence. When the state
  cannot decide the case, label it `unsure` with the reason instead of guessing.

## Versions

| Version | Change | Scored on |
| --- | --- | --- |
| `@2` | Frozen v2 wording | OpenRouter `typesafe/jev-1.14` |
| `@3` | Rules moved into criteria (Q1, Q4); same meaning | `typesafe/jev-1.14.0` direct |

- Each decision's version key hashes `{question, state, model}`; the aggregate hashes
  `{questions, state, model}` (compact sorted-key JSON). `scores.csv` uses `<question>@<key>`.
- A yes means the named violation is present. Every decision is provisional and shadow-only.

## Labels and exclusions

- Counting labels are the owner's 2026-11-18 bulk confirmation (`provenance: human`), plus SN062
  and SN063 (owner-confirmed 2026-11-19).
- 2026-11-21: the innovation council labelled the 58 scored cases blind to scores and earlier
  labels; Claude arbitrated and appended one `agent_reviewed` row per case (`labeller`,
  `arbiter`, `council_label`). A human label stays the counting label until the owner confirms a
  change; disagreements are listed in [retune-2026-11-21.md](retune-2026-11-21.md).
- SN038, SN040, SN042 and SN044 are decided in code (no eligible cluster), and SN005 is an
  abstention for insufficient evidence: all five stay out of scores.

## Replay base rule

All 61 original IDs and frozen revisions are retained. Source repositories are read through git
objects only; no checkout, imports or execution of their code. `cases.jsonl` stores the
regenerated `state`, its digest, `base`, `rev`, `candidate_base`, original provenance and a
question stratum. All rows are `dev`; related historical pairs are not presented as independent
held-out cases.

- **After** rows: base is the recorded provenance parent, head is the row's frozen revision.
  Changes and candidates come from that actual past diff.
- **Before** rows: base equals head, so rename/retired-term/residue arrays are empty and never
  leak the future regrouping. Candidate extraction uses the empty tree
  (`4b825dc642cb6eb9a060e54bf8d69288fbee4904`) through that frozen revision as a historical
  introduction window. This makes existing candidate clusters observable without inventing future
  additions. It is archived folder evidence, **not a claim that this folder would be selected in a
  PR at that revision**.
- **Current-tree** rows: base equals head, with no future change. These three rows are not prefix
  decisions.

An explicit-unit extractor API regenerates historical units even when they are not destination
folders in a present PR. Production uses only CLI-selected destination folders in the actual
parent..head diff.

### Additional owner-confirmed replay cases

SN062 labels `one_name_per_concept` yes for
`agent/extensions/dynaflows/guardrails/omp_guardrails`; SN063 labels `folder_name_coverage` yes for
`agent/extensions/dynaflows/skills/omp-dyna-workflows/templates`, whose `workflows.py` is a
1,494-line orchestration implementation. Both states come directly from `structure-state` over
`8e757085762fdc47a41fb1a392891880b4bf8334..cc6e079dc394010b415f2267229e5d6119aeb71c`,
with the original extractor caps and no checkout or execution of historical code.

## Deterministic extraction caps

Each compact UTF-8 unit state is at most **48,000 bytes**. The extractor emits every destination
folder without a folder-count cap; each state scans at most 4,096 tracked paths and retains at
most 256 recursive files per unit. Python parsing is capped at 256 KiB per blob and 8 MiB total;
128 symbols and 128 imports per file; module/symbol docstrings at 512 characters. Context retains
24 units with 64 direct files each; each edge partition has 512 rows. Term owners retain 128 terms
with 24 paths each; clusters 32; co-change history 128 commits; change arrays 256 rows.
Evidence-limit rows are capped at 24 with 512-character text.

Caps apply in sorted deterministic order. To satisfy the final byte budget, the extractor
repeatedly halves the largest retained collection, preserving schema fields.
`evidence_limits.caps`, truncation records and `state_budget_omissions` expose incomplete
evidence. Byte bounds do not guarantee sufficient semantic context or model-token fit:
extraction/judge failures are reported as infrastructure errors, not clean judgments. These limits
deliberately stay far below TypeSafe's 32k per-question context and 32 MiB transport limit; large
irrelevant state also degrades accuracy ([model
jaggedness](https://docs.typesafe.ai/model-jaggedness.md)).
