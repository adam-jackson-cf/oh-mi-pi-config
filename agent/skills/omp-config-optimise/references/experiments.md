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

Keep artifacts under `~/.omp/.todo/artifacts/<date>-<topic>/` (gitignored).

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
  --reps 3 --out ~/.omp/.todo/artifacts/<date>-<model>-effort
python3 analyze.py ~/.omp/.todo/artifacts/<date>-<model>-effort
```

Add `--arm F-fast=low@priority --reps 1` for a Fast-tier latency check. Probe
`:off`/`:minimal` on the new model first (see `omp-mechanics.md`) and test
"no reasoning" as the lowest effort that actually emits zero reasoning.
The simple fixtures hit a ceiling (low and medium both 100%); the hard
fixtures are the ones that discriminate. Pass `--fixtures` to run a subset.

Hidden checks must assert only what the plan states. A check that asserts
unstated behaviour is a fixture defect: correct it, re-validate, re-score every
arm, and record the deviation in the report.

Tracked fixtures are kept clean under the repository lint gate. The bytes used
in each recorded run are preserved in that run's artifact directory
(`fixtures/` or `fixtures-as-run/`); the tracked copies are behaviourally
equivalent revisions and re-validate before any new run.

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
  --out ~/.omp/.todo/artifacts/<date>-review-detection
python3 run_reviews.py <same arms> --rerun-failed --out <same dir>
python3 analyze.py ~/.omp/.todo/artifacts/<date>-review-detection
```

## Reusable harness: vision reading

`experiments/vision-reading/` generates 16 chart, table, diagram and
small-text questions with answers computed from the plotted data
(`uv run generate_items.py <dir>`), then scores exact answers:

```bash
cd ~/.omp/agent/skills/omp-config-optimise/experiments/vision-reading
uv run generate_items.py ~/.omp/.todo/artifacts/<date>-vision/items
python3 run_vision.py --items ~/.omp/.todo/artifacts/<date>-vision/items \
  --arm S-low=anthropic/claude-sonnet-5-5:low --reps 3 \
  --out ~/.omp/.todo/artifacts/<date>-vision
python3 run_vision.py --analyze ~/.omp/.todo/artifacts/<date>-vision
```

The first run hit a ceiling (15 of 16 items solved by every arm); add harder
items before relying on small differences.

## Record

- 2026-09-28 — Does GPT-6 Luna need reasoning for fixed-plan work? `low` 18/18, `medium` 18/18,
  `high` 15/18; Fast ~20% lower wall time. Artifact:
  `~/.omp/.todo/artifacts/280926-luna-reasoning-eval/report.md`.
- 2026-09-28 — Does `low` still hold on harder multi-file plans? `low` 7/9, `medium` 9/9, `high` 9/9
  after an events-check correction; `task` raised to `medium`. Artifact:
  `~/.omp/.todo/artifacts/280926-luna-hard-effort/report.md`.
- 2026-09-28 — What replaces GPT-6 Sol/Luna on implementation? Hard set: Sonnet 5.5
  `low`/`medium`/`high` 9/9, GPT-6 Sol `high` 9/9, GPT-5.6 Sol `low` 9/9, Terra `xhigh` 8/9,
  GPT-6 Luna `medium` 6/9; `task` moved to Sonnet 5.5 `low`. Artifact:
  `~/.omp/.todo/artifacts/280926-sol-luna-replacement/report.md`.
- 2026-09-28 — Which OpenAI model judges and reviews code best? Seeded-defect review: all six
  arms (GPT-6 Sol `high`/`medium`, GPT-5.6 Sol `low`, Terra `xhigh`, Astra `medium`, Opus
  `medium`) 36/36; judges and `reviewer` set to the cheapest, GPT-6 Sol `medium`. Artifact:
  `~/.omp/.todo/artifacts/280926-review-detection/report.md`.
- 2026-09-28 — Can `vision` move to Sonnet 5.5? Sonnet `low` and `medium` 48/48, GPT-6 Sol
  `medium` 45/48; `vision` moved to Sonnet 5.5 `low`. Artifact:
  `~/.omp/.todo/artifacts/280926-vision-reading/report.md`.
