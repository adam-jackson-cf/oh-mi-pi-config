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
| Decide | Per bool question: `yes` at or above `threshold`, else `no`; a band with a fallback judge escalates its middle |
| Log | Appends one JSON line to `<agent dir>/dynaflows/judgment-calls.jsonl` |

- The agent dir is `PI_CODING_AGENT_DIR`, else `~/.omp/agent`; never the repository.
- Returns the logged record plus `answers`: `judgment`, `state_sha256`, `model`,
  `question_version`, `probabilities`, `outcomes`, `timestamp`.
- Choice questions return probabilities only; the caller's rubric reads them with the decision's
  `round_up_floor`.
- Treat `escalate` as uncertain: take the conservative branch or ask the owner; never read it as `no`.
  No shipped judgment has a band today, so none escalates.
- Rails read the log through `lib/judgment-calls.ts` (`readJudgmentCalls`, `latestJudgmentCall`).

## Rail judgments

Structured mode only. Rails run their deterministic pre-checks first, then the same registry
through `lib/decide.py` (`questions`, then `record`) with omp's judge role; the model that
answered must be `thresholds.model`. A failed call is never an outcome.

- Every judgment is enforced.
- A failed call refuses the commit, merge or command and leaves the evidence obligation unmet; a
  failed `in-scope` call on an `ask` lets the question through.

| Judgment | Rail | yes | escalate |
| --- | --- | --- | --- |
| `extension-impact` | Restart review | Review task | Review task |
| `adr-needed` | `gh pr merge`, no ADR file in the PR | Refused: add an ADR | Owner |
| `implementation-integrity` | `git commit`, after the commit checks | Refused: restore checks | Owner |
| `completion-evidence` | Turn end, agent's PR, after P2-P8 | Unmet obligation | Owner |
| `in-scope` | `ask` tool; `rails.yml` `askFirstCommand` commands | Ask first | Ask first |

- `no` passes, except an `ask` the judgment calls in scope: the rails refuse it; decide it yourself.
- The owner approves an escalation with a prompt line `$approve <judgment>:<id>`; the rails
  print the line. An approval holds for that session and that subject only.

## In-scope call site

Rails see only the `ask` tool and `askFirstCommand` commands. Before any other action the agreed
work may not grant (another machine, a shared setting, new cost or tooling), call:

```python
outcome = (
    await decide(
        "in-scope",
        {
            "work": {
                "agreed_scope": owner_messages,  # initial request, then the four latest owner messages
                "agent_context": reasoning,  # your text just before the item
                "proposed_action": action,  # one question, or "[intent] tool args"
            }
        },
    )
)["outcomes"]["ask_first"]
```

| Outcome | Then |
| --- | --- |
| `yes`, `escalate` | Ask the owner first |
| `no` | Act; it is a detail of the agreed work |

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
- A bool decision is a single cutoff `{"threshold": t}`, or a band `no_at_most` < `yes_at_least` with a
  `fallback` naming another registered judgment. A band without one, or any `status`, is refused.
- Labels: the innovation council takes the first pass, the agent arbitrates, the owner may override.
- Changing wording, state shape or model makes the keys stale: re-score, re-tune, then ship.
- A judgment acts from the moment it merges: ship only thresholds fit to enforce, and list any label shortfall in its `thresholds.json`.
