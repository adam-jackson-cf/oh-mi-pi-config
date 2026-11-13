# Target and safety boundary

## Objective

Establish a safe, reproducible target, its record path and the setup mode before analysis.

## Required actions

1. Run `git rev-parse --show-toplevel` and `git status --porcelain=v1` in the target. Exit with a
   clear reason if it is not a Git repository.
2. Resolve the record path from `git remote get-url origin`: `https://github.com/<owner>/<name>(.git)`,
   `git@github.com:<owner>/<name>(.git)` or `ssh://git@github.com/<owner>/<name>(.git)` gives
   `repos/<owner>/<name>/` (lowercase) under the oh-mi-pi-config root. A worktree resolves to its
   main checkout's record. No GitHub `origin` remote means no record; report it and stop.
   Record whether that record already exists.
3. Record the base ref, tracked and untracked work, protected paths, chosen language/runtime stack,
   operating system, and CI provider. Verify user-supplied stack choices against source and lockfile
   evidence.
4. The harness checks cover the whole repository. There are no baselines and no new/changed-code
   scope. Record candidate scope exclusions only for generated, vendored and fixture paths, with
   evidence; they become record config after approval.
5. Identify agent instructions and access restrictions before reading files. Never inspect secret
   files or protected paths without their required authorization; verify environment variables by
   presence only.
6. Select a temporary candidate root outside the target checkout and outside the records directory.
   No target writes, record writes, package installation, formatter fixes, Git staging, or CI
   changes occur in Steps 0–5.
7. Record whether the target has an existing hook, gate runner, test suite, type checker, linter, or
   documentation ownership rule. Existing files remain owned by the target until the user chooses
   keep, merge, or replace.
8. Record the setup mode: `greenfield` when no record exists and the target has no
   guardrail checks, `adopt` when hand-written checkers in the repo already enforce harness
   rule families and the harness uses them instead of its own steps, and `extend` when a
   record exists and only a missing part is added, such as a verification surface.

## Done when

- Git root, record path, stack evidence, CI provider, base ref or its absence, scope exclusion
  candidates, protected paths, and unrelated work are recorded.
- Candidate work has a separate temporary root, and the target checkout and records are unchanged.
