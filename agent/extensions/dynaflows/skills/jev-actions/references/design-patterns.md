# Design patterns

## Objective

Choose the judgments the application needs, the approach that combines them, and each question's
shape and template.

## Guidance

- Use this runbook when designing new judgments; a review of existing questions skips it.
- Work back from what the application will show, select, change or hand off to the judgments it
  needs, keeping the user's stack and scope.
- For an open-ended request, build the direction that best serves the goal, and list the others in
  the result; for a concrete request, build it.
- Take the first of the rows Gate to Eval judge whose signal matches each judgment; Route, not Gate,
  picks which of two or more options runs, and Gate decides only whether one step runs.
  A Route also follows [route decisions](route-decisions.md).
  Add the Face-value, Changing state, Chain or Place row on top when its signal also holds.
  Start from the named template in `assets/templates/`: `noul-condition.json`, `choice-route.json`
  or `score-dimension.json`.

| Signal | Approach | Question shape | Template |
| --- | --- | --- | --- |
| A costly or risky step should run only for some inputs | Gate | One Noul per trigger reason; "any" in code | `noul` |
| One of several handlers, tools, models or queues must run | Route | A Choice whose options say what each is for | `choice` |
| Code retrieved candidates to order, filter or keep in context | Rerank | One Noul per query and candidate, or a Score per chunk | `noul`, `score` |
| Code found candidate values or spans; one is intended | Select | A Choice over the candidates | `choice` |
| A property is graded on a scale, or reused as data | Grade | One Score per dimension; weights in code | `score` |
| Claims, fields or a summary must match supplied evidence | Verify | One Noul per claim or field | `noul` |
| Traces or outputs need a verdict per failure mode | Eval judge | One Noul per failure mode; exact checks in code | `noul` |
| The input must be flattened before it is judgeable at a glance | Face-value | Pre-process in code, then the row it reduces to | that row's |
| An agent's next step depends on fresh observations | Changing state | Fresh judgments per step; code keeps goals | that row's |
| A violation needs several conditions to hold at once | Chain | One Noul per necessary condition; "all" in code | `noul` |
| An item's fit, membership or identity among existing items | Place | A Choice over code-listed alternatives, with a no-fit option | `choice` |

- Worked examples: Gate [confidence routing][cr]; Route [function calling][fc], [fan-out][fo];
  Rerank [reranking][rr], [hierarchy][hc]; Select [extraction][ve], [structure][sr];
  Grade [composite][cs], [features][fd]; Verify [citations][cc], [cascades][ec].
- In Changing state, keep inferred state apart from observed facts, and check freshness first.
- Code proposes, Jev judges, code combines: code lists the candidate items, Jev answers one narrow
  question per item, and code counts, compares and aggregates. Never ask Jev to search a large
  state for any qualifying item or pair.
- Judge the evidence unit: ask about the smallest item whose state holds the evidence a labeller
  would cite, such as one proposed pair of passages or one file, never the container holding it,
  and let code combine the answers, for example by the maximum per container. A container-level
  question dilutes one decisive item among unrelated ones; an item merged from several occurrences,
  such as every definition of one word, is a container too. The item is one code holds whole, such
  as a symbol, file or passage, never a fragment cut from one, such as a word inside a name.
  Code proposes, and keeps its score, at the unit the rule acts on, such as the pair to merge or the
  file to move, picks the evidence units inside it, and reports each finding there.
- Keep the proposer's score: the code that proposes candidates usually measures something, such as
  a similarity, a count or a rule match. Combine it with Jev's answer in code, for example as a
  product, so code supplies what it can measure and Jev removes the cases code cannot tell apart.
  Measure as arms: the literal rule, the written policy's observable condition applied to code
  facts with no weights or added features; the proposer's score alone; each Jev question alone; and
  each Jev question combined with the proposer. When a deterministic arm meets the target, ship the
  one closest to the written policy as a check instead of a judgment. When the condition has several
  code readings, measure each, narrowest first. Measurement decides the end state: an arm counted on
  fewer held-out flags than tuning needs may order arms but never decides it.
  Every condition in a proposer filter or Chain link traces to a clause of the written policy or an
  owner ruling; a feature that is only evidence for a clause is an optional arm, never a required
  condition.
- Score the violation itself: the answer that drives a flag must state the policy's condition, not a
  correlate of it. "Jev would place this item elsewhere" or "these share a topic" can rank well and
  still flag mostly clean cases.
- Chain: each link is a simple observable condition, such as "is this file run by a person" and
  "does it only read the package"; code asks later links only for items earlier links and code
  facts keep, and flags only when every link holds, which raises precision where "any" lowers it.
- Place: Jev places one item among options well and compares two items poorly. When an item has
  one right home among mutually exclusive alternatives, such as sibling folders, owners or routes,
  place it among them and let code flag when an alternative beats the current placement by a
  margin. When several alternatives can each be a separate violation, such as duplicates or
  conflicting names, judge each code-proposed pair and take the maximum in code. For a policy not
  about placement, a Place question is only an arm. To judge a pair, judge one code-proposed pair
  of evidence units, such as two passages rather than the folders or documents holding them, ask it in both orders and
  average, and combine the answer with the proposer's measured similarity in code.
- Set each threshold from which mistake costs more, as [threshold tuning](threshold-tuning.md)
  describes; an uncertainty band needs an [escalation band](escalation-band.md) check.
- Send these to code or a reasoning model instead, saying so in the result, never routing them by
  Jev's confidence:
  - a second judgment that needs the first one's answer inside the same step;
  - derivations such as math, dates, counts, code correctness, behavioural equivalence or logic;
  - open-ended rankings, such as "is this worth attention", with no observable property;
  - state beyond the pinned model's limit on the [models](https://docs.typesafe.ai/models.md) page;
  - factual claims with no reference evidence in state.

[cr]: https://docs.typesafe.ai/patterns/confidence-routing.md
[fc]: https://docs.typesafe.ai/cookbooks/function_calling.md
[fo]: https://docs.typesafe.ai/patterns/fan-out.md
[rr]: https://docs.typesafe.ai/cookbooks/rerank_typesafe.md
[hc]: https://docs.typesafe.ai/cookbooks/hierarchical_classification.md
[ve]: https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook.md
[sr]: https://docs.typesafe.ai/cookbooks/autoformat.md
[cs]: https://docs.typesafe.ai/patterns/composite-scoring.md
[fd]: https://docs.typesafe.ai/cookbooks/autoresearch_feature_discovery.md
[cc]: https://docs.typesafe.ai/cookbooks/citation_check.md
[ec]: https://docs.typesafe.ai/cookbooks/sde_cascade.md
