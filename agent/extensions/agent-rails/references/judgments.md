# Judgments

How to ask a judgment, how its mode is chosen, how rails act on it and how its decisions are reviewed. Build and tune a judgment with `skill://jev-actions`.

## decide()

```python
context = decision_context({"trigger": ..., "repo": ..., "item": ...})
answers = await decide(name, state, context)
```

Load `<extension>/lib/rails_kernel.py` in an eval cell; it needs `judge()`. `trigger` is `commit`,
`prove`, `pr` or `manual`; `repo`, `commit`, `pr`, `item_kind` and `item` name what was judged.

| Step | What `decide()` does |
| --- | --- |
| Load | Reads `questions.json` and `thresholds.json`; refuses stale keys or a missing taxonomy field |
| Model | Refuses when `omp config get modelRoles` names another judge than `thresholds.model` |
| Judge | One `judge(state, questions)` call; a failure is logged, then raises, never an outcome |
| Decide | Per bool question: `yes` at or above `threshold`, else `no`; a band with a fallback escalates its middle |
| Log | One `decisions` row per question in the decision store |

- The store is `<agent dir>/agent-rails/agent-rails.db` (`PI_CODING_AGENT_DIR`, else `~/.omp/agent`), with one schema,
  [decision-store.sql](../engine/data/decision-store.sql). Flags, errors and a stable 1-in-10 sample of passes keep their payload until reviewed.
  A store at another schema version is refused: on upgrade, rename the old file (for example to `agent-rails.v2.db`).
- It returns the logged record plus `answers`: `call_id`, `judgment`, `state_sha256`, `model`, `question_version`, `probabilities`,
  `outcomes`, `modes` and `timestamp`.
- Choice questions return probabilities only. Treat `escalate` as uncertain: take the conservative branch or ask the owner, never read it as `no`.

## Effective mode

- A decision's global `mode` in `thresholds.json` is `enforced` (acts) or `shadow` (logged, never acts); a per-language one holds a mode per language.
- The repo config's [`judgments`](../docs/config.md#judgments) overrides only soften it: each question (or language) runs
  at the lower of the two, off < shadow < enforced. A missing key follows the global mode.
- An off question is never asked or logged; with every question off, `decide()` returns `{"judgment": name, "off": true}`. `decisions` rows
  record the effective mode.

## Rail judgments

Rails run their deterministic pre-checks first, then `decide()` through `lib/decide.py` with omp's
judge role. They run in governed repositories; `in-scope` runs in every Git repository.

| Judgment | Rail | yes | escalate |
| --- | --- | --- | --- |
| `extension-impact` | Restart review | Review task | Review task |
| `implementation-integrity` | `git commit`, after the commit checks | Refused: restore checks | Owner |
| `completion-evidence` | Turn end and `gh pr merge` of the agent's PR | Unmet obligation; merge refused | Owner |
| `in-scope` | `ask` tool; `askFirstCommand` commands | Ask first | Ask first |

- A shadow decision never acts ("shadow, not blocking"). A failed judge call refuses, except that an
  `ask` goes through. `no` passes, except an `ask` judged in scope: decide it yourself.
- The owner approves an escalation with the printed `$approve <judgment>:<id>` line, for that session and subject.

## In-scope call site

Before any action the agreed work may not grant (another machine, a shared setting, new cost or tooling) and the rails cannot see:

```python
work = {
    "agreed_scope": owner_messages,  # initial request, then the four latest owner messages
    "agent_context": reasoning,  # your text just before the item
    "proposed_action": action,  # one question, or "[intent] tool args"
}
item = {"trigger": "manual", "item_kind": "action", "item": action[:200]}
answers = await decide("in-scope", {"work": work}, decision_context(item))
```

`answers["outcomes"]["ask_first"]` `yes` or `escalate`: ask the owner first. `no`: act; it is a detail of the agreed work.

## Folder contract

A judgment is `judgments/<name>/` in the extension:

| File | Holds |
| --- | --- |
| `questions.json` | `state` shape, `questions` (bool or choice) as `judge()` takes them, and optional `variants` |
| `thresholds.json` | `model`, `version_key`, `score_space: raw`, one `decisions` entry per question |
| `cases.jsonl`, `labels.jsonl` | Eval cases and appended labels with provenance |
| `scores.csv` | Raw scores per case and `question@version_key` from the pinned model |
| `label-guide.md` | The rules each label cites; `label-guide.<language>.md` for a language's own cases |

- `version_key` is the sha256 of compact sorted JSON `{questions, state, model}` (plus `variants` when present); each decision's
  key hashes `{question, state, model}` for the question it asks. Changing wording, state shape or model makes the keys stale:
  re-score, re-tune, then ship.
- A decision is `{"threshold": t}`, or a band `no_at_most` < `yes_at_least` with a `fallback` judgment. Each carries `mode`, `shadow_exit` when
  shadow, and the taxonomy fields in [eval-data schemas](../skills/jev-actions/references/eval-data-schemas.md).
- A per-language decision (structure-naming's) holds shared fields once and each language's `version_key`, `mode`, cutoff, `shadow_exit` and
  evidence under `languages.<language>`; a language without settings never runs. `questions.json` `variants.<question>.<language>` words the
  question for one language. Store rows and `shadow_progress` carry the language (`any` for a decision that holds for every input).

## Shadow review and labels

Run `guardrails shadow-review` from `<extension>/engine` with `uv run --project .`:

| Command | Does |
| --- | --- |
| `export <judgment> [--question Q] [--sample-passes N]` | A blind batch of unreviewed flags and sampled passes |
| `import <labels.json> [--labeller council\|arbiter]` | Labels into `reviews` and the judgment's eval data |
| `report [<judgment>]` | `shadow_progress` and precision at the cutoff per question |
| `inspector <judgment> [--verdict V] <out.jev.json>` | Jev Inspector session files rebuilt from payloads |

`--machine nuc` reads the NUC's store read-only over `ssh`. The council labels first, the agent arbitrates and the owner may override.
`blind_label(batch_files, rubric_path, labels)` runs two blind read-only sessions (scout and reviewer) and returns
`agreed`, `disputed` and `failed` lists; it writes no labels.

## Dependency advisories

A commit refused with `SECURITY.dependency-audit` lines is triaged, never suppressed:

```python
found = dependency_audit_findings(repo)  # ok, repo, advisories, failures
result = await triage_advisories(found, evidence)  # halt, owner, fix, failures, report
```

- `evidence` maps each advisory key to exactly: `advisory_text`, `advisory_source`, `scope`
  (`runtime` or `dev`), `scope_source`, `code_search` (`queries` and `hits` of `{location, line}`),
  `fix_options` and `blocked_fix` (`null` or `{blocked_by, constraint, constraint_source}`).
- A `MAL-` advisory goes to the owner; `dependency-severity` and a fixed rubric rate the rest.
- `halt`: give the owner `report`. Otherwise upgrade each `fix` entry and commit again. Agents
  never add or edit dependency-audit exceptions.
