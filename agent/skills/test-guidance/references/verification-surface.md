# Verification surface

## Objective

Drive usable product outcomes through an isolated, deterministic lever and maintained feature map.

## Guidance

- Read Verification surface in the repo test profile. With a valid no-runnable-app exemption only,
  use outcome tests instead of journeys; do not create an artificial app or divert to setup.
  Use T1 when in-process, T2 seams when the actual host process is needed; never invent a lever for an exempt repo.
- The lever is one command performing launch → doctor → drive → evidence → cleanup.
  Doctor proves ready means usable, not merely that a process or port exists.
- Document the doctor-failure marker in Command notes. Enforced by SURFACE.lever-isolation (omp-guardrails lever-selftest).
  Classify that marker as blocked infrastructure, never as a defect proof's expectRed,
  even when the command exits nonzero. Stop that run, retain marker/probe evidence, clean up and report blocked
  to the calling workflow. Repair only within authorized scope; otherwise ask the owner once:
  "Repair the environment so doctor succeeds, or leave this proof blocked?"
  Resume only after the evidenced cause is repaired and doctor succeeds; rejection leaves the proof blocked.
  Never retry merely to get green.
- Accept Data directory argument and Evidence directory argument from the profile.
  Use isolated gitignored or temporary directories. Never drive a shared user instance.
- Clean up after every failed iteration. Verify what dry runs actually skip;
  list stand-in-only setup steps as unverified, never as exercised production setup.

## Feature map and journeys

- Enforced by SURFACE.feature-map (omp-guardrails surface-lint).
  Put the observable end state in the Sub-features body.
  Retain existing file organization when it supports these sections. If the existing map cannot carry them,
  report the layout gap to the calling workflow before writing; do not silently relax headings or create parallel maps.
- Script deterministic T2 journeys. Enforced by SURFACE.journeys (omp-guardrails surface-lint).
  A library's map points to T1 outcome tests when no process, build or UI boundary is crossed.
  Agent exploration belongs in T3, not deterministic journey proofs.
- Under DynaFlows, a defect proof must fail with its asserted expectRed on the broken baseline;
  a preserve proof must pass before and after. New-surface tolerates an absent baseline API.
  Lever journeys use defect with an asserted unmet outcome (including a newly requested outcome),
  or preserve for existing outcomes, never new-surface. Freeze means pin whole proof-file bytes before implementation.
  Under DynaFlows, `prove()` checks journey stability: a `journey: true` proof must pass every clean run on the
  head being merged, so do not rerun journeys by hand before freeze.
  Outside DynaFlows, retain these test/proof principles without imposing task actors, and run each journey proof
  at least three times; count only clean runs, rejecting doctor failures, hangs and contaminated state.
- Proof commands run journey files through the test runner. The proof task writes any lever changes the proof needs
  and freezes them with the journey files. For the journey's greening change, implementation edits product code
  and feature-map docs only, never frozen lever/journey bytes.
  During approved Project setup, implementation writes the feature map in the repo; profile and guardrail settings
  go in the repo test profile (under DynaFlows, the record's `profile.md`, never a repo file).
  Hand profile mapping gaps to that workflow's approved setup extension; separately approved budget-profile
  edits follow [suite-budget.md](suite-budget.md) and belong to implementation, never the frozen proof task.
- A PR changing or adding a mapped feature writes its map entry in the implementation task and its journey in the proof task.
  Plan a new feature's intended map entry from its requirement before writing its journey.
  Run nightly drift only when a feature map and Run events scheduler exist;
  otherwise report the valid surface/scheduler exemption in the PR, or a profile gap if neither is recorded.
