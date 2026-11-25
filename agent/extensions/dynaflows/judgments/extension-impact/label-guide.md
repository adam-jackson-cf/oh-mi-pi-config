# Extension impact label guide

One question, `impact`, in [questions.json](questions.json). Label 1 means the change reaches the
repository: it must be reviewed or addressed for that repository before the change is relied on.

| Input | What the labeller sees |
| --- | --- |
| `config` | Rail sections: autonomy, ci, gates, judgments, mergePolicies, afterMerge, commands |
| `change` | Changed files, commit subject, bounded diff excerpts |

Labellers never see Jev scores. Cite one rule per label and give a one-sentence reason.

## Yes rules (label 1)

Yes when the change alters:

- Y1. How the config is read, validated, written or migrated: a section or field this config holds
  or must hold.
- Y2. A fixed guard, which runs in every repository: hook bypass, default-branch push, `gh pr merge`,
  worktree placement, subagent paths, config protection, the onboarding refusal.
- Y3. A commit check or merge step (commit checks, catalogue rules, path, naming, structure,
  `prove()`, CI, judged rules) that this config's `gates`, `ci` or `judgments` leaves on, and
  that applies to every repository or to a language, framework, runner or command the config shows.
- Y4. Which steps a config setting turns on or off, or how autonomy asks are granted.
- Y5. The required steps of a skill an agent follows in every governed repository: `git-tool`,
  `test-guidance`, `setup-repo-proofs`.

## No rules (label 0)

| Rule | No when the change only |
| --- | --- |
| N1 | Edits docs, ADRs, readme, workflow pages or dependency maps |
| N2 | Edits tests, fixtures, formatting, types or internal structure with the same results |
| N3 | Changes a step this config's `gates`, `ci`, `autonomy` or `judgments` turns off |
| N4 | Changes a language, framework or CI runner the config does not use |
| N5 | Changes help text, messages or wording; every required step stays the same |
| N6 | Changes judgment data or eval tooling for steps the repository does not run |

## Tie-breaks

- T1. Any one changed behaviour that reaches the repository makes the label 1.
- T2. Judge the change as if applied today to this config. Configs are converted from a 2026-11-21
  record snapshot (every gate on, autonomy from the record's agentMerge); older commits are replayed
  against it. A change to how the record was read counts under Y1 for the config field converted
  from it.
- T3. A clipped diff: judge visible hunks, file paths and the subject. A runtime file
  (`rails.yml`, `lib/`, `index.ts`, engine source) with visible behaviour change counts.
- T4. A removed check or refusal is a behaviour change (Y1-Y3), not a cleanup.
- T5. Language-neutral catalogue or commit-check changes apply to every repository whose
  `gates.commitChecks` is on.

## Worked examples

| Example | Label | Rule |
| --- | --- | --- |
| Rails refuse `git push --force` to the default branch; any repository | 1 | Y2 |
| Config validator requires `commands.commit`; any repository | 1 | Y1 |
| Python directory casing rule; repository whose commands run Python tooling | 1 | Y3 |
| Same rule; repository whose config sets `gates.commitChecks` false | 0 | N3 |
| Go test adapter; Python-only repository | 0 | N4 |
| Workflow page redrawn as diagrams | 0 | N1 |
| `ruff format` over engine files | 0 | N2 |
| `gates.prove` false starts skipping structure review too; any repository | 1 | Y4 |
| `prove()` change; repository whose config sets `gates.prove` false | 0 | N3 |
