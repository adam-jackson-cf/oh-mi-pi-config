# Judgments and decide()

## Objective

Ask a calibrated judgment through one registry call and act on its yes, no or escalate outcome.

## Guidance

- A judgment is a folder `agent/extensions/dynaflows/judgments/<name>/` (kebab-case name), built and
  tuned with `skill://jev-actions`.
- `await decide(name, state, decision_context({"trigger": ..., "repo": ..., "item": ...}))` in the eval
  kernel after `%load` of `workflows.py`; it needs `judge()`. `trigger` is `commit`, `prove`, `pr` or
  `manual`; `repo`, `commit`, `pr`, `item_kind` and `item` name what was judged.

| Step | What `decide()` does |
| --- | --- |
| Load | Reads `questions.json` and `thresholds.json`; refuses stale keys or a missing taxonomy field |
| Model | Refuses when `omp config get modelRoles` names another judge than `thresholds.model` |
| Judge | One `judge(state, questions)` call; a failure is logged, then raises, never an outcome |
| Decide | Per bool question: `yes` at or above `threshold`, else `no`; a band with a fallback judge escalates its middle |
| Log | One `decisions` row per question in the decision store, `<agent dir>/dynaflows/dynaflows.db` |

- The agent dir is `PI_CODING_AGENT_DIR`, else `~/.omp/agent`; never the repository.
- The store is SQLite; [decision-store.sql](../../../engine/guardrails/data/decision-store.sql) is its
  only schema. A row holds the outcome (`flag` for yes, `pass` for no, `escalate`), status (`ok`,
  `error`, `incomplete`), score, threshold, mode, versions, subject and machine. Every flag and error,
  and a stable 1-in-10 sample of passes, keep the call's payload: the exact state, the questions sent
  and the raw answers, until the decision is reviewed.
- Returns the logged record plus `answers`: `call_id`, `judgment`, `state_sha256`, `model`,
  `question_version`, `probabilities`, `outcomes`, `modes`, `timestamp`.
- Choice questions return probabilities only; the caller's rubric reads them with the decision's
  `round_up_floor`.
- Treat `escalate` as uncertain: take the conservative branch or ask the owner; never read it as `no`.
  No shipped judgment has a band today, so none escalates.
- Rails read logged calls through `lib/judgment-calls.ts` (`readJudgmentCalls`, `latestJudgmentCall`).

- Each decision's `mode` is `enforced` (acts) or `shadow` (logged, never acts); see the
  [decision store](decision-store.md).

## Rail judgments

Structured mode only. Rails run their deterministic pre-checks first, then the same registry
through `lib/decide.py` (`questions`, then `record`, or `error` for a judge call that failed) with
omp's judge role; the model that answered must be `thresholds.model`. A failed call is logged and is
never an outcome.

- The table below is for enforced decisions. A shadow decision never acts: the rail behaves as if
  the judgment had not run and notes a shadow yes as "shadow, not blocking".
- A failed call refuses the commit, merge or command and leaves the evidence obligation unmet; a
  failed `in-scope` call on an `ask` lets the question through.

| Judgment | Rail | yes | escalate |
| --- | --- | --- | --- |
| `extension-impact` | Restart review | Review task | Review task |
| `implementation-integrity` | `git commit`, after the commit checks | Refused: restore checks | Owner |
| `completion-evidence` | Turn end and `gh pr merge`, agent's PR, after P2-P8 | Unmet obligation; merge refused | Owner |
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
        decision_context({
            "trigger": "manual",
            "item_kind": "action",
            "item": action[:200],
        }),
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
| `thresholds.json` decision | Cutoff, `mode`, `shadow_exit` (shadow only) and the taxonomy below |
| `cases.jsonl`, `labels.jsonl` | Eval cases and appended labels with provenance |
| `scores.csv` | Raw scores per case and `question@version_key` from the pinned model |
| `label-guide.md` | The rules each label cites |

- `version_key`: sha256 of compact sorted JSON `{questions, state, model}`; each decision's key hashes
  `{question, state, model}`.
- A bool decision is a single cutoff `{"threshold": t}`, or a band `no_at_most` < `yes_at_least` with a
  `fallback` naming another registered judgment. A band without one, or any `status`, is refused.
- Every decision carries a jev-actions taxonomy and a `mode`; `load_judgment` refuses a missing or
  unknown value. [Decision store](decision-store.md) lists the fields.
- Labels: the innovation council takes the first pass, the agent arbitrates, the owner may override.
- Changing wording, state shape or model makes the keys stale: re-score, re-tune, then ship.
- An enforced judgment acts from the moment it merges: ship only thresholds fit to enforce, and list
  any label shortfall in its `thresholds.json`. Ship in shadow mode only when precision cannot be
  measured yet; retire a judgment only when it cannot do the job.
