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
- Use named JSON fields for multi-part context; question IDs are not sent to the model, so
  instructions and criteria carry the complete meaning.
- Split independently useful dimensions without breaking the relationship judged; a bounded action
  selection or contextual interpretation is one judgment.
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
- When fixing splits a question, give each part a new question ID and `thresholds.json` entry,
  combine them with "any" in code, mark the old entry retired, and keep the parts `provisional`;
  a report lists these as proposals.

| ID | Check | Usual fix for a no |
| --- | --- | --- |
| Q1 | Instructions hold one question sentence, after at most the data clause | Drop schema descriptions; rules to criteria |
| Q2 | No "and" or "or" joins two conditions, unless one implies the other | One question per condition |
| Q3 | No "unless", "except" or "other than" clause | A `<near-miss>` of each question whose yes cases it removes; list unclear ones as assumed |
| Q4 | Every definition, inclusion, exclusion and tie-break sits in criteria | Move it to its criterion |
| Q5 | Each criterion or level is a concrete situation, not "not X" or the question restated | Describe what a reader sees |
| Q6 | High probability means the condition code acts on holds | Rephrase so yes is flagged |
| Q7 | The subject is a backticked state path | Name the path |
| Q8 | It asks what the state shows, not what someone would do, think or file | Ask the observable property |
| Q9 | No part can be computed by code from the state | Code checks what a pattern detects, such as emails or IDs, with no question, or passes it as a fact |
| Q10 | It judges one item, not whether any item in a list qualifies; Q11's Noul is exempt | One question per item, "any" in code |
| Q11 | A Choice is paired with a Noul asking whether any option fits, unless a catch-all option always fits | Add the Noul |
| Q12 | A person shown only this state and question could answer at a glance | Fix the state, then the wording |
