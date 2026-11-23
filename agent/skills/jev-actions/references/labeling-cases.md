# Labeling cases

## Objective

Produce labels that measure exactly what each question asks, with their provenance recorded.

## Guidance

- Before labeling, write a one-page label guide per question version: its criteria word for word as
  the yes and no definitions, exclusions, tie-breaks and two worked examples per label; relabel
  affected cases when the wording changes.
- Let observed outcomes decide labels where they exist; for every other case of a Jev judgment
  change, the `innovation-council` agent does the label pass and the main agent arbitrates.
- Label blind: labelers see the unit and its evidence, never the expected verdict, a score, Jev's
  answer, the process verdict or another label, and cite the policy rule that decided each label.
- Council label pass: at least two independent council labellers per case, sessions with no shared
  context, ideally from different model families, each appended as an `agent` row with the labeller
  in `by`; when they agree, append the agreed label as `agent_reviewed`.
- Arbiter: the main agent decides each disputed or unconfirmed case individually, shown only the
  case, its evidence and the frozen guide, blind to scores and to every earlier label.
  Append each decision as an `arbiter` row with the arbiter in `by`, its own `timestamp`,
  `confirmed` `individual`, `blind` true and the reason in `note`.
  A decision made over several cases at once is `confirmed` `bulk` and counts as none.
- Record a person's label as provenance `owner`; it never counts, and no labeller or arbiter sees it.
- Give every council labeller a working shell or readable copy of each repository its cases come
  from; labellers that cannot open a repository abstain.
- Treat another project's published labels as model labels unless their provenance shows a person
  made them.
- Use model pre-labels only to order review, never to skip confident cases;
  LLM judges tend to repeat Jev's confident errors.
- When many labels cite one rule, check whether it was read too broadly, clarify it, and re-derive
  those labels together.
- Near-duplicates with opposite labels mean the policy does not decide the case: settle the policy
  with the user, as [error analysis](error-analysis.md) requires, and relabel before blaming the
  model.
- Any labeller or the arbiter that cannot decide a case from the written policy labels it `abstain`
  with the reason in `note`; `unsure` is not a label value.
  An abstain counts as no label; keep it out of tuning as review-routing or question-splitting
  evidence.
- Record each label's provenance:
  - `arbiter`: the main agent decided it individually and blind.
  - `outcome`: an observed result decided it, such as a support resolution or a reverted change.
  - `agent_reviewed`: two independent council labellers agreed from the frozen guide.
  - `agent`: any other model label, such as a single labeller or a pre-label.
  - `unknown`: a label found only in `scores.csv`; it counts, and is listed in `shortfalls`.
  - `owner`: a person's label; it never counts.
- `arbiter` and `agent_reviewed` require that no labeller, and not the arbiter, saw a score or an
  earlier label for the case; record `blind` true only then, and otherwise the row counts as
  `agent`.
- After a meaning change, only labels made under the new version key count: a bulk row, or any row
  under an older key, counts as none, and an agent correction under the new key counts only once the
  arbiter decides that case individually.
- Append labels to `labels.jsonl`; never edit `cases.jsonl`.
  To resplit, write a new dated cases file and record which file each scoring run used; never move
  a case whose `test` result has been seen.
  Rows placed in `test` after their scores were seen become `dev`; the next `test` comes from new
  cases.
- A case's counting label is its latest `arbiter` label, else its latest `outcome`, `agent_reviewed`
  or `unknown` label; cases whose counting label is `abstain`, or with only `agent` labels or an
  unresolved disagreement, have none.
  Label set sizes, tuning and `scores.csv` use only counting labels.
- Take counting labels from
  `python3 -B scripts/check_labels.py --cases </abs/cases.jsonl> --labels </abs/labels.jsonl> --json`,
  adding `--version-key <question-id>=<version-key>` for each question after a meaning change:
  - an `arbiter` row lacking `by`, `timestamp` or `confirmed` `individual`, or sharing its arbiter
    and timestamp with another case, counts as none;
  - an `arbiter` or `agent_reviewed` row without `blind` true counts as `agent`;
  - a row of a keyed question under another version key counts as none;
  - `abstain` counts as no label and is reported per question, and `unsure` is rejected;
  - an `owner` row counts as none;
  - it also checks unit coverage, and fails when every case has its own unit.
