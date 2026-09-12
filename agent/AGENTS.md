# **CRITICAL** Must follow behaviour rules - how you carry out actions (always active)

## Security Requirements

- **NEVER** commit, echo, print, or log API keys, tokens, passwords, or other
secrets in command output or transcripts
- **ALWAYS** use presence checks instead of value printing when verifying
environment variables
- **ALWAYS** mask sensitive values if display is required; show only the first
and last 4 characters

## Shaping Completion Response Messages

- Clarity applies to every user-facing response. `excluded_content` exempts
  completion formatting, not understandable explanation. Skill-defined output
  templates remain unchanged; improve those outputs at their canonical source.
- Lead with the answer and what it means for the user's objective. Include
  enough background to understand it without reconstructing earlier work.
  Keep the Activity Summary brief.
- When understanding depends on sequence, ownership, structure, or what
  exists versus what is proposed, use the smallest useful flow, tree, or
  comparison. Prefer prose when a visual would add no clarity.

- `excluded_content` is considered:
  - git-only activity
  - tasks that are part of these skill workflows: `analyze-code-quality`,
  `analyze-architecture`, `analyse-security`, `analyze-codebase-integrity`,
  `analyze-agentic-readiness`, `deep-interview`, `experiment-observations`,
  `fault-catalog`, `intent-plan`
  - results surfaced to a user by a skill with a defined output template. The
  skill template takes precedence and must remain unchanged; do not add,
  remove, rename, reorder, or wrap its sections to satisfy completion response
  shaping
  - clarification questions, tool narration, blocked responses, partial
  responses, or other interim responses
- `already_surfaced` is when content (including by description or intent) has
already appeared in:
  - reasoning
  - progress updates
  - earlier response content
  - prior completion responses
- `already_surfaced` prevents repetition, but never omit the minimum context
needed to understand the completion response without rereading earlier
messages.
- Include the following when not `excluded_content`:
  - Start each completion response with a concise bulleted section titled
  `Activity Summary` that states the outcome, work carried out, changed files
  or areas, and verification. Describe agent or tool activity only when it
  materially affects confidence in the outcome.
  - Make the completion response independently understandable zoomed out view. Establish the
  objective, relevant prior state, resulting state, and practical consequence
  before relying on project-specific detail.
  - Identify and surface only the key points needed to answer, decide, or act.
  Label them `K1`, `K2`, and so on; use the natural number of qualifying
  points rather than filling a quota.
    - Put the answer, outcome, or decision in `K1`, together with the minimum
    context needed to understand it and essential verification.
    - For each key recommendation, include the reason it follows from the
    evidence and the consequence for the user's objective.
    - Distinguish observed facts, inferences, and proposed actions when their
    status could otherwise be mistaken.
    - Define unfamiliar project-specific terms and expand opaque identifiers
    on first use, or provide a direct artifact or source reference that does.
    - For an execution recommendation, identify the next concrete action and,
    when relevant, its dependencies, entry conditions, exit conditions, owner
    or owning component, and conditions that keep it blocked.
    - Include `R#` only for a new objective-adjacent topic that materially
    affects the user's decision or next action but falls outside the discussed
    scope.
  - Include a bulleted section titled `Gaps & Contradictions` only when it
  contains new objective-relevant gaps, contradictions, inconsistencies, or
  knock-on effects that are not `already_surfaced`. Use bullet IDs `C1`, `C2`,
  etc. Omit this whole section if none qualify. Never add filler or
  restatements.
  - Include a bulleted section titled `Beneficial Suggestions` only when it
  contains new objective-linked suggestions that are not `already_surfaced`
  and would materially improve the outcome. Use bullet IDs `S1`, `S2`, etc.
  Omit this whole section if none qualify. Never add filler or restatements.
  - Never add an unlabeled recap or conclusion that repeats the
  `Activity Summary` or key points.
- When a substantial task, todo list, or multi-phase plan has completed, add to
your response:
  - Include a bulleted section titled `Architectural Decision Changes` only
  when not `excluded_content` and it contains new runtime design decisions that
  are not `already_surfaced` and materially differ from the user's original
  requirements. Use bullet IDs `A1`, `A2`, etc. Omit this whole section if none
  qualify. Never include it for routine tasks.
  - Include a bulleted section titled `Focused Review` only when it contains
  specific changed files from the completed todo list or multi-phase plan that
  are not `already_surfaced` and had repeated tool or verification failures
  before success, high complexity in the starting or finished code, or high
  blast radius across code or the user journey. Use bullet IDs `F1`, `F2`, etc.
  Name the file, the qualifying characteristic, and the relevant line range.
  Omit this whole section if no changed file qualifies. Never include a generic
  changed-file list, replay individual failed attempts, add filler, or restate
  other sections.
