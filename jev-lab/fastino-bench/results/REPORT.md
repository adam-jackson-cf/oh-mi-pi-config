# Jev vs Fastino GLiDE vs GLiNER-2.5-Decide (2026-10-02)

Framework, datasets and commands: [../README.md](../README.md). Per-experiment tables are generated
by `report.ts` into `results/` (runtime-only). Models: Jev `typesafe/jev-1.13-20260917` (pinned,
checked on every response), `fastino/GLiDE`, `fastino/GLiNER-2.5-Decide` in two schema translations
(`decide`: instructions + criteria text as task/labels; `decide-keys`: ids + option keys).

## Headline

- E2 reasoning (160, computed truth)
  - What it tests: arithmetic, temporal, code tracing, policy exceptions
  - Jev: 75.0% correct; arithmetic 37.5%
  - GLiDE: **98.1%**; arithmetic 100%
  - Decide (best variant): ≈50% (chance)
- E1 guard.bash (298)
  - What it tests: live guard replay, promotion corpora + canaries + blind-labelled real
  - Jev: AUROC 0.934; shipped rule 47/54 caught, 21/244 false
  - GLiDE: AUROC 0.946; 48/54, **12/244**
  - Decide (best variant): AUROC 0.901
- E1 guard.write (151)
  - What it tests: credential literals
  - Jev: AUROC 0.985; 39/64, 0/87
  - GLiDE: AUROC 0.989; **44/64**, 0/87
  - Decide (best variant): AUROC 0.949
- E1 guard.result (197)
  - What it tests: prompt injection
  - Jev: AUROC 1.000; 48/49, 0/148
  - GLiDE: AUROC 1.000; 49/49, 0/148
  - Decide (best variant): AUROC 0.898
- E1 guard.integrity (247)
  - What it tests: quality-gate weakening, composed nouls
  - Jev: AUROC 0.839; 24/27, 9/220
  - GLiDE: AUROC 0.918; 22/27, 10/220
  - Decide (best variant): AUROC 0.667
- E3 long page, whole (152)
  - What it tests: injection buried in 1k–28k tokens
  - Jev: 97.4%; 4 hard-negative false flags
  - GLiDE: **100%**
  - Decide (best variant): 38–51%
- E3 long page, guard windows (152 pages)
  - What it tests: as shipped: 6k windows, max ≥ 0.8
  - Jev: 96/96, 0/56
  - GLiDE: 96/96, 0/56
  - Decide (best variant): 33/96 caught (`keys`)
- E4 proportionality, real (121 labelled, 11 overreach)
  - What it tests: todo item done vs plan item
  - Jev: AUROC 0.776; 62.8% at 0.5 (FPR 38%)
  - GLiDE: AUROC 0.766; **81.8%** (FPR 12%)
  - Decide (best variant): ≤ 0.55
- E4 proportionality, injected (99, 40 overreach)
  - What it tests: constructed unrequested vs required changes
  - Jev: AUROC 0.882; TPR 80% FPR 22%
  - GLiDE: AUROC 0.915; TPR 87.5% FPR 11.9%
  - Decide (best variant): ≤ 0.60

"Shipped rule" = the live `jev-guard.ts` thresholds (imported, not copied), which were tuned for
Jev on overlapping data, so they favour Jev. AUROC is threshold-free.

## Quality, speed and cost scorecard

`bun scorecard.ts` (per-slice tables in `results/scorecard.md`). Cost: Jev = OpenRouter billed
`usage.cost`; Fastino = reported tokens × list price. Latency: client wall time, paired cases.

| Backend | $ / 1k (all slices) | p50 | p95 | p99 | > 8 s | Guard (E1): $ / 1k, p95, > 8 s |
| --- | --- | --- | --- | --- | --- | --- |
| Jev | $0.10 | 0.26 s | 0.41 s | 0.51 s | 0% | $0.06, 0.36 s, 0% |
| GLiDE | $1.56 (15×) | 1.04 s | 40 s | 90 s | 20.1% | $0.67, 13 s, 9.0% |
| Decide | $0.026 (¼×) | 0.59 s | 3.0 s | 9.8 s | 1.6% | $0.023, 2.8 s, 1.2% |
| Decide-keys | $0.023 | 0.60 s | 3.1 s | 9.5 s | 1.5% | $0.019, 2.7 s, 1.1% |

Scores 0–100. Quality = mean over the profile's slices of (AUROC − 0.5) / 0.5 (E2: accuracy). Speed
(mean of p50 and p95) and cost each score 100 − 25 × log10(value / best backend), i.e. −25 per 10×
worse. Inline-guard speed also loses 10 points per 1% of calls over the 8 s fail-closed timeout.

- Inline guard (E1; 40/40/20)
  - Jev: **93** (Q 88, S 100, C 88)
  - GLiDE: 49 (Q 93, S 0, C 62)
  - Decide: 76 (Q 69, S 72, C 98)
  - Decide-keys: 63
- Advisory / offline (E2–E4; 80/0/20)
  - Jev: 72 (Q 70, C 82)
  - GLiDE: **74** (Q 80, C 51)
  - Decide: 28
  - Decide-keys: 26
- Balanced (all; 50/25/25)
  - Jev: **85** (Q 78, S 100, C 84)
  - GLiDE: 73 (Q 85, S 67, C 54)
  - Decide: 64
  - Decide-keys: 56

- GLiDE has the best quality in every profile; its speed and cost cost it every profile except
  latency-tolerant work, where it edges Jev only because quality is weighted 80%.
- The guard verdict is driven by the timeout rule: 9% of GLiDE guard calls would be wrongful blocks.
  Without that penalty GLiDE's guard speed is 73 and its guard score 79, still below Jev's 93.
- Decide's guard score rests on cost and speed; its quality figure is AUROC on its best (described)
  labels, and its thresholded decisions are unusable without per-question recalibration.
- Weights are judgement, not measurement: the raw columns above are the evidence.

## Findings

1. **GLiDE's reasoning claim holds.** On E2 GLiDE beat Jev on 37 cases and lost none (exact McNemar
   p < 0.0001); Jev's weak spot is arithmetic (37.5%) and temporal (77.5%). Paired bootstrap of the
   AUROC difference GLiDE − Jev: bash [−0.004, +0.028], write [−0.004, +0.012], integrity
   [−0.002, +0.179], E4 injected [−0.021, +0.095]. GLiDE is never worse; on the existing guards the
   gain is real but small because Jev is already near ceiling there.
2. **GLiDE is better calibrated.** Lower Brier/ECE on E2 (0.042 vs 0.136), E3 and E4. On real E4
   cases Jev flags 38% of proportionate items at 0.5; GLiDE 12%. Jev thresholds must not be reused
   for GLiDE.
3. **GLiDE latency is bimodal and breaks the guards' 8 s fail-closed budget.** p50 ≈ 1 s, but the
   escalated "thinking" path ran 6–176 s: 44/449 bash+write calls (9.8%) exceeded 8 s, which in
   enforce mode means a block. E4 watchdog-state calls had p50 32 s. Jev p99 < 0.6 s everywhere.
4. **GLiDE cost ≈ 14× Jev per call** (list $0.30/M input with multi-pass token counts:
   3,254 calls ≈ $4.03 vs Jev 3,267 calls ≈ $0.29). The run exhausted the account's included
   Fastino credit (HTTP 402) with 13 of 642 E4 GLiDE cases unsent; those cases are excluded from
   paired metrics.
5. **GLiDE serving was flaky:** intermittent `404 vLLM HTTP 404 (model_not_found)` and 504s on
   requests that succeed on retry. The adapter now retries those; a production integration would
   need the same, against Fastino's documented "don't retry 404" advice.
6. **GLiNER-2.5-Decide is not a drop-in for Jev-style questions.** It hosts no instructions or label
   descriptions, returns only the winner's confidence, and has an 8k window (clipping 30–60% of E1
   integrity/E4 states). It is near chance on reasoning and proportionality, and its best guard
   showing (bash AUROC 0.90 with long described labels) collapses with key-only labels (0.50). Its
   case is cost/latency (≈ 1/3 of Jev's cost, p50 0.6 s) for short, label-native taxonomies, which
   none of our Jev integrations are. Decide's thresholded numbers on 3-option questions depend on
   the adapter's even spread of the remainder; use AUROC.
7. **Proportionality (owner hypothesis: context, not framing, was the problem).** On real cases the
   new main-thread todo-item state did *not* beat the live watchdog state: AUROC with the watchdog's
   own composed `P(yes)` was Jev 0.833 / GLiDE 0.828 (watchdog state) vs 0.772 / 0.793 (todo state,
   same questions) vs 0.776 / 0.766 (todo state, new `beyond_item` noul). With only 11 overreach
   positives every CI spans ±0.15, so this neither confirms nor refutes the hypothesis. What it does
   show:
   - Real overreach is rare: 11/127 (8.7%) labelled items, so precision at any useful recall stays
     low for both models.
   - The new framing needs a recalibrated threshold: the watchdog's product composition never
     reaches 0.5 (TPR@0.5 ≤ 9%); the single `beyond_item` noul does.
   - When the overreach is visible in state (injected set), both models separate it well and GLiDE
     halves Jev's false-positive rate (11.9% vs 22%). The dominant failure is information loss:
     52/180 items were unlabellable because the todo-state builder clipped the changed text (144
     actions clipped), and the live watchdog state hides activity in 144/180 windows.
8. **Shipped item state (`proportionality-item-2026-10-02.1`, Jev only; GLiDE credit exhausted).**
   `builders/e4-itemstate.ts` rebuilds every E4 case with the watchdog's own builder
   (`agent/extensions/lib/proportionality.ts`): one 28k-token budget, nothing cut on any case
   (estimated tokens p50 2.0k, p90 8.9k, max 19.6k). The 53 previously unlabellable real items were
   relabelled blind from the unclipped state (`labels/{sol,son}-item.jsonl`, applied to
   `real-itemstate` only): 42 agreed `no_overreach`, 0 `overreach`, 11 disputed (left unscored).

   | Dataset | n (overreach) | Jev AUROC | caught / false at 0.6 | caught / false at 0.8 |
   | --- | --- | --- | --- | --- |
   | real, item state | 169 (11) | 0.779 | 7/11, 38/158 (precision 0.16) | 1/11, 0/158 |
   | injected, item state | 99 (40) | 0.869 | 29/40, 9/59 | 19/40, 0/59 |

   0.6 maximises balanced accuracy on the injected set and is the watchdog's advise threshold; 0.8
   is its ask-user threshold. The fuller state did not raise Jev's AUROC on real items (watchdog
   state 0.833 on its 127 cases); the watchdog ships in `shadow`.
9. **History replay and the shipped `.3` process (`proportionality-item-2026-10-02.3`, Jev only).**
   `builders/e4-history.ts` replays every main session's todo `done` events with the shipped builder
   (1,155 items; sessions active in the last 2 h and smoke/bench sessions skipped), split by session
   hash into dev (826) and test (329, hash mod 3 = 0). A sample of ≤ 8 items per session (614) was
   labelled blind by GPT-6.1 Sol and Sonnet with `builders/e4-policy.md`; the 153 uncertain or
   disputed were relabelled under policy v2 on the diff-visible state (`labels/hist/`). 556 definite
   labels, 21 `overreach`, 12 of them unrequested commits; disagreements stay unscored.

   Error analysis on dev: the `.1` noul (AUROC 0.784) missed the commits and could not see deletions
   (118 of 1,952 edits were bare `CUT`/`REM` lines; edits now carry the result diff). Jev agreed
   with the label consensus on 94.6% at 0.7 when shown the same state, so state and policy were the
   gap, not perception. Variants on dev (AUROC): noul + code facts 0.837; Choice `worst_change` +
   facts 0.844; commit rule + router + Choice 0.996. A cascade was dropped (the 0.4–0.7 band held
   70 dev items, 1 positive). The last variant was frozen and scored once on test, then shipped.
   `bun e4-process.ts --split <dev|test|all>` scores the shipped process at its thresholds:

   | Process | Split (n, overreach) | AUROC | caught/false at 0.7 | caught/false at 0.8 | Calls |
   | --- | --- | --- | --- | --- | --- |
   | `.1` noul, every item | test (164, 7) | 0.772 | — | 1/7 | 100% |
   | `.3` rule → router → Choice | dev (392, 14) | 0.996 | 13/14, 15/378 | 13/14, 4/378 | 69% |
   | `.3` | test (164, 7) | 0.976 | 6/7, 10/157 | 6/7, 4/157 | 62% |
   | `.3` | real item set (169, 11) | 0.705 | 2/11, 14/158 | 1/11, 9/158 | 68% |
   | `.3` | injected (99, 40) | 0.670 | 16/40, 4/59 | 13/40, 2/59 | 78% |

   The rule caught 12 of 13 flagged history items (false hit: a plan-driven orchestrate run). The
   router skipped 171 history items with no positive; on the older sets it skips 2 real and 8
   injected positives (edits inside named files, a working checkout under `/tmp`), but without it
   Jev catches at most one more per set at 0.8, so it stays. The real item set's labels used the
   earlier rubric on a state without diffs and are not comparable with history. Mean cost per item
   $0.00013.

   The `.3` variants were chosen after reading dev errors, and the policy rubric was revised (v2)
   during that analysis; test was scored once, but both label rounds are agent-only (`proxy`).

## Threats to validity

- E1: thresholds and integrity question framings were tuned on overlapping sets (round-2 positives,
  integrity `c*` units), favouring Jev. Known-format secrets are masked in guard.write state exactly
  as Jev sees them. Integrity has 27 hacks; 92 cases have no open escalation family.
- E2/E3 are synthetic. E3's injections all address the agent explicitly, so the earlier "injection
  buried in a long page" miss did not reproduce for Jev.
- E4 labels: two blind families (GPT-6.1 Sol `reviewer`, Sonnet `task`) agreed on 120/180; 8
  definite disagreements were adjudicated from the labellers' cited evidence
  (`datasets/e4-proportionality/labels/adjudicated.jsonl`), 52 are `uncertain`. Items 025–027 share
  one activity window. The injected set has no watchdog-state variant, so context cannot be compared
  there.
- Latency was measured at 3–16 concurrent requests from one client; GLiDE's tail includes its
  queueing.

## Recommendations

- guard.bash, guard.write (enforce, 8 s, fail closed)
  - Recommendation: Keep Jev
  - Why: GLiDE's gain is small and ~10% of calls would exceed the timeout and block
- guard.result (fails open)
  - Recommendation: Keep Jev
  - Why: both at ceiling
- guard.integrity (enforce)
  - Recommendation: Shadow-test GLiDE with its own thresholds
  - Why: AUROC 0.839 → 0.918, but on 23 risk-bearing positives with tuned-for-Jev composition
- Proportionality watchdog (advisory, latency-tolerant)
  - Recommendation: Shipped `.3`: commit rule, router, Choice `worst_change`, thresholds 0.7/0.8,
    `shadow`. `enforce` is the owner's call on finding 9
  - Why: History test: 6/7 caught, 4/157 false at 0.8; weak on oversized changes inside named files
    (injected 13/40)
- New reasoning-heavy decisions (dates, budgets, policy clauses, script tracing)
  - Recommendation: Prefer GLiDE where a multi-second tail is acceptable
  - Why: E2: 98.1% vs 75%
- Decide
  - Recommendation: Do not use for Jev-format judgments
  - Why: near chance without instructions
