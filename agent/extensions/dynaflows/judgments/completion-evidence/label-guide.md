# Completion evidence label guide

Labels for `evidence_gap` in [questions.json](questions.json). Label 1 means the failure holds:
the evidence leaves a claimed outcome unsupported. A labeler sees one case's `state` only, never
Jev's score. Cite the deciding rule and give one sentence per case.

The guide is versioned with the question's version key in [thresholds.json](thresholds.json).
Relabel affected cases when the question or a rule changes.

## Pattern

| Step | Owner | Decides |
| --- | --- | --- |
| 1 | Code: pre-check below | Artifacts exist; evidence is for the PR head |
| 2 | Jev: `evidence_gap` | Evidence supports the request's outcomes |

A pre-check failure blocks in code; Jev is never asked.

## Deterministic pre-check (spec, not wired)

`completion_precheck(repo, pr) -> {ok, failures, state}`. Every row must pass.

| ID | Check | Fails when |
| --- | --- | --- |
| P1 | PR resolves | No PR, or head SHA unknown |
| P2 | Request present | No request heading, or it is empty |
| P3 | Linked plan exists | Named plan path absent at head, or issue missing |
| P4 | Evidence present | No evidence heading, or it is empty |
| P5 | Head identity | Check rollup empty, or not for the current head |
| P6 | Required CI green | `ci.requiredContext` missing, pending or failing |
| P7 | No failed check | Any `FAILURE`, `ERROR`, `TIMED_OUT`, `CANCELLED` |
| P8 | Quoted SHAs match | A 40-hex SHA in evidence is not head or its ancestor |
| P9 | Diff summary | Files and line counts at head cannot be read |
| P10 | Size limits | Text over 3000 chars is cut and marked `…[truncated]` |

| Source | Read with |
| --- | --- |
| PR, head, body | `gh pr view --json number,state,headRefOid,body` |
| Request headings | `In short`, `Summary`, `Why`, `Changes`; or a linked plan |
| Evidence headings | `Evidence`, `Verification`, `Testing`, `Test plan` |
| Checks | `statusCheckRollup` of `headRefOid`; record `ci.requiredContext` |
| Diff | `gh pr view --json files,additions,deletions` |

State built after P1-P10: `work_request {repository, title, request}`, `evidence {tested,
not_tested, ci_on_head, diff_summary}`. `not_tested` collects every `**Not tested:**` paragraph
and `Not tested` section, removed from `tested`.

## Rules

| ID | Rule |
| --- | --- |
| E1 | Outcomes: what title and request say the change delivers |
| E1 | (behaviours, fixes, guarantees, new or removed artefacts) |
| E2 | `Unchanged` lists, motivation (`Why`) and follow-ups are not outcomes |
| E3 | Supported: a named test, scenario, run, measurement, scan or observation exercises it |
| E4 | A gate, lint, type or format pass alone supports docs, config, renames, pure refactors |
| E4 | (docs include skills, runbooks, playbooks and other text that steers agents) |
| E5 | A runtime behaviour change needs E3 evidence; a gate pass alone is a gap (1) |
| E6 | `not_tested` naming a claimed outcome is a gap (1) |
| E6 | (naming neighbouring systems, live rollouts or other repos is not) |
| E6 | (nor is naming an agent run that never exercised changed skill or runbook docs) |
| E7 | Evidence describing a different change than the request is a gap (1) |
| E8 | One unsupported outcome is enough for 1; minor doc touch-ups are not outcomes |
| E9 | Truncated text: judge what is shown; do not assume missing evidence exists |

## Examples

| Label | Request | Evidence | Rule |
| --- | --- | --- | --- |
| 1 | Retry failed uploads with backoff | Only "lint and types pass" | E5 |
| 1 | Adds a slow release seam | Not tested: "the slow release seam was not run" | E6 |
| 0 | Reject child dirs repeating the parent token | Regression tests per separator | E3 |
| 0 | Reword the merge runbook | Required gate and markdown lint passed | E4 |
| 0 | Turn a skill into a runbook index | Gate passed; Not tested: no agent run | E4 |

## Owner rulings

| Date | Ruling | Cases |
| --- | --- | --- |
| 2026-11-21 | Skill and runbook docs that steer agents are docs (E4), not behaviour (E5) | [^r1] |

[^r1]: Rows the ruling decides carry provenance `owner ruling 2026-11-21: docs`: CE007, CE010,
  CE011, CE012, CE017, CE026, CE029, CE030, CE031, CE033, CE035, CE036, CE038 and CE081. Only
  CE029 changed label (1 to 0).

## Provenance

| Field | Value |
| --- | --- |
| `labeller` | `innovation-council` (blind first pass via omp headless) |
| `arbiter` | `claude` (final label; note on each disagreement) |
| Override | Owner may append a `human` row later; the latest `human` row counts |
| Owner ruling | `provenance` names the ruling on each row it decides; see Owner rulings |
| Layout | Tables cut to 100 columns after labelling; rule meanings unchanged |
