# Question design

## Objective

Write each question so Jev can answer it from the state it is given, and keep out judgments Jev
cannot make reliably.

## Guidance

- When a judgment will gate behavior or judge outputs, write its questions only after the framing
  STOP
  in [building eval data](building-eval-data.md) is answered and its failure modes are discovered.
  Until then, list draft questions in the STOP message only.
- Choose the primitive by what the answer means, then read its page:
  - One of a defined set: [Choice](https://docs.typesafe.ai/primitives/choice.md).
    It picks one option, and its distribution compares competing options.
  - Whether a condition holds: [Noul](https://docs.typesafe.ai/primitives/noul.md).
    It returns the probability of yes with no separate confidence; use one Noul per label when
    several may apply.
  - Degree along a described dimension: [Score](https://docs.typesafe.ai/primitives/score.md).
    It returns a probability-weighted position on ordered levels; use comparable per-item Scores for
    graded ranking.
- Give each question enough relevant state to answer: source text, identities, relationships,
  policies and current facts.
- Supply the reference evidence each judgment needs; Jev cannot judge facts it has not seen.
- Prefer named JSON fields when context has several parts, and reference nested state with
  backticked paths such as `ticket.messages[0].text`.
- Put the judgment in instructions, and define its possible answers in criteria.
- Include the complete meaning in the question; question IDs are for code and are not sent to the
  model.
- When state holds untrusted text, such as a trace or a user message, tell the question to treat it
  as data, not instructions.
- Ask one narrow, coherent judgment per question.
- When several reasons or failure modes can trigger one flag or eval verdict, ask one Noul per
  reason or mode,
  in parallel over the same state, with a checklist in criteria of what counts and what is excluded,
  and combine "any" in code rather than asking one broad "did anything go wrong" question.
- Prefer binary criteria over graded ones for eval judgments.
- Split independently useful dimensions without destroying the relationship being judged.
- Treat a bounded action selection or a contextual interpretation as one valid judgment;
  atomic does not mean literal fact extraction or a one-sentence limit.
- Use strings for simple questions.
  Use structured objects or arrays when definitions, contrasts, exclusions or examples clarify
  instructions or criteria.
- Write Score levels that describe concrete situations and stand on their own.
- Include a no-match outcome when nothing may fit, and use a separate presence judgment when it is
  independently useful.
- For source-value selection, check candidate coverage; the model cannot choose an omitted value.
- Check the jaggedness page for the pinned model version, and design around weak spots that apply to
  the input,
  such as negation, dates, long state or option order.
- Keep these judgments out of Jev:
  - Checking a derivation, such as math, code correctness or logic: compute or verify it in code, or
    send it to a reasoning model.
  - Factuality without supplied reference evidence: confidence does not expose errors there,
    so supply the evidence or use another check.
- Test comparisons where the wrong answer is more elaborate on labeled cases before relying on Jev.
  For pairwise preference, judge both presentation orders and average the probabilities.
