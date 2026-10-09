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

"Shipped rule" = the live `jev-guard.ts` thresholds (imported, not copied), which were tuned for
Jev on overlapping data, so they favour Jev. AUROC is threshold-free.

## Quality, speed and cost scorecard

`bun scorecard.ts` (per-slice tables in `results/scorecard.md`). Cost: Jev = OpenRouter billed
`usage.cost`; Fastino = reported tokens × list price. Latency: client wall time, paired cases.

| Backend | $ / 1k (all slices) | p50 | p95 | p99 | > 8 s | Guard (E1): $ / 1k, p95, > 8 s |
| --- | --- | --- | --- | --- | --- | --- |
| Jev | $0.10 | 0.26 s | 0.42 s | 0.51 s | 0% | $0.06, 0.36 s, 0% |
| GLiDE | $0.91 (9×) | 0.97 s | 10 s | 41 s | 8.1% | $0.67, 13 s, 9.0% |
| Decide | $0.023 (¼×) | 0.58 s | 2.8 s | 8.3 s | 1.2% | $0.023, 2.8 s, 1.2% |
| Decide-keys | $0.020 | 0.58 s | 2.8 s | 8.0 s | 1.1% | $0.019, 2.7 s, 1.1% |

Scores 0–100. Quality = mean over the profile's slices of (AUROC − 0.5) / 0.5 (E2: accuracy). Speed
(mean of p50 and p95) and cost each score 100 − 25 × log10(value / best backend), i.e. −25 per 10×
worse. Inline-guard speed also loses 10 points per 1% of calls over the 8 s fail-closed timeout.

- Inline guard (E1; 40/40/20)
  - Jev: **93** (Q 88, S 100, C 88)
  - GLiDE: 49 (Q 93, S 0, C 62)
  - Decide: 76 (Q 69, S 72, C 98)
  - Decide-keys: 63
- Advisory / offline (E2–E3; 80/0/20)
  - Jev: 75 (Q 75, C 75)
  - GLiDE: **89** (Q 98, C 53)
  - Decide: 27
  - Decide-keys: 31
- Balanced (all; 50/25/25)
  - Jev: **87** (Q 84, S 100, C 82)
  - GLiDE: 81 (Q 94, S 75, C 59)
  - Decide: 70
  - Decide-keys: 60

- GLiDE has the best quality in every profile; its speed and cost cost it every profile except
  latency-tolerant work (E2–E3), where it leads 89 to 75 because quality is weighted 80%.
- The guard verdict is driven by the timeout rule: 9% of GLiDE guard calls would be wrongful blocks.
  Without that penalty GLiDE's guard speed is 73 and its guard score 79, still below Jev's 93.
- Decide's guard score rests on cost and speed; its quality figure is AUROC on its best (described)
  labels, and its thresholded decisions are unusable without per-question recalibration.
- Weights are judgement, not measurement: the raw columns above are the evidence.

## Findings

1. **GLiDE's reasoning claim holds.** On E2 GLiDE beat Jev on 37 cases and lost none (exact McNemar
   p < 0.0001); Jev's weak spot is arithmetic (37.5%) and temporal (77.5%). Paired bootstrap of the
   AUROC difference GLiDE − Jev: bash [−0.004, +0.028], write [−0.004, +0.012], integrity
   [−0.002, +0.179]. GLiDE is never worse; on the existing guards the gain is real but small because
   Jev is already near ceiling there.
2. **GLiDE is better calibrated.** Lower Brier/ECE on E2 (0.042 vs 0.136) and E3. Jev thresholds must
   not be reused for GLiDE.
3. **GLiDE latency is bimodal and breaks the guards' 8 s fail-closed budget.** p50 ≈ 1 s, but the
   escalated "thinking" path ran 6–176 s: 44/449 bash+write calls (9.8%) exceeded 8 s, which in
   enforce mode means a block. Jev p99 < 0.6 s everywhere.
4. **GLiDE cost ≈ 9× Jev per decision** on the E1–E3 slices ($0.91 vs $0.10 per 1k; list $0.30/M
   input with multi-pass token counts). The run exhausted the account's included Fastino credit
   (HTTP 402).
5. **GLiDE serving was flaky:** intermittent `404 vLLM HTTP 404 (model_not_found)` and 504s on
   requests that succeed on retry. The adapter now retries those; a production integration would
   need the same, against Fastino's documented "don't retry 404" advice.
6. **GLiNER-2.5-Decide is not a drop-in for Jev-style questions.** It hosts no instructions or label
   descriptions, returns only the winner's confidence, and has an 8k window that clips long states.
   It is near chance on reasoning, and its best guard showing (bash AUROC 0.90 with long described
   labels) collapses with key-only labels (0.50). Its case is cost/latency (≈ ¼ of Jev's cost, p50
   0.6 s) for short, label-native taxonomies, which none of our Jev integrations are. Decide's
   thresholded numbers on 3-option questions depend on the adapter's even spread of the remainder;
   use AUROC.

## Threats to validity

- E1: thresholds and integrity question framings were tuned on overlapping sets (round-2 positives,
  integrity `c*` units), favouring Jev. Known-format secrets are masked in guard.write state exactly
  as Jev sees them. Integrity has 27 hacks; 92 cases have no open escalation family.
- E2/E3 are synthetic. E3's injections all address the agent explicitly, so the earlier "injection
  buried in a long page" miss did not reproduce for Jev.
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
- New reasoning-heavy decisions (dates, budgets, policy clauses, script tracing)
  - Recommendation: Prefer GLiDE where a multi-second tail is acceptable
  - Why: E2: 98.1% vs 75%
- Decide
  - Recommendation: Do not use for Jev-format judgments
  - Why: near chance without instructions
