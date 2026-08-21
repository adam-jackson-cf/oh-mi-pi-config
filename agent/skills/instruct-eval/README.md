# instruct-eval

## Overview

instruct-eval is a self-contained workflow and Python harness for testing whether an exact instruction changes agent behavior under isolated baseline and candidate conditions.

## When to use it

- Evaluating broad behavior guidance before adding it to persistent instructions.
- Comparing an exact user-prompt instruction against an instruction-free baseline.
- Testing whether architecture guidance survives realistic follow-up change pressure.

## Example payload

Write `local://instruct-eval-payload.json` as an exact JSON object, then invoke the skill. Required fields are `instruction`, `provider`, `model`, and `repository`; `thinking` is optional and defaults to `medium`.

`experiment.py init --payload <resolved-payload-path>` is the only initialization interface.

## References

- [Preflight experiment workspace](references/step-0-preflight-workflow.md)
- [Define treatment and causal hypothesis](references/step-1-treatment-design-workflow.md)
- [Design fixtures and checkpoint requests](references/step-2-fixture-design-workflow.md)
- [Review semantic oracles and rubric](references/step-3-oracle-review-workflow.md)
- [Freeze the protocol](references/step-4-protocol-freeze-workflow.md)
- [Execute trials and checkpoints](references/step-5-execution-workflow.md)
- [Blind-score architecture](references/step-6-blind-scoring-workflow.md)
- [Analyze, amend, and decide](references/step-7-analysis-workflow.md)
