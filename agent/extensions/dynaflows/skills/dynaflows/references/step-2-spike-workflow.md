# Spike

## Objective

Answer the remaining plan-changing questions with bounded scratch experiments, then discard all spike code.

## Required actions

1. Spike every remaining question. Bug reproduction and project-setup inventory are mandatory spike activities.
2. Read `skill://git-tool`, resolve the primary remote and default branch, and fetch it.
3. Create the scratch tree with `git worktree add --detach ~/worktrees/<repo>/spike-<slug> <fetched-ref>`,
   using the configured `worktree.base` root, the lowercase `origin` repo name and a `spike-` slug prefix.
4. Give each question a pass/fail predicate, a timebox and declared side effects.
   Writes to shared services require owner approval.
5. Delegate to a non-isolated task with absolute scratch paths and the smallest bounded experiment on the real surface.
   The task returns findings, commands, actual outputs and failures.
6. Race prototypes only when built results decide. Use innovation-council only when no prototype can settle the question.
7. Save `local://spike-<slug>.md`, then remove the tree with `git worktree remove --force ~/worktrees/<repo>/spike-<slug>`.
   Reuse no spike code.
8. Investigation ends here with its report; every other playbook continues to Plan.

## Done when

- Every question has a saved answer or recorded failure, the scratch tree is removed and no spike code remains.
