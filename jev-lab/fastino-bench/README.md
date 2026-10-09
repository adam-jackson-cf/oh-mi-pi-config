# Fastino benchmark: Jev vs GLiDE vs GLiNER-2.5-Decide

Replays the same Jev-format decisions (`state` + typed `questions`) against:

- `jev`
  - Model: `typesafe/jev-1.13-20260917`
  - Route: OpenRouter `/api/alpha/decisions` via `agent/extensions/lib/jev.ts`
  - Notes: the live integration
- `glide`
  - Model: `fastino/GLiDE`
  - Route: `POST https://api.fastino.ai/v1/systemone`
  - Notes: structured Jev criteria flattened to strings
- `decide`
  - Model: `fastino/GLiNER-2.5-Decide`
  - Route: `POST /v1/chat/completions` with a `classifications` schema
  - Notes: task = question instructions, labels = criteria text
- `decide-keys`
  - Model: same
  - Route: same
  - Notes: task = question id, labels = option keys (`yes`/`no` for noul)

Decide hosts no instructions or label descriptions, returns only the winning label's confidence in
single-label mode, and has an 8,192-token window. The adapter therefore spreads `1 - confidence`
evenly over the other options (exact for two options) and clips state to 24k chars, halving on a
"too long" 400. Treat Decide's thresholded metrics on 3+ option questions as adapter-dependent;
AUROC is the fair comparison.

## Layout

- `lib/types.ts`: `BenchCase` (one replayable decision + truth) and `RunResult`.
- `lib/backends.ts`: adapters; bounded retries on transport errors, 425/429/502/503/504.
- `lib/metrics.ts`, `report.ts`: accuracy (Wilson CI), AUROC, Brier, ECE, integration-level act
  metrics (incl. the live guard thresholds, imported from `agent/extensions/jev-guard.ts`), strata,
  exact McNemar vs Jev, repeat stability, window→page aggregation.
- `run.ts`: resumable runner, append-only `runs/<experiment>/<backend>.jsonl` (mode 0600).
- `builders/`: deterministic, offline dataset builders → `datasets/<experiment>/*.jsonl`.
- `results/`: generated reports; `results/REPORT.md` is the write-up.

## Experiments

- `e1-baseline`
  - Question: Same decisions as the live guards (bash, write, result, integrity)
  - Builder: `builders/e1-baseline.ts` drives the real guards offline with a stubbed fetch to
    capture the exact Jev request
  - Truth: 2026-10-02 promotion corpora, canaries, two-family blind labels; integrity relabels
- `e2-reasoning`
  - Question: Temporal, arithmetic, code-trace and policy-exception decisions plus easy controls
  - Builder: `builders/e2-reasoning.ts`
  - Truth: computed by the generator
- `e3-longctx`
  - Question: Injection buried in 1k–28k-token pages; `full` = whole page, `guard-clipped` = the
    guard's 6k windows
  - Builder: `builders/e3-longctx.ts`
  - Truth: by construction

## Run

The Fastino key comes from Infisical, env `dev`. `.infisical.json` (runtime-only) links the
project; recreate it with `infisical init` in this directory. The Jev key is `OPENROUTER_API_KEY`
or `omp token openrouter`. Keys are never printed or stored.

```sh
cd ~/.omp/jev-lab/fastino-bench
bun builders/e2-reasoning.ts                     # (re)build a dataset; all builders are offline
infisical run --silent -- bun run.ts --experiment e2-reasoning [--backends glide,jev] [--reps 2]
bun report.ts --experiment e2-reasoning --by kind,control --out results/e2-reasoning.md
bun report.ts --experiment e1-baseline --dataset guard.bash --by set
bun scorecard.ts --out results/scorecard.md        # quality, speed and cost across all slices
```

GLiDE's slow path can take minutes per call, so run long experiments as a managed background
process with a long timeout. Re-running resumes: only cases without a successful result are sent.
