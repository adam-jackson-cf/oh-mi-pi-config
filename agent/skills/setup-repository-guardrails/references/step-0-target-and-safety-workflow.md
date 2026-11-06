# Target and safety boundary

## Objective

Establish a safe, reproducible target and adoption scope before analysis.

## Required actions

1. Run `git rev-parse --show-toplevel` and `git status --porcelain=v1` in the target. Exit with a
   clear reason if it is not a Git repository.
2. Record the base ref, tracked and untracked work, protected paths, chosen language/runtime stack,
   operating system, and CI provider. Verify user-supplied stack choices against source and lockfile
   evidence.
3. Ask for a base ref if new/changed-code scope cannot be resolved. Offer whole-repo,
   new/changed-code, and selected-path scopes; do not infer approval from a default.
4. Identify agent instructions and access restrictions before reading files. Never inspect secret
   files or protected paths without their required authorization; verify environment variables by
   presence only.
5. Select a temporary candidate root outside the target checkout. No target writes, package
   installation, formatter fixes, Git staging, or CI changes occur in Steps 0–5.
6. Record whether the target has an existing hook, gate runner, test suite, type checker, linter, or
   documentation ownership rule. Existing files remain owned by the target until the user chooses
   keep, merge, or replace.

## Done when

- Git root, stack evidence, CI provider, base ref or its absence, adoption scope options, protected
  paths, and unrelated work are recorded.
- Candidate work has a separate temporary root and the target checkout is unchanged.
