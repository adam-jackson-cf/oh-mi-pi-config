# Class prevention

## Objective

Turn a recurring owner correction into the strongest enforceable repository constraint, with historical proof.

## Guidance

1. Treat the owner's correction as ground truth; do not rerun a reported failure to dispute it.
   Inspect relevant recent commits, reverts, and PR review comments for the same failure mode.
   Group by cause, not by file or wording. Two occurrences establish a mistake class; one qualifies
   when its impact is high: unverified work merged, data/evidence loss, security exposure, or owner
   time blocked for hours. State the impact and observed count; never invent recurrence.
2. For each class, identify the intended invariant and concrete past examples with commit SHAs,
   paths, revert/review links or timestamped session-log references. Read existing enforcement and the rule registry.
3. Choose the highest level that prevents the class, in this strict order:
   **architecture > types > lint/CI check > behaviour test > agent rule/docs**.
   Try removing the invalid state or dependency first, then make it unrepresentable in types.
   State a concrete reason each higher level cannot enforce the invariant before moving down.
   Difficulty or convenience alone is not a reason to choose prose.
4. A single bespoke lint/CI check for one class may go directly into the existing authoritative
   repository gate and CI, with its regression proof; no full guardrail approval inventory is needed.
   Route new rule families through `skill://setup-repo-proofs`.
   Require a deterministic error naming the offending path and specific fix. Prefer existing checks;
   remove superseded weaker mechanisms rather than adding an optional parallel command.
5. Separate prevention level from regression proof: proof is always required, normally a test,
   even when architecture, types or lint prevents the mistake. Choose a behaviour test as the
   prevention level only when higher levels cannot encode the invariant.
   Cover the class and meaningful boundaries, not just one input. Read `skill://test-guidance`
   and the repository's testing guidance before adding tests.
6. Use an agent rule or documentation only for irreducible judgment.
   A repeated unenforced rule must move to a higher level, not be reworded or duplicated.
   If no higher level can enforce it, report the blocker and seek an owner decision; never
   claim another prose reminder prevents recurrence.
7. Prove the chosen prevention rejects a real past mistake: use its historical bad revision or
   replay its actual offending change in an isolated worktree against the real enforcer boundary.
   For session-log-only failures with no bad git revision, replay the logged inputs against that
   real boundary in an isolated worktree; cite the log/timestamp, inputs, command and diagnostic.
   State which boundary was not exercised (for example, direct hook replay does not exercise
   session event delivery). A synthetic substitute or a mock of the enforcer is not historical proof.
   Record the rejection and corrected pass. For architecture/types, show the old invalid construction
   is impossible or rejected.
   Report inaccessible evidence or an unexercised boundary as a gap, never as proven coverage.
8. Allow exceptions only on the offending line, with a reason, expiry date, and explicit owner
   approval reference. Use the language's inline directive/comment form; no blanket exclusions.
   An exception is not enforcement evidence, and expired exceptions must fail the gate.
9. Update the repository rule table with the enforcer, chosen level, historical proof, and date.
   Replace obsolete entries when enforcement moves up; preserve the evidence and migration trail.

## Report the result

Reply once per class using these fields:

- **Class**: Shared failure mode and invariant; observed count and high-impact basis if only one.
- **Evidence**: Historical revision or timestamped log; actual rejection, corrected pass and boundaries not exercised.
- **Level chosen**: Prevention level (architecture, types, lint/CI check, behaviour test, or judgment-only rule), separately from regression proof.
- **Why higher failed**: A concrete reason for every higher level not chosen; `none` at architecture.

Name the updated registry and any unproven check or blocked escalation. Never claim an unenforced
reminder, an approved exception, or an unexercised check makes recurrence impossible.
