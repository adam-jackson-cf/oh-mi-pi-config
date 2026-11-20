# Rule table

## Objective

Maintain a repository-local record of each correction rule, its enforcer, and historical proof.

## Guidance

Use an existing repository rule registry when available; otherwise maintain `.omp/rules.md`.
Do not put repository-specific entries in this shared reference or add fields to `.omp/gate.json`.

### Format

Use these exact columns:

| rule | enforcer | level | evidence | added |
| ---- | -------- | ----- | -------- | ----- |

- `rule`: Stable rule ID and a concise invariant; reuse existing IDs where they match.
- `enforcer`: Actual architecture boundary, type, check path/command, test, or agent rule location.
  State `unenforced` when no mechanism actually rejects violations; do not hide that status.
- `level`: One of `architecture`, `types`, `lint/CI check`, `behaviour test`, or `agent rule/docs`.
  This is the priority order, strongest first; documentation is only for irreducible judgment.
- `evidence`: Real mistake SHA/path, revert/review link or timestamped session log, plus regression
  proof with the rejecting diagnostic and corrected-pass result. Record the prevention level separately
  from proof, normally a test. For log-only failures, replay logged inputs at the real enforcer boundary
  in an isolated worktree; name boundaries not exercised. Link long evidence instead of pasting logs.
  Mark missing proof explicitly; a proposed check is not a demonstrated enforcer.
- `added`: ISO date `YYYY-MM-DD` when the rule first entered the registry.

### Maintain entries

1. Read the table before adding a rule; update an existing invariant instead of duplicating it.
2. Record each real occurrence and why it qualifies as a class under [class prevention](class-prevention.md).
3. On a repeat of an unenforced rule, move up at least one level in the priority order.
   Prefer the highest feasible level, not merely the next one. Do not reset the date or erase
   historical proof; record the enforcement migration in the evidence link or adjacent note.
4. If an allegedly enforced rule recurs, inspect the bypass or missing scope and repair the
   enforcer. Do not add a prose reminder beside a broken check.
5. Add and prove checks as [class prevention](class-prevention.md) steps 4 and 7 require before claiming enforcement.
6. Keep exceptions as class prevention step 8 requires. The registry may link that line, but it cannot serve as a
   blanket waiver.
7. Remove obsolete weaker rules and duplicate reminders when the stronger enforcer replaces them.
   If moving up is genuinely impossible, preserve the entry as unenforced and escalate with
   the higher-level failure reasons rather than claiming the recurrence is solved.
