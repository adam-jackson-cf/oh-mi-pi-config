# Map the repository's testing homes

## Objective

Establish the Git target and record where the repository already keeps its testing rules, commands, helpers and agent procedures.

## Required actions

1. Run `git rev-parse --show-toplevel` and `git status --porcelain=v1` in the target.
   Stop with the reason when it is not a Git repository.
2. Create `<notes>`, a new temporary directory outside the target, and write `<notes>/inventory.md`. Scratch files of
   this run go only there. Change no repository content (tracked files, instruction files) before Step 5.
3. Read every agent instruction file: `AGENTS.md` and `CLAUDE.md` at the root and in subdirectories. Record each with its
   level, how `CLAUDE.md` pairs with `AGENTS.md` (symlink, `@AGENTS.md` import, or standalone), its sections whose headings
   name tests or testing, and whether `.claude/` exists. Files inside a Git submodule or vendored source are read for
   context only: they are never a home, a pointer target or a migration candidate. Follow their access restrictions;
   never open secret files, and check environment variables by presence only.
4. Record the remote URL, the branch, the unrelated uncommitted work and any protected paths the instruction files name.
   Nothing in this run stages or reverts them.
5. Record the stack from manifests and lock files: languages, package manager, test runner, and the documented or
   lockfile dependency install command.
6. Record under `## Homes`, for each object kind in [placement](placement.md), its named home with the instruction-file
   `path:line`, else its observed home with example paths, else `none` with the searches run. Include every other file
   that states testing rules (a testing doc, a contributor guide section, a repository skill), with its headings,
   and the existing agent skills folder.
7. Record an earlier install, with every file and pointer it owns, and plan its replacement for Step 4:
   - an index whose `Repository decisions` names installed objects is refreshed through that list;
   - a legacy install is a testing skill with `## Tiers and assertion owners` and `## Before handing over` headings; guides
     with runbook names in a docs folder and the instruction lines or scoped files pointing to those guides belong to it,
     while a line pointing to the index's path stays. A legacy file at a path this run installs becomes an `edit` row,
     every other legacy file and pointer a `delete` row. Its `## Repository notes` lines and every concrete value in its
     files (a limit, a command) that the new index would not hold become `Repository decisions` bullets.
   Any other testing skill or file is repository material, never an earlier install.

## Done when

- `<notes>/inventory.md` holds `## Target` (Git root, branch, unrelated work, protected paths, instruction files, stack,
  install command, earlier install) and `## Homes`.
- `git status --porcelain=v1` in the target is unchanged.
