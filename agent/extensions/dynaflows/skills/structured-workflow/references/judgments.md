# Judgments and decide()

## Objective

Ask a calibrated judgment through one registry call and act on its yes, no or escalate outcome.

## Guidance

- A judgment is a folder `agent/extensions/dynaflows/judgments/<name>/` (kebab-case name), built and
  tuned with `skill://jev-actions`.
- `await decide(name, state)` in the eval kernel after `%load` of `workflows.py`; it needs `judge()`.

| Step | What `decide()` does |
| --- | --- |
| Load | Reads `questions.json` and `thresholds.json`; refuses stale version keys |
| Model | Refuses when `omp config get modelRoles` names another judge than `thresholds.model` |
| Judge | One `judge(state, questions)` call; a failure raises, never an outcome |
| Decide | Per bool question: `yes` at or above `yes_at_least`, `no` at or below `no_at_most`, else `escalate` |
| Log | Appends one JSON line to `<agent dir>/dynaflows/judgment-calls.jsonl` |

- The agent dir is `PI_CODING_AGENT_DIR`, else `~/.omp/agent`; never the repository.
- Returns the logged record plus `answers`: `judgment`, `state_sha256`, `model`,
  `question_version`, `probabilities`, `outcomes`, `timestamp`.
- Choice questions return probabilities only; the caller's rubric reads them with the decision's
  `round_up_floor`.
- Treat `escalate` as uncertain: take the conservative branch or ask the owner; never read it as `no`.
- Rails read the log through `lib/judgment-calls.ts` (`readJudgmentCalls`, `latestJudgmentCall`).

## Folder contract

| File | Holds |
| --- | --- |
| `questions.json` | `state` shape and `questions` (bool or choice), as `judge()` takes them |
| `thresholds.json` | `model`, `version_key`, `score_space: raw`, one `decisions` entry per question |
| `cases.jsonl`, `labels.jsonl` | Eval cases and appended labels with provenance |
| `scores.csv` | Raw scores per case and `question@version_key` from the pinned model |
| `label-guide.md` | The rules each label cites |

- `version_key`: sha256 of compact sorted JSON `{questions, state, model}`; each decision's key hashes
  `{question, state, model}`.
- A bool decision holds `status` (`provisional` or `ready`), `no_at_most` < `yes_at_least`.
- Changing wording, state shape or model makes the keys stale: re-score, re-tune, then ship.
