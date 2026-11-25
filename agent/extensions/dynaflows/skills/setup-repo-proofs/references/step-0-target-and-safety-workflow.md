# Target and safety boundary

## Objective

Establish a safe, reproducible target, its rollout scope and the setup modes before analysis.

## Required actions

1. Run `git rev-parse --show-toplevel` and `git status --porcelain=v1` in the target.
   Stop with a clear reason if it is not a Git repository.
2. Record the base ref or its absence, tracked and untracked work, protected paths, chosen language/runtime stack,
   operating system and CI provider. Verify user-supplied stack choices against source and lock-file evidence.
3. Offer three rollout scopes and record the owner's choice; never infer approval from a default:
   - `whole`: every Git-owned file (the default recommendation);
   - `changed`: files added or changed since a base ref; ask for the base ref when it cannot be resolved;
   - `paths`: owner-selected repo-relative paths.
   A scope changes which files a rule gates, never which applicable rules must exist.
   It is not a baseline: whole-repository findings stay reported (Step 4) and nothing waives them.
4. Record candidate scope exclusions only for generated, vendored and fixture paths, and for frozen, digest-pinned study
   or evaluation records principle 10 protects, each with origin evidence.
   Third-party code is outside the owned-code boundary ([design principles](design-principles.md), principle 7);
   a vendored copy the repo edits is owned code.
5. Identify agent instructions and access restrictions before reading files.
   Never inspect secret files or protected paths without their required authorization; verify environment variables by presence only.
6. Create the run state per [run state](run-state.md): `.proofs/` with its guard and `state.json`, excluded through
   `.git/info/exclude`. Resume from an existing `state.json` only as prior decisions to re-confirm.
   Select a temporary candidate root outside the target checkout for clones, candidate code, fixtures and command output.
   Steps 0–5 make no other target writes, package installations, formatter fixes, Git staging or CI changes; tools that Steps 3–4
   need are installed only into an environment under the candidate root, and formatter fixes apply only to candidate copies.
7. Record whether the target has an existing hook, gate runner, test suite, type checker, linter or documentation ownership rule.
   Existing files remain owned by the target until the owner chooses keep, merge or replace.
8. Record every setup mode that applies; modes can overlap:
   - `greenfield`: the target has no verification checks yet;
   - `adopt`: existing checkers or configurable tools enforce, or could hold, catalogue rule families;
   - `extend`: established controls exist and only a missing part is added, such as a verification surface.
9. Never build a second verification system beside the repo's own, and never reshape repo code to satisfy a duplicate copy of
   a rule an existing checker carries.

## Done when

- Git root, stack evidence, CI provider, base ref or its absence, scope choice and options, exclusion candidates,
  protected paths, modes and unrelated work are recorded in `state.json`.
- Candidate work has a separate temporary root, and the only target changes are the `.proofs/` folder and its
  `.git/info/exclude` entry.
