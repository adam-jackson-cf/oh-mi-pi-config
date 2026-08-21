---
name: "instruct-eval"
description: "Design and run controlled evaluations of whether a supplied instruction changes agent behavior; USE WHEN testing an instruction through isolated baseline and candidate trials with blind architectural scoring."
---

# Workflow

## Input

- Read the invocation payload from `local://instruct-eval-payload.json`, then pass its resolved file path to `experiment.py init --payload`.
- Require one exact JSON object with nonempty string fields `instruction`, `provider`, `model`, and `repository`. Permit only optional fields `thinking`, `run_id`, `trials_per_condition`, `practical_effect_threshold`, and `significance_level`.
- Preserve `instruction` verbatim. Default omitted `thinking` to `medium`, `trials_per_condition` to `3`, `practical_effect_threshold` to `0.2`, and `significance_level` to `0.05`; reject missing required fields, extra fields, and invalid value types before Step 0.

### Step 0: Preflight experiment workspace

- **Purpose**: Resolve the repository root, validate OMP capabilities, and allocate an isolated run under `.experiments/instruct-eval`.
- **When**: At every invocation before design or execution.
- Parse the exact instruction and optional `--thinking` argument, defaulting to `medium`.
- Validate provider, model, thinking level, OMP version, Python version, and writable repository root.
- Create a unique run directory without modifying `.todo` or activating candidate guidance.
- Record resolved arguments and tool versions in `request.json`.
- Workflow: [references/step-0-preflight-workflow.md](references/step-0-preflight-workflow.md)

### Step 1: Define treatment and causal hypothesis

- **Purpose**: Make the supplied instruction the only intentional difference between baseline and candidate conditions.
- Preserve the instruction verbatim.
- Define the desired behavior, competing behavior, candidate injection point, and falsifiable treatment hypothesis.
- Reject task prompts that directly reveal the desired architecture.
- Pre-register run counts, practical-effect threshold, and stop conditions.
- Workflow: [references/step-1-treatment-design-workflow.md](references/step-1-treatment-design-workflow.md)

### Step 2: Design fixtures and checkpoint requests

- **Purpose**: Create varied scenarios that expose compliance, shortcuts, future change cost, and overcorrection.
- Produce at least three fixture options: shortcut pressure, change pressure, and overcorrection control.
- Select scenarios with distinct failure mechanisms rather than textual variants.
- Define an initial request and one or more checkpoint requests for each scenario.
- Specify pristine fixture files, public behavior, hidden invariants, preferred architecture, and disallowed shortcuts.
- Workflow: [references/step-2-fixture-design-workflow.md](references/step-2-fixture-design-workflow.md)

### Step 3: Review semantic oracles and rubric

- **Purpose**: Prove tests and scoring distinguish behavior without encoding unspecified implementation details.
- Run pristine, complete-reference, and plausible-failure probes.
- Require public and hidden tests to assert observable contracts only.
- Define exact preferred and non-preferred architecture archetypes and rubric dimensions.
- Stop on parameter-order, source-text, timing, or implementation-specific oracle assumptions.
- Workflow: [references/step-3-oracle-review-workflow.md](references/step-3-oracle-review-workflow.md)

### Step 4: Freeze the protocol

- **Purpose**: Make every experimental input immutable before baseline execution.
- Freeze fixtures, prompts, hidden tests, rubric, model, thinking level, timeouts, permissions, and run counts.
- Write `protocol.json`, `manifest.json`, and `checksums.json`.
- Validate baseline and candidate differ only by the exact instruction treatment.
- Require a new run for any post-freeze design change.
- Workflow: [references/step-4-protocol-freeze-workflow.md](references/step-4-protocol-freeze-workflow.md)

### Step 5: Execute trials and checkpoints

- **Purpose**: Capture fresh first-pass baseline, candidate, and subsequent change-pressure results.
- Run unique disposable fixture copies in fresh OMP processes.
- Use the same resolved `--thinking` value for every condition and scorer process.
- Preserve commands, outputs, exit codes, versions, context checksums, diffs, and test outcomes.
- Restore baseline context before applying identical checkpoint requests to every initial workspace.
- Never count corrective turns.
- Workflow: [references/step-5-execution-workflow.md](references/step-5-execution-workflow.md)

### Step 6: Blind-score architecture

- **Purpose**: Measure design outcomes without revealing condition identity.
- Create a randomized alias map after initial trials finish.
- Copy each implementation into a clean scoring workspace without condition metadata.
- Run the frozen scorer prompt and architecture rubric.
- Validate score schema, scorer exit status, and absence of production edits.
- Workflow: [references/step-6-blind-scoring-workflow.md](references/step-6-blind-scoring-workflow.md)

### Step 7: Analyze, amend, and decide

- **Purpose**: Calculate treatment effects, validate evidence, and preserve invalidated history.
- Compute proportions, Wilson 95% intervals, Fisher exact comparisons, archetype distributions, behavior outcomes, changed files, and production NLOC.
- Apply pre-registered behavior-preservation, practical-effect, reproducibility, checkpoint-effect, and control rules.
- When an oracle, rubric, or protocol defect is found, preserve invalidated evidence, record an amendment, and rerun only from the last valid frozen boundary.
- Write `analysis.json`, `verification.json`, and `comparative-review.json` with no rollout authorization unless every rule passes.
- Workflow: [references/step-7-analysis-workflow.md](references/step-7-analysis-workflow.md)

## Output

### Result Format

- Report the run directory and frozen instruction.
- Report baseline and candidate behavior, architecture, and checkpoint effects by scenario.
- Report the pre-registered decision result, amendments, invalidated evidence, and residual risks.
- Do not authorize rollout when any decision rule fails.
