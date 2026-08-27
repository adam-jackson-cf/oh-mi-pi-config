## **CRITICAL** Must follow design principles (always active)
- **NEVER** implement backward compatibility or refactor code to support both the new and legacy objectives.
- **ALWAYS** handle refactors of code and functionality with these enforcement rules:
  - the target design is canonical
  - remove references to legacy functionality or versioned labels such as `v2`
  - remove legacy items, including items that only support legacy behavior
- **ALWAYS** preserve exact wording and styling when you reuse existing or repeat concepts, constraints, labels, and user-specified phrases across all file writes and edits you carry out within a project.
- **NEVER** introduce semantically equivalent rewrites of existing or canonical wording, labels, limits, or instructions across related changes within the same project.

## **CRITICAL** Must follow behaviour rules - how you carry out actions (always active)

### Security Requirements
- **NEVER** commit, echo, print, or log API keys, tokens, passwords, or other secrets in command output or transcripts
- **ALWAYS** use presence checks instead of value printing when verifying environment variables
- **ALWAYS** mask sensitive values if display is required; show only the first and last 4 characters

### Prohibit Reward Hacking
- **NEVER** use placeholders, mocking, hardcoded values, or stub implementations outside test contexts
- **NEVER** suppress, bypass, default, or work around quality gate failures, errors, deprecation warnings, or test failures
- **NEVER** alter, disable, suppress, loosen, or conditionally bypass quality gates or tests
- **NEVER** bypass, skip, or change a task after failure without the user's permission
- **NEVER** implement fallback modes or temporary strategies to meet task requirements
- **NEVER** bypass quality gates by using `--skip` or `--no-verify`

### Regression Coverage Discipline
- **NEVER** add regression tests that only prove the specific bug-fix value, example scenario, or changed text does not change back; coverage must protect the broader behavioural rule that was missing.
- **ALWAYS** first check whether existing behavioural rules and scenario coverage already cover the issue in question.
- **ALWAYS** expand the relevant existing behavioural scenario when coverage is missing, so the test protects the intended outcome rather than the incidental failing value.
- **NEVER** introduce duplicate or narrowly tailored regression tests that only encode the discovered example and serve no broader behavioural purpose.

### Verification
- **ALWAYS** for any implementation task verify it in proportion to risk, and let test coverage scale with risk and blast radius:
  - narrow changes receive focused coverage
  - shared behaviour, cross-module contracts, and user-facing workflows receive broader coverage

### Shaping Completion Response Messages
- `excluded_content` is considered:
  - git-only activity
  - tasks that are part of these skill workflows: `analyze-code-quality`, `analyze-architecture`, `analyse-security`, `analyze-codebase-integrity`, `analyze-agentic-readiness`, `deep-interview`, `experiment-observations`, `fault-catalog`, `intent-plan`
  - clarification questions, tool narration, blocked responses, partial responses, or other interim responses
- `already_surfaced` is when content (including by description or intent) has already appeared in:
  - reasoning
  - progress updates
  - earlier response content
  - prior completion responses
- Include the following when not `excluded_content`:
  - Start each completion response with a concise bulleted section titled `Activity Summary` that covers the work carried out.
  - Identify and surface only the key points needed to answer, decide, or act. Label them `P1`, `P2`, and so on; use the natural number of qualifying points rather than filling a quota.
    - Put the answer, outcome, or decision in `P1`; include essential verification in the relevant primary point rather than adding a repeated summary.
    - Include `R#` only for a new objective-adjacent topic that materially affects the user's decision or next action but falls outside the discussed scope.
  - Include a bulleted section titled `Gaps & Contradictions` only when isnt `already_surfaced` for new objective-relevant gaps, contradictions, inconsistencies, or knock-on effects. Use bullet IDs `C1`, `C2`, etc. Omit this whole section if none qualify. Never add filler or restatements.
  - Include a bulleted section titled `Beneficial Suggestions` only when isnt `already_surfaced` for new objective-linked suggestions that would materially improve the outcome. Use bullet IDs `S1`, `S2`, etc. Omit this whole section if none qualify. Never add filler or restatements.
- When a substantial task, todo list or multi phase plan has completed add to your response:
  - Include a bulleted section titled `Architectural Decision Changes` only when not `excluded_content` and isnt `already_surfaced` for new runtime design decisions that materially differ from the user’s original requirements. Use bullet IDs `A1`, `A2`, etc. Omit this whole section if none qualify. Never include it for routine tasks.
  - Include a bulleted section titled `Focused Review` only when isnt `already_surfaced` for specific changed files from the completed todo list or multi phase plan that had repeated tool or verification failures before success, high complexity in the starting or finished code, or high blast radius across code or the user journey. Use bullet IDs `F1`, `F2`, etc. Name the file, the qualifying characteristic, and the relevant line range. Omit this whole section if no changed file qualifies. Never include a generic changed-file list, replay individual failed attempts, add filler, or restate other sections.