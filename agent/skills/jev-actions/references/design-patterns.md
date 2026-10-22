# Design patterns

## Objective

Choose which judgments the application needs and the pattern that combines them.

## Guidance

- Start from the behavior the user wants: what the application will show, select, change or hand
  off.
  Work back from it to the judgments it needs.
- Keep known rules, calculations, exact lookups and execution in code.
- Preserve the user's chosen stack and scope, and add TypeSafe where semantic understanding helps.
- Do not limit the design to classification.
  Combine primitives around the user's goal, including ideas that fit no established recipe.
- **Route and fill known arguments**: a request can select a handler and its typed parameters.
  Ask useful branch-specific questions up front, and consume only the relevant answers.
  Read the worked examples in
  [function calling](https://docs.typesafe.ai/cookbooks/function_calling.md)
  and [speculative fan-out](https://docs.typesafe.ai/patterns/fan-out.md).
- **Select instead of generate**: find candidate values or source spans in code,
  use a judgment to select the intended one, then copy or normalize it.
  Code can also assemble source text into a formatted document or reading guide.
  To pick tools, describe the goal, ask one Noul per candidate tool, and load only the top few into
  an agent's context.
  Read the worked examples in
  [value extraction](https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook.md)
  and [structure recovery](https://docs.typesafe.ai/cookbooks/autoformat.md).
- **Find and judge evidence**: retrieve candidates, compare their relevance to a query, and select
  useful context.
  For an agent's own context, give each chunk (tool output, earlier turn, file excerpt) a Score
  with levels such as omit, short summary, long summary and full text, and let code rebuild the
  context for the next step.
  Read the worked examples in [reranking](https://docs.typesafe.ai/cookbooks/rerank_typesafe.md)
  and
  [hierarchical classification](https://docs.typesafe.ai/cookbooks/hierarchical_classification.md).
- **Turn judgments into reusable data**: score dimensions once,
  then let code or user controls change weights, thresholds, rankings and views.
  With labeled outcomes, those signals can become classical ML features.
  Read the worked examples in
  [composite scoring](https://docs.typesafe.ai/patterns/composite-scoring.md)
  and [feature discovery](https://docs.typesafe.ai/cookbooks/autoresearch_feature_discovery.md).
- **Verify and escalate**: check specific claims or fields against their evidence,
  and send uncertain or failing cases to a person or a reasoning model.
  Check a compressed or summarized output the same way: does it still contain what the task needs?
  When the flag itself is the escalation, one threshold sending a case to a person, gate it as
  [threshold tuning](threshold-tuning.md) describes.
  Add an escalation band on uncertainty only as [error analysis](error-analysis.md) describes.
  Read the worked examples in
  [citation checks](https://docs.typesafe.ai/cookbooks/citation_check.md)
  and [extraction cascades](https://docs.typesafe.ai/cookbooks/sde_cascade.md).
- **Judge traces and outputs**: use Jev as a low-cost eval judge over agent traces or generated
  outputs,
  with one Noul per failure mode as [question design](question-design.md) describes.
  Keep exact checks, such as tool call status, in code.
  Discover the failure modes and collect cases as [building eval data](building-eval-data.md)
  describes.
  Tune each judge's threshold against human labels, as [threshold tuning](threshold-tuning.md)
  describes, before trusting it.
- **Respond to changing state**: code can keep goals and observations while fresh judgments guide
  the next bounded step.
  Keep inferred state distinct from observed facts, and check freshness before applying a result to
  a changed situation.
- For an open-ended request, offer the few directions that best serve the user's goal, and recommend
  a starting point.
- For a concrete request, choose the relevant pattern and build without a brainstorm detour.
