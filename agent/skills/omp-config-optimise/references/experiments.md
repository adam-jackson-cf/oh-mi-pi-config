# Experiments before routing

Route on a capability claim only after a frozen experiment supports it. Use
`experiment-design` (Fable lead, Astra and Opus peers) to design experiments for
new questions; reuse the harness below for the recurring effort question.

## Gates

- **G0 eligibility** — probe the knob first. Example: `:off` on
  `gpt-6-luna` looked like "no reasoning" but applied the server default.
- **G1 direction** — write the hypothesis, arms and decision rule in
  `protocol.md` before any trial.
- **G2 fixture validity** — every start state fails the hidden check and every
  reference passes; freeze bytes in `manifest.sha256`.
- **G3 trials** — fresh headless process per trial, shuffled arm order,
  preserved logs.
- **G4 blind scoring** — deterministic hidden tests and scope checks; no
  model or human grading of outputs.
- **G5 evidence** — `analysis.md` and `report.md` beside the runs.
- **G6 decision** — apply the pre-registered rule; state ceiling effects and
  sample-size limits.

Choose an artifact output directory outside the repository for each experiment.

## Reusable harness: fixed-plan effort

`experiments/fixed-plan-effort/` holds nine fixed-plan fixtures with hidden
checks: 4 implementation and 2 simple-action (`kind` in `meta.json`), plus 3
`implementation-hard` fixtures (8–9 steps, 6–9 files, steps phrased as intent
resolved from codebase conventions). Scripts:

```bash
cd ~/.omp/agent/skills/omp-config-optimise/experiments/fixed-plan-effort
python3 validate_fixtures.py
python3 run_trials.py --model openai-codex/<new-model> \
  --arm A-low=low --arm B-medium=medium --arm C-high=high \
  --reps 3 --out <output-dir>
python3 analyze.py <output-dir>
```

Add `--arm F-fast=low@priority --reps 1` for a Fast-tier latency check. Probe
`:off`/`:minimal` on the new model first (see `omp-mechanics.md`) and test
"no reasoning" as the lowest effort that actually emits zero reasoning.
Pass `--fixtures` to run a subset.

Hidden checks must assert only what the plan states. A check that asserts
unstated behaviour is a fixture defect: correct it, re-validate, re-score every
arm, and record the deviation in the report.

## Reusable harness: review detection

`experiments/review-detection/` measures how well a model finds plan-violating
defects in a code change (judge and reviewer roles). `cases.py` builds 12 cases
from the three hard fixed-plan fixtures: per fixture one clean change and three
changes with two seeded defects each. Every seeded defect alone fails the
fixture's hidden check. Scoring is deterministic: file plus plan step or line
window, one-to-one, with clean-case false positives and missingness handling
described in `analyze.py`.

```bash
cd ~/.omp/agent/skills/omp-config-optimise/experiments/review-detection
python3 validate_cases.py
python3 run_reviews.py --arm G-high=openai-codex/gpt-6-sol:high \
  --arm P-low=openai-codex/gpt-5.6-sol:low --reps 2 \
  --out <output-dir>
python3 run_reviews.py <same arms> --rerun-failed --out <same dir>
python3 analyze.py <output-dir>
```

## Reusable harness: vision reading

`experiments/vision-reading/` generates 16 chart, table, diagram and
small-text questions with answers computed from the plotted data
(`uv run generate_items.py <dir>`), then scores exact answers:

```bash
cd ~/.omp/agent/skills/omp-config-optimise/experiments/vision-reading
uv run generate_items.py <output-dir>/items
python3 run_vision.py --items <output-dir>/items \
  --arm S-low=anthropic/claude-sonnet-5-5:low --reps 3 \
  --out <output-dir>
python3 run_vision.py --analyze <output-dir>
```

## Ranking harness: SCBench model comparison

The three harnesses above are pass/fail gates. SCBench
(`~/Projects/inflight/slop-code-bench`) ranks models.
Each run builds and then extends one program through 8 cumulative
checkpoints. Use it to compare models; OMP is only the vehicle.

- Agent: SCBench's `omp` adapter (`configs/agents/omp.yaml`, OMP 18.4.4,
  vanilla config, $20 and 250-step caps). Models are
  `omp_broker/<model>` (`configs/models/*.yaml`); effort is the `thinking=`
  override. Models without an off mode reject `disabled`.
- Credentials: start `omp auth-broker serve --bind=127.0.0.1:8765` on the
  host first and stop it afterwards. Containers reach it at
  `host.docker.internal:8765` with the bearer token only; they never hold
  refresh tokens.
- Run in non-blocking mode: `continue_after_test_failure=true`, so every
  checkpoint runs even when the previous one missed tests (otherwise the run
  stops at the first imperfect checkpoint). Checkpoints are still assessed
  against every test. Use a per-rep `save_dir`.
- Rank on the final score (pass rate adjusted for code quality), then mean
  pass rate (`C_p`) and the quality components. Strict checkpoints solved is
  context only. This differs from SCBench's own reporting rule by owner decision (2026-09-30).
- Cost is OMP's reported cost. Codex credits are 25× it for every GPT-6 and
  GPT-6.1 model (the credit rate card is exactly 25× API price). Use at least
  3 reps per arm; runs take about 1–2 hours and up to $5.50 API-equivalent
  each.
- Run outputs stay in SCBench under `experiments/omp-model-compare/`.
