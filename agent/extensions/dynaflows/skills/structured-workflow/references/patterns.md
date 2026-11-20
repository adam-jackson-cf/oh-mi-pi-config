# Pattern contracts

## Objective

Load the orchestration helpers and find each helper's contract.

## Guidance

- Load the helpers in a standalone Python eval cell: `%load ~/.omp/agent/extensions/dynaflows/engine/workflows.py`.
- Only `freeze`, `prove`, `structure_review`, `blind_label`, `dependency_audit_findings` and `triage_advisories`
  are supported orchestration APIs. Other functions are internal helpers, even when plain-Python callable.
- `blind_label` requires eval's `agent()`/`wait()` globals; `triage_advisories` needs `judge()`;
  async `structure_review` needs `judge()`/`judge_batch()`. `freeze`, `prove` and `dependency_audit_findings`
  also run in plain Python.
- Signatures, schemas, caps and failure handling have one home each:
  [freeze](freeze.md), [prove](prove.md), [structure review](structure-review.md), [blind label](blind-label.md)
  and [dependency advisories](dependency-advisories.md).
- Git authority and CI, PR and merge mechanics belong to `skill://git-tool`.
- Freeze raises an attributed `WorkflowError` for broken contracts or infrastructure.
- Prove fails closed with JSON-safe evidence and cleans its scratch trees; there is no automatic fix loop or fallback pass.
- Never catch a cap or contract error and continue as if verified. Main owns changes, CI and follow-up routing under
  the stage and Git contracts.

## Rails command parsing

- Rails inspect `git`/`gh` words inside supported tool-runner launchers: `mise exec`/`mise x` (with `--` before
  the command), `pnpm exec`/`pnpm dlx`, `npx`, `bunx`, `uv run` and `poetry run`.
- `uv run` and `poetry run` honour `--` as the end of launcher options. Without it, ambiguous launcher options with a
  later `git`/`gh` word block rather than silently skipping checks; put the Git command first after options or use `--`.
- Rails recursively scan `$(...)` and backtick command substitutions, including inside double quotes,
  without evaluating their output.
- Alias and shell function definitions whose bodies contain a `git` or `gh` word fail closed.
