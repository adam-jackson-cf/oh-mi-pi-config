# Project setup playbook

Establish owner-approved controls as a prerequisite PR before ordinary implementation resumes.

Follow [the six-stage contract](../../SKILL.md#six-stage-contract) in order;
only the deltas below belong here. Ordinary implementation cannot skip its
owner-approved project-setup prerequisite. Helper mechanics live in
[patterns](../patterns.md); Git mechanics live in `skill://git-tool`.

## 1. Frame

Identify stack, protected work, missing controls and owners. Read `skill://setup-repository-guardrails`.

## 2. Spike

Inventory is mandatory: use setup Step 0 target/safety, Steps 1–2 inventory/rule
mapping, Step 3 candidates and Step 4 dry-run impact. Scratch work prepares the
Step 5 approval; do not apply controls during inventory.

## 3. Plan

Map canary criteria to new-surface proofs for new controls and preserve proofs
for existing controls, including intentional violations rejected and valid inputs
accepted. Complete explicit owner approval at setup Step 5 before application.

## 4. Implement

Invoke setup Step 6 approved application, then Step 7 rule coverage verification.
Install the approved hook before the first commit. Bootstrap is the exception to
needing completed controls before proposing/installing them, not an approval bypass.

## 5. Verify

Use canary/live evidence under the shared contract.

## 6. Integrate

Complete this prerequisite PR before returning to Frame of the original request.
