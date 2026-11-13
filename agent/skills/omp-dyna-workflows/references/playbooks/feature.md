# Feature playbook

Deliver the requested feature with frozen acceptance evidence.

Follow [the six-stage contract](../../SKILL.md#six-stage-contract) in order;
only the deltas below belong here. Ordinary implementation cannot skip its
owner-approved project-setup prerequisite. Helper mechanics live in
[patterns](../patterns.md); Git mechanics live in `skill://git-tool`.

## 1. Frame

Define the new user-visible outcome and affected owners/callers.

## 2. Spike

Resolve only remaining questions on the real feature surface.

## 3. Plan

Each proof encodes requested behaviour: it fails at base, where the feature is absent,
and passes at head. Reject doctor/environment failures as red.
Each proof is an outcome test or journey linked from the feature's map entry; the proof task
writes or updates that entry with the proof, and `freeze()` refuses an unlinked proof file.
Proof tasks freeze runner-invoked journey files
and required lever changes together. Implementation changes product code and map docs only.

## 4. Implement

Implement the feature within the framed scope.

**Discoveries.** User-facing behaviour the plan did not name (a new error, option, output or
state the caller can see) is not left to review. The implementation task adds or updates its
feature-map entry and writes its outcome test, leaves both uncommitted with no other pending
edits, and stops. Main re-freezes through the existing path (`previous=(path, digest)`, with
`revise` only for changed existing proofs). The new test is red at base because the behaviour did
not exist there, so TDD holds. Implementation then makes it pass. No "added after freeze" exception.

**Map sweep.** Before the PR opens, a sweep task lists each user-facing behaviour the branch diff
adds or changes, with a concrete source path; items without one do not count. Any item no changed
map doc covers gets a map entry and an outcome test, then re-freeze as for a discovery. The sweep
writes entries and tests, never review findings.

## 5. Verify

Frozen proofs pass at head, every changed feature-map doc links a frozen proof, and CI is green
on the exact head.

## 6. Integrate

No feature-specific integration delta.
