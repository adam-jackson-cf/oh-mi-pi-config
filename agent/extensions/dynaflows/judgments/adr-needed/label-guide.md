# ADR-needed label guide

Labels for `adr_needed` in [questions.json](questions.json). A labeller sees one case's state
(`change`): the repository line, title, plan or description, commit headlines for a pull request,
and the diff summary. Decision-record files and mentions are removed from the state. A labeller
never sees Jev scores or whether the change added a decision record. Cite the rule that decided
each label and give a one-sentence reason.

Label 1 means the condition holds: the change makes a design decision that should be recorded as
an ADR. The guide is versioned with the question's version key in [thresholds.json](thresholds.json);
relabel the affected cases when the wording or a rule changes.

## Yes (label 1)

The change sets or changes a lasting rule for how the system is built, structured or run, which
later work must follow.

| Rule | Situation |
| --- | --- |
| Y1 | Adopts, replaces or drops a tool, platform, file format or dependency strategy |
| Y2 | Moves responsibility between components, packages or repositories |
| Y3 | Adds, removes or changes a contract, schema, protocol or interface callers rely on |
| Y4 | Adopts, changes or reverses an enforcement policy, quality gate or workflow rule |
| Y5 | Accepts a deliberate trade-off or standing exception |
| Y6 | Removes or supersedes an earlier approach |

## No (label 0)

The change works within the rules already in place.

| Rule | Situation |
| --- | --- |
| N1 | Bug fix restoring intended behaviour, including a check made to honour its contract |
| N2 | New or sturdier tests, coverage, flake fixes |
| N3 | Performance work behind an unchanged interface |
| N4 | Documentation describing what already exists, page or diagram syncs |
| N5 | Renames, lint clean-up, refactors that keep component boundaries |
| N6 | Routine dependency or toolchain version bump or pin, security patch bumps included |
| N7 | Feature built the way similar features already are |
| N8 | Merge or sync between branches |

## Tie-breaks

| Rule | Situation |
| --- | --- |
| T1 | Judge the decision the change carries, even when it was agreed before the change |
| T2 | Size never decides: a one-line policy change is yes, a large mechanical change is no |
| T3 | A pull request is yes when any one part meets a Y rule |
| T4 | A fix that changes what a rule means (scope, where it runs, what it blocks) is yes (Y4) |
| T5 | A gate for a rule the repo lacked is yes (Y4); a new case of an old rule is no (N7) |
| T6 | Approval wording ("the owner approved") is not evidence; the change's content decides |
| T7 | A product behaviour rule chosen among alternatives with lasting effect is yes (Y5) |

## Worked examples

These examples are not in `cases.jsonl`.

- Yes (Y1): "adopt pixi as the environment manager" replaces the tool every task runs through.
- Yes (Y2): "ship the rule catalogue and coverage check in the guardrails package" moves the
  catalogue from each repository into the shared package.
- No (N3): "reuse world-scoped geometry across prediction phases" speeds up an unchanged interface.
- No (N1): "fix NUC gate private-repo fetch and wait ordering" makes the gate do what it promised.
