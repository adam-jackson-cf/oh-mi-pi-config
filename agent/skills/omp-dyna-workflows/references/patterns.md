# Pattern contracts

Each entry: inputs; stop rule; verification gate; output.

- **`adversarial_verify`**: objective, repo, check_cmd, max_rounds; stops at a reviewer `pass` or
  max_rounds; `check_cmd` must pass before any review; returns status, rounds, history.
- **`blind_label`**: frozen batch files, rubric, labels; stops when all batches return; two
  distinct agents in separate sessions, blind to each other and to any model's answer; returns
  agreed, disputed.

## Failure handling

- Agents that fail or return off-schema output come back as an exception in their result slot.
  They are never counted as a pass or a vote.
- `adversarial_verify` returns `exhausted` rather than guessing; `blind_label` returns `disputed`.
  The orchestrator decides, and a human confirms labels.
- `WorkflowError` means a contract was broken: over a cap, or the same agent as both labellers.
  Fix the call; do not catch and continue.

## Adding a template

Only add one when a native keyword cannot express it (usually a dependency-coupled loop or an
owner-specific rule). Keep it in `workflows.py`, reuse `run_check`, give it a cap and a stop rule,
and document it in SKILL.md and here.
