# Rule verification

## Objective

Demonstrate that no applicable rule is merely named, skipped or disconnected from execution.

## Required actions

1. Run the enabled controls over the whole repository with approved exclusions. Expect zero
   findings and no unexpected target output. Run each project's checker tests through its public
   entry point; violations fail with the affected file/rule and concrete correction, valid inputs pass.
2. Exercise actual runner, hook and CI wiring in an isolated checkout: a passing commit gate permits
   a commit and a failing one blocks it. Never test by pushing to a protected branch. Parallel CI's
   aggregate must fail for failed, cancelled and skipped tiers, not just cover all tier selectors.
3. Verify tool selectors, limits, exceptions, pinned versions, evidence freshness and approved timing
   behavior. Run commit/CI commands or their tier jobs and local/CI parity tests. Unavailable,
   skipped and timed-out checks are not passes. Observe actual CI on the final approved commit.
4. Confirm no proposal, report or temporary output leaked into the target. Compare final paths with
   the approved matrix; rerun affected controls after repairs. Confirm superseded contracts were
   removed or explain why none existed.
5. Return the workflow's terminal result with rule IDs, commands/results, counts, documentation
   changes and blockers. Do not save a retained summary artifact.

## Done when

Every applicable rule is implemented, invoked and demonstrated by outcome tests. Whole-repository
checks, local/CI parity and approved scope pass or have concrete blockers; none is called green.
