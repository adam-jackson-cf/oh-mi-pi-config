# Designing Jev judgments

How to shape a process around Jev (TypeSafe's System One model) and write the questions it asks.
The live docs are the source of truth (see the skill's TypeSafe docs section); this page gives
direction and the lessons learned building real processes. Adapted in part from TypeSafe's
`typesafe-ai` agent skill (MIT License, Copyright (c) 2026 TypeSafe AI).

## Programming model

Jev understands natural language and returns typed answers with probabilities, not generated text
or explanations. Code owns the workflow; Jev supplies programmable common sense where ordinary
code needs semantic understanding. Keep known rules, calculations, exact lookups, comparisons,
counting and execution in code. Preserve the user's stack and scope; add Jev only where a judgment
helps.

## Docs map

Read targeted pages, not the whole site. Append `.md` to a page path for Markdown; resolve
relative links against `https://docs.typesafe.ai`.

| Task | Start here |
| --- | --- |
| Programming model | `concepts/system-one`, `concepts/how-to-build-with-system-one` |
| Coding-agent context | `introduction/coding-agents` |
| What to build | `concepts/use-case-map`, `patterns`, `cookbooks` (closest recipe) |
| State and questions | `concepts/state`, `primitives`, its primitive page, `primitives/advanced` |
| Uncertainty | `confidence`, `patterns/confidence-routing`, self-consistency cookbooks |
| Known model weak spots | `model-jaggedness/<model version>` |
| API code | `api`, `sdk/python`, `sdk/javascript`, `models` (limits) |
| Older integration | `migrating-to-v1` and the installed SDK's reference |

## Find the useful shape

Start from what the application will show, select, change or hand off, and work back to the
judgments it needs. Consider more than classification:

- **Route deterministically, then judge.** Rules and parsers decide what they can and route only
  ambiguous cases to Jev; Jev's answers are then confirmations on pre-filtered cases, which keeps
  false-alarm volume low. Fix routing gaps in the deterministic stage, not with a broader question.
- **Route and fill known arguments.** One request selects a handler and its typed parameters; ask
  branch-specific questions up front and consume only the relevant answers (`cookbooks/
  function_calling`, `patterns/fan-out`).
- **Select instead of generate.** Find candidate values or spans in code; a judgment selects the
  intended one; code copies or normalizes it (`cookbooks/pre_parsed_value_extraction_cookbook`).
- **Find and judge evidence.** Retrieve candidates, judge relevance, keep the useful context
  (`cookbooks/rerank_typesafe`, `cookbooks/hierarchical_classification`).
- **Turn judgments into reusable data.** Score dimensions once; code or user controls apply
  weights, thresholds and views. With labelled outcomes the answers become classical ML features
  (`patterns/composite-scoring`, `cookbooks/autoresearch_feature_discovery`).
- **Verify and escalate.** Check claims or fields against their evidence; send uncertain or
  failing cases to a person or a reasoning model (`cookbooks/citation_check`,
  `cookbooks/sde_cascade`).
- **Respond to changing state.** Code keeps goals and observations; fresh judgments guide the next
  bounded step. Keep inferred state apart from observed facts, and check a result is still fresh
  before applying it to a changed situation.

For an open-ended request, offer the few directions that best serve the goal and recommend one.
For a concrete request, pick the pattern and build.

## Write the policy before the questions

A judgment can only be as consistent as the policy it serves. Before writing questions, write the
decision policy as numbered rules ("first rule that decides wins"), with examples on both sides.
Then split it:

- Rules code can decide exactly (path is a fixture, directive names a rule code, command is not
  chained to a commit) are code. Do not ask Jev what a regex can answer.
- Only the perceptual parts ("is this a working directive or text mentioning one?", "does the
  behaviour the removed test covered still exist?") become questions.
- The same written policy is the labelling rubric (see [development.md](development.md)). If two
  near-identical cases would get different labels, the policy is incomplete, not the model.

## Rules state conditions; approval is a mechanism

Each rule or policy line states one condition of the thing being judged, and it either holds or it
does not. It never names who may do it: no "unless the user asked", no "only through the
maintainer", no permission clauses.

- Qualifiers that describe the change itself (a scratch path, a fixture file, a rule-coded
  directive) are part of the condition and stay in the rule.
- Qualifiers about the actor or permission belong to the action mechanism: a confirm prompt, a
  block, a review gate. Its outcome is recorded beside the decision (for example `userDecision`),
  not fed back into the judgment. Jev's state carries no "who asked" signal.
- Messages shown on a positive name the condition that matched ("matches rule X: a change to the
  guard code"), not a guess at intent ("looks like it weakens a gate").
- Labels then follow from the change alone, which keeps them decidable and consistent.

## Design each question

| Need | Primitive | Distinction |
| --- | --- | --- |
| One of a defined set | Choice | Picks one; the distribution compares options |
| Whether a condition holds | Noul | P(yes); no separate confidence; one per label if several fit |
| Degree on a described scale | Score | Probability-weighted position on ordered levels |

- **State.** Give each question what it needs and no more: source text, identities,
  relationships, policies, current facts. Use named JSON fields; reference them with backticked
  paths such as `evidence[0].matched_line`. Large state full of unrelated detail costs accuracy,
  so filter in code first. Facts code can compute (is a test file, assertions removed, checks
  skipped, other files changed) go in as computed fields, not as raw text for Jev to work out.
- **Instructions and criteria.** The judgment goes in `instructions`; `criteria` define every
  possible answer and must agree with the instructions. Question ids are not sent to the model, so
  the text must carry the full meaning. Strings suit simple questions; use structured forms when
  definitions, contrasts, exclusions or examples help: `{question, focus}` instructions and
  `{what, not_for, examples}` criteria. Score levels describe concrete, standalone situations.
- **Atomic, not literal.** One narrow, coherent judgment per question; split independently useful
  dimensions without breaking the relationship being judged. A bounded interpretation is fine;
  atomic does not mean one-sentence or pure extraction.
- **Literal reading.** Jev answers the words written. Name the exact condition and put boundary
  cases in the criteria. When you catch yourself explaining what a question "really meant", that
  explanation is the missing half of the instruction.
- **No match.** Include a no-match or abstain outcome when nothing may fit, or a separate presence
  question when that is independently useful. For selection, check candidate coverage: Jev cannot
  choose an omitted value.

Example from an integrity guard for coding agents. One broad noul per category flagged a third of
legitimate work:

```json
{"suppression": {"type": "noul", "instructions":
  "Does this change add a suppression without a specific, valid reason scoped to the code?"}}
```

The replacement asks atomic questions over a narrow state built by code, and code composes them:

```json
{"d_live": {"type": "noul",
  "instructions": "Is `evidence[0].matched_line` a working suppression directive?",
  "criteria": {
    "true": {"what": "A real directive that silences a check",
             "examples": ["x = f()  # type: ignore"]},
    "false": {"what": "Text that only mentions a directive",
              "examples": ["a docstring describing `# noqa`", "a regex literal"]}}},
 "s_reason_stated": {"type": "noul", "instructions":
  "Does the directive in `evidence[0].matched_line` state a specific reason in its comment?"}}
```

Code then applies the policy: a rule-coded line directive is allowed (escape-hatch codes such as
`no-explicit-any` excepted); otherwise escalate when `d_live` holds and neither a stated reason
nor a technical necessity does.

## Compose

- Ask independent questions over the same state in one request, including useful speculative
  ones; they run in parallel and cannot see each other's answers. State each speculative premise
  explicitly. Use a second request only when an answer is needed to fetch evidence, build new
  state or choose the next options. Measure tokens, cost and end-to-end latency.
- Keep policy explicit in code and raw answers reusable. Weighted scores suit compensating
  preferences; an "any serious violation" rule needs separate conditions. Per-family composition
  rules ("escalate when live and unexplained and not a fixture") are easier to audit and fix than
  one blended score.
- Changing a weight, threshold or display filter need not rerun inference when the evidence and
  question meanings are unchanged; replay stored answers instead.
- A learned composition (logistic regression, gradient boosting over answer probabilities) needs
  real positives from many independent sessions. Synthetic positives that lack the context real
  cases carry teach the model the wrong signal. In the integrity guard, synthetic hacks had no user
  request, so a model learned "no user request means violation". The deeper lesson: a signal
  about who asked never belonged in the state; the approval mechanism records it.

## Read probabilities

- A noul near 0.5 means yes and no are similarly likely, not a medium-strength yes. Treat the
  0.4–0.6 band as uncertainty: route it (ask the user, a reasoning model, or a second question)
  rather than forcing it through a single cut-off.
- Choice and Score confidence summarize how concentrated the distribution is, not whether the
  answer is right or safe to act on. Several acceptable options can spread probability; low
  confidence need not invalidate a harmless preference. Ignore uncertainty on unused branches.
- Do not expect structural identities between separate questions: P(X) and 1 − P(not X) differ,
  and a noul threshold does not transfer to a Choice over the same idea.
- Choose thresholds on your own labelled data and the consequences of each action. Cookbook
  thresholds and demo results are examples to evaluate, not rules. Typed output guarantees the
  interface, not truth; the models are trained for calibration, but validate in your domain.

## Integration checklist

- Record every decision: version, state, questions, answers, verdict, resolved model, cost and
  latency. Bump the version on every question, threshold, rule or composition change.
- Redact secrets before state leaves the process and before it is logged; keep API credentials
  server-side.
- Build state from every relevant line, not the first rule hit: a guard that showed Jev only the
  first matching line let a blanket suppression through behind an allowed one.
- Check condition qualifiers against your own tests and smoke runs: a "scratch directory is legit"
  qualifier also covered test repositories created under the OS temp directory.
- Parse commands the way the tool does: global options (`git -c k=v commit`) hid commits from a
  screen that only expected `git commit`.
- Known model weak spots (counting, arithmetic, date comparison, indirection, adversarial text,
  generation) belong in code or another model; check the jaggedness page for the version in use.
- Automated actors work around guarded resources, never through exemptions. A loop that maintains
  a process's own rules edits a copy (a git worktree), passes a deterministic gate and an
  independent reviewer, and a script outside any agent deploys the result. An environment-variable
  exemption can be forged by any agent that learns of it. If an unattended step must modify the
  guarded resource, do it in that non-agent deployment script, after the gate and the review.
- Guard the gate with what it guards: the rules, the fixtures, the regression gate and the
  reviewer prompts. A rule over a resource must cover every route to it (edit, write, read,
  search, list, shell); state the gaps that remain, such as broad searches that never name the
  path.
- Advisory text is not enforcement. An instruction file next to protected material may never be
  loaded (context discovery rules, subagents). Keep a process's maintenance tooling out of
  auto-advertised surfaces such as skills and prompts, and route access through the action
  mechanism.
