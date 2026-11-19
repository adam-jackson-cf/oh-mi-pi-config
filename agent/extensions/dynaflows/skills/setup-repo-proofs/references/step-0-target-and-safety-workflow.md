# Target and safety boundary

## Objective

Establish a safe, reproducible target and the setup modes before analysis.

## Required actions

1. Run `git rev-parse --show-toplevel` and `git status --porcelain=v1` in the target. Stop with a
   clear reason if it is not a Git repository.
2. Capture the base ref or its absence, tracked and untracked work, protected paths, language/runtime,
   operating system and CI provider. Verify supplied stack choices against source and lock files.
3. Checks cover the whole repository, without waived-finding baselines or new/changed-code scope.
   List proposed generated, vendored and fixture exclusions with origin evidence. Third-party code
   is outside the owned-code boundary; an edited vendored copy is owned code.
4. Identify instructions and access restrictions before reading files. Never inspect secrets or
   protected paths without authorization. Check environment variables by presence only.
5. Select a temporary candidate root outside the target. Steps 0–5 make no target changes,
   package installations, formatter fixes, staging changes or CI changes.
6. Identify existing hooks, gate runners, tests, type checkers, linters and documentation ownership.
   Existing files retain their owner until an explicit keep, merge or replace decision.
7. Select every applicable mode: `greenfield` for a project without guardrail checks; `adopt` when
   existing checkers or configurable tools can carry the selected rules; `extend` when adding only
   a missing part to established controls. Modes can overlap.
8. Reuse existing tools, commands and ownership policies, and keep local checks and CI runnable by any contributor.
   Every applicability or coverage claim needs source evidence for the individual rule.

## Done when

The Git root, stack evidence, CI provider, base ref, exclusions, protected paths and unrelated work
are known. Candidate work has a separate temporary root; the target is unchanged.
