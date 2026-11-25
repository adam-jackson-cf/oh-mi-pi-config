# Baseline in place

## Objective

Run, in the target, exactly the commands the plan needs: tier selection always, and the full suite only when the plan can
change code.

## Required actions

1. Record `git status --porcelain=v1` in the target, copy every already-modified tracked file and every untracked,
   non-ignored file into `<notes>/baseline-snapshot/` at its repository path, and list every tracked path already deleted
   or renamed away.
   Install dependencies with the install command from `inventory.md`;
   never update a lock file. When an instruction file forbids installing, or the install cannot run, record that and the
   command, run with what is installed, and continue with static evidence for what cannot run.
2. List the collected tests of each tier with the runner's collect-only option, using the tier's complete command or,
   without one, the runner's own options (a folder, marker, name pattern, exclusion, package list or `-run` pattern).
   When a file has no tier mechanism, give it the tier and tag its design calls for, using the tier definitions in
   `assets/templates/installed/references/writing-tests.md` (a mocked module does not run); an untagged file is T1.
   Record under `## Tier facts` the exact invocation and collected count per tier, and every test file no tier or two
   tiers select. A count is the tests the collect-only listing shows (Go: top-level `Test` functions from `-list`);
   subtests or cases generated at run time are noted beside it, never added. Replace the Step 1 test counts of migration
   candidates with collected tests.
3. When `## Decisions` holds `merge`, `full` or breadth `exemplars`, run T1 and T2 once with those invocations,
   disabling result caching (for example `go test -count=1`), and the static checks from `Commands and checks`.
   Record pass and fail counts and wall time under `## Baseline`, and a check that cannot run as `not run: <error>`.
   Otherwise run no tier: Step 5 runs each command the installed text states.
4. Never run T3.
5. Compare each path from action 1 with its state then (its snapshot copy when modified or untracked, absent when listed
   as deleted, else `HEAD`) and each other tracked path with `HEAD`. Restore every path a run changed to that state,
   removing a listed path a run recreated, and remove every non-ignored file a run created; ignored files a run creates
   (dependency caches) stay. Report each restored or removed path and the command that changed it.

## Done when

- `## Tier facts` holds each tier's invocation and collected count, and `## Baseline` holds the full run results or
  `not needed: <option and breadth>`.
- Every tracked file holds its content from before this step.
