# Apply and verify

## Objective

Write the approved rows into the target and show that the suite, the stated commands and every route still work.

## Required actions

1. Write each approved row as its exact text, keeping uncommitted changes in edited files. When a planned file changed
   since Step 0, apply the row to its current text and list the file in the result. Leave unrelated work, protected paths
   and unapproved rows untouched.
2. Verify the rows:
   - when a helper, exemplar or design change row was applied: run each tier's
     collect-only listing, T1, T2 and the static checks with the installed commands, and compare with `## Baseline`;
   - otherwise: run each command the installed index states (tier commands and checks), once, with result caching disabled.
   Never run T3.
3. Fix a failure an applied row caused inside that row's files. When the fix needs a file or location outside the plan,
   show it and ask exactly "Apply these changes outside the plan: <files>?" **STOP** until the owner replies; on rejection
   remove the failing row and every row that depends on it, restoring their files, and report them.
   A failure also present in `## Baseline`, or without a baseline a failing test in a file no row changed, is reported, not fixed.
4. Check that every route and pointer resolves to an existing file and heading, the symlink resolves, no rendered
   guidance file (index, runbooks, pointer and rule text) contains `{{`, no adopted rule appears in two files this run
   wrote or edited, and no installed line contradicts another (for example a decision forbidding what a stated command
   does; fix the command). When the repository defines a lint or check for its agent skills, run it on the installed skill.
5. Never stage, commit or push.
6. Delete `<notes>` as the last action, unless the owner asked to keep it.

## Done when

- The result lists `git status --porcelain=v1 --untracked-files=all` and `git diff --stat` for the target, the
  verification results beside the baseline, and each route check.
- Under breadth `none`, the result lists the migration candidates as follow-up tasks for the installed skill.
- The result lists each mechanism gap from `## Tier facts` as a follow-up for the repository's owner.
