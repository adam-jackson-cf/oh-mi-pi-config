# Extension impact label guide

One question, `impact`, in [questions.json](questions.json). Label 1 means the change reaches the
repository: it must be reviewed or addressed for that repository before the change is relied on.

| Input | What the labeller sees |
| --- | --- |
| `mode` | Mode name and the fixed list of what runs in it |
| `record` | `gate.json` in full, bounded `profile.md` summary |
| `change` | Changed files, commit subject, bounded diff excerpts |

Labellers never see Jev scores. Cite one rule per label and give a one-sentence reason.

## Yes rules (label 1)

Yes when the change alters:

- Y1. How the record is read, validated, written or migrated: a field this record holds or must
  hold. Any mode
- Y2. A guard listed for minimal mode: hook bypass, default-branch push, `gh pr merge`, worktree
  placement, subagent paths, `agentMerge`, `git-tool` read. Any mode
- Y3. A structured-only check or merge step (commit checks, catalogue rules, path, naming,
  structure, `prove()`, CI) that applies to every repository or to a language, framework, runner
  or command the record shows. Structured only
- Y4. Which steps a mode runs, or how a repository's mode is decided. Repositories in the affected
  mode
- Y5. The required steps of a skill or agent role an agent follows in this repository: `git-tool`
  in any mode; `structured-workflow`, `test-guidance`, `setup-repo-proofs`, agent roles in
  structured mode

## No rules (label 0)

| Rule | No when the change only |
| --- | --- |
| N1 | Edits docs, ADRs, readme, workflow pages or dependency maps |
| N2 | Edits tests, fixtures, formatting, types or internal structure with the same results |
| N3 | Changes structured-only steps and the mode is minimal |
| N4 | Changes a language, framework or CI runner the record and profile do not use |
| N5 | Changes help text, messages or wording; every required step stays the same |
| N6 | Changes judgment data or eval tooling for steps the repository does not run |

## Tie-breaks

- T1. Any one changed behaviour that reaches the repository makes the label 1.
- T2. Judge the change as if applied today to this record in this mode. Records are a 2026-11-21
  snapshot; older commits are replayed against it. A change whose effect `mode.runs` already
  describes changes no step that mode runs (no Y4); Y1-Y3 still apply. Owner-confirmed 2026-11-21.
- T3. A clipped diff: judge visible hunks, file paths and the subject. A runtime file
  (`rails.yml`, `lib/`, `index.ts`, engine source) with visible behaviour change counts.
- T4. A removed check or refusal is a behaviour change (Y1-Y3), not a cleanup.
- T5. Language-neutral catalogue or commit-check changes apply to every structured repository.

## Worked examples

| Example | Label | Rule |
| --- | --- | --- |
| Rails refuse `git push --force` to the default branch; any repository | 1 | Y2 |
| Record loader requires `checks.commit`; any repository | 1 | Y1 |
| Python directory casing rule; structured Python repository | 1 | Y3 |
| Same rule; minimal TypeScript repository | 0 | N3 |
| Go test adapter; structured Python-only repository | 0 | N4 |
| Workflow page redrawn as diagrams | 0 | N1 |
| `ruff format` over engine files | 0 | N2 |
| Minimal mode stops running a step it ran before; minimal repository | 1 | Y4 |
| Same change; structured repository, structured steps unchanged | 0 | N3 |
