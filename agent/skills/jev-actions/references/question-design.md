# Question design

## Objective

Write each question so Jev can answer it from the state it is given, check it against the question
rubric, and keep out judgments Jev cannot make reliably.

## Guidance

- Use this runbook when writing or reviewing questions; a request for only this ends here, listing
  later sections as next steps.
- Choose the primitive by what the answer means, then read its page:
  - One of a defined set: [Choice](https://docs.typesafe.ai/primitives/choice.md).
  - Whether a condition holds: [Noul](https://docs.typesafe.ai/primitives/noul.md), the probability
    of yes with no confidence.
  - Degree along a described dimension: [Score](https://docs.typesafe.ai/primitives/score.md), a
    position on ordered levels; comparable per-item Scores give graded ranking.
- Start from the template your [design patterns](design-patterns.md) row names, and fill each slot:
  - `<subject.path>`: the backticked state path judged, with the option list named for `any_fits`;
    `<observable-property>`: what a reader would see there, phrased so yes is the case code acts on.
  - `<counts-case>`, `<counts-edge>`: a typical yes, and a borderline yes;
    `<not-counts-case>`, `<near-miss>`: a typical no, and a no that looks like yes.
  - `<option-name>`, `<option-purpose>`: a descriptive option key, and the observable input traits
    it handles, never "would succeed".
  - `<dimension>`, `<level-situation>`: the scale, and one standalone situation per level, lowest
    first.
  - Send `route` and `any_fits` as two questions without the wrapper keys; give a Score one
    `<level-situation>` per level.
  - Drop the data clause, the template's first sentence, only when no state text is untrusted.
- Give each question the state and reference evidence it needs: source text, identities,
  relationships, policies and current facts; Jev cannot judge facts it has not seen.
  Names alone rarely carry a judgment: send each item's own bounded content, such as its signature,
  description and first lines of body; add use sites only when the criteria read use, and about 3
  member names for an item judged against a container's name. A fact the proposer measures stays in code.
- Give each item only the state its question reads (Q18); fields built for other questions sway
  answers, and an empty list reads as a signal.
- Sameness: ask for the parts that make two items the same under the policy, read side by side,
  such as the same steps on the same input for the same purpose; ask whether either could replace
  the other in every use only when the policy is about names. `false` names what sets them apart
  (owner, contract, unit, external API), one calling, wrapping or testing the other, and boilerplate.
- Decompose before rewording: when the written policy's violation needs several conditions a
  labeller can observe apart, such as "the answer cites a source", "the source is in state" and
  "the source supports the answer", write one question per condition as a Chain or Place from
  [design patterns](design-patterns.md), not one question that asks for the whole verdict.
  Label each link separately, so [error analysis](error-analysis.md) can see which link fails.
  A condition that only marks another's boundary belongs in that question's `false` criterion.
- Use named JSON fields for multi-part context; question IDs are not sent to the model, so
  instructions and criteria carry the complete meaning.
- Split independently useful dimensions without breaking the relationship judged; a bounded action
  selection or contextual interpretation is one judgment.
- Give each defect one question in a judgment (Q16): check each positive's reason against the other
  questions' criteria; a reason another question's criteria accept is question overlap, so narrow
  one question or move the case before labelling.
- Prefer binary criteria for eval judgments, and objects or arrays when they clarify criteria.
- For source-value selection, check candidate coverage; Jev cannot choose an omitted value.
- Design around the pinned version's jaggedness-page weak spots that apply to the input, such as
  negation, dates, long state or option order.
- Test comparisons where the wrong answer is more elaborate on labeled cases before relying on Jev;
  for pairwise preference, average both presentation orders.
- Ship a question only when every applicable question-rubric check below is yes; report any no you
  keep, and why.
  Deliver each as filled JSON under a kebab-case question ID, such as `summary-has-email`; write
  `questions/` files only when eval data is stored.
- To review existing questions, take caller code and a sample state from the repository or the
  user, asking once without ending the turn; run Q1-Q5 on the question text alone, and mark checks
  you cannot run `not checked`.
  Report each no with its rewrite; write fixes only when asked.
  Run Q14 and Q15 against the written policy after the Terms step in
  [framing the decision](framing-the-decision.md), and Q16 across every question of the judgment.
- Quote each question's written policy line in the report.
  Q14 is no when that line relies on change over time, history or future use, or uses a word about
  change such as outgrow, grows, becomes, still, no longer or over time, and state holds one snapshot.
- Q15 also needs each policy example the criteria rely on to name an item that exists, or the policy
  to mark it hypothetical; otherwise take the term to the user.
- Near-misses for Q15 come as boundary pairs on both `true` and `false`.
- Unlistable items: when code cannot list Q10's items, ask for the strongest instance and
  require the judge to cite both items by name from state; otherwise narrow the question to a
  listable pair.
  A question about a whole unit that is true when any part qualifies, such as a responsibility, a
  file or a pair of terms, is a list question under Q10.
- Judge meaning on instructions and criteria together: moving text between them keeps the meaning,
  and adding, removing or narrowing an exclusion anywhere is a new meaning.
  Treat a rewrite that changes the subject path, the state it reads, or what the question includes or
  excludes as a new question meaning, even when it aims to keep the meaning: old labels do not carry
  over.
  Relabel a sample blind under the new text, at least 10 cases per label or all if fewer, including
  every case whose label cites the changed part; reuse the old labels only when every sampled label
  matches, else relabel all affected cases.
  A path change is a new meaning unless the new path holds every item that the old labels' notes
  cite; check this on at least 3 labelled cases per label.
- When fixing splits a question, give each part a new question ID and `thresholds.json` entry,
  combine them in code ("any" for alternative triggers, "all" for a Chain's links), mark the old
  entry retired, and ship each part enforced at the cost-rule default from
  [threshold tuning](threshold-tuning.md) under the user's recorded override until it is tuned; a
  report lists these as proposals.

Q1-Q5 apply to the instructions and to each criterion separately.

| ID | Check | Usual fix for a no |
| --- | --- | --- |
| Q1 | Instructions hold one question sentence, after at most the data clause | Drop schema descriptions; rules to criteria |
| Q2 | No "and" or "or" joins two conditions, unless one implies the other | One question per condition |
| Q3 | No "unless", "except" or "other than" clause | A `<near-miss>` of each question whose yes cases it removes; list unclear ones as assumed |
| Q4 | Every definition, inclusion, exclusion and tie-break sits in criteria | Move it to its criterion |
| Q5 | Each criterion or level is a concrete situation, not "not X" or the question restated | Describe what a reader sees |
| Q6 | High probability means the condition code acts on holds | Rephrase so yes is flagged; a missing relation: ask it, naming its forms; score 1 - P |
| Q7 | The subject is a backticked state path | Name a path that keeps Q13 yes |
| Q8 | It asks what the state shows, not what someone would do, think or file | Ask the observable property |
| Q9 | No part can be computed by code from the state | Code checks what a pattern detects, such as emails or IDs, with no question, or passes it as a fact |
| Q10 | It judges one item, not whether any item in a list qualifies; Q11's Noul is exempt | One question per item, "any" in code; see unlistable items |
| Q11 | A Choice is paired with a Noul asking whether any option fits, unless a catch-all option always fits | Add the Noul |
| Q12 | A person shown only this state and question could answer at a glance | Fix the state, then the wording |
| Q13 | The subject path holds the item judged and anything it is compared with | Widen the path, or add the compared items as named fields |
| Q14 | The written policy's condition is observable in the state sent when production asks | Add history to state, change the asking condition, or demote it |
| Q15 | Every criterion clause traces to a clause of the written policy | Take an unsourced clause, such as a carve-out, to the user as a policy change |
| Q16 | No other question's criteria accept this question's positive reasons | Narrow one question or move the case |
| Q17 | It judges or places one item, or one code-proposed pair of evidence units, never two containers | A Place Choice, or a both-orders pair question |
| Q18 | The state holds only fields this question reads | Drop fields built for other questions |
| Q19 | Code asks only when the state can show the premise | Skip items with no evidence, or judged against a generic name or a memberless container |
