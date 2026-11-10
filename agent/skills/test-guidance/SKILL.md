---
name: "test-guidance"
description: >-
  Use when creating, moving, changing or deleting a test, chasing a flaky test or intermittent CI
  failure, controlling test-suite size or run time, or drawing a claim from a test run or seed sweep.
hide: true
disable-model-invocation: true
---

# Task

## Start here: pick the tier

- **Purpose**: Place each assertion at the tier that drives its behaviour, without duplicate owners.
- **When**: Before creating, moving, changing or deleting a test.
- A project's own test skill or AGENTS.md test rules take precedence; this skill fills gaps.
- Where this skill is silent, use judgement: keep existing content, make no irreversible write,
  ask the user once when the choice changes the outcome, and state the choice in the result.
- Runbook: [Test tiers and layout](references/tiers.md)

## Write a T1 unit test

- **Purpose**: Test decisions in memory with injected dependencies and behavioural coverage.
- **When**: Creating or changing a unit test, its fixtures or its dependency seams.
- Runbook: [T1 unit-test patterns](references/unit-tests.md)

## Test determinism

- **Purpose**: Prove seeded execution, replay, arithmetic and order-insensitivity.
- **When**: Testing deterministic computation, codecs, replay, host parity or goldens.
- Runbook: [Determinism tests](references/determinism.md)

## Test behaviour and statistics

- **Purpose**: Assess behaviour using invariants, audits and adequately supported measured claims.
- **When**: A test, sweep or report claims how a system behaves or that its behaviour changed.
- Runbook: [Behaviour and statistics](references/behaviour-and-statistics.md)

## Chase a flaky test

- **Purpose**: Reproduce and remove an intermittent failure's cause rather than hide it.
- **When**: A test passes and fails on the same tree locally or in CI.
- Runbook: [Flaky tests](references/flakes.md)

## Before committing

- **Purpose**: Complete the applicable gates and report only checks that actually passed.
- **When**: Before committing, pushing or handing over a change that affects tests.
- Runbook: [Before committing](references/before-commit.md)

## Control suite growth and time

- **Purpose**: Keep each test justified, singly owned and within its tier's time budget.
- **When**: Adding, moving or deleting tests, or changing suite count, placement or run time.
- Runbook: [Suite budget](references/suite-budget.md)
