---
name: "git-tool"
description: "Use the repository's Git workflow safely and consistently. USE WHEN the task requires any Git action"
---

# Git Tool Skill

- Use Conventional Commits: `feat|fix|refactor|build|ci|chore|docs|style|perf|test`.
- Use atomic commits at logical task boundaries.
- Start with `git status`, `git diff`, and `git log` before edits, staging, commits, or pushes.
- Create branches with `git switch -c <branch-name>` when the user asks for a new branch.
- Never run `git reset --hard`, `git clean`, `git restore`, or `rm`.
- Never run repo-wide search/replace scripts such as `sed -i`, `perl -pi -e`, or `python -c`.
- Use the repo's package manager and runtime.

## Commit And Push Workflow

When the user asks to commit and push current repository changes:

1. Run `git status` and inspect the changed file list for unrelated work or secret-bearing files.
2. Run `git diff` and, when relevant, `git diff --cached` before committing.
3. Add all intended changes with `git add -A` only after the changed file list is understood.
4. Write a concise Conventional Commit message that accurately summarizes the staged changes.
5. Commit the staged changes.
6. Push to the current branch's remote.
   - If the current branch has no upstream remote branch, push with upstream tracking.
   - If the repository has no configured remotes, do not push.
7. After pushing, report the pushed remote target.
   - If the current branch is `main`, report the normal remote repository URL.
   - If the current branch is not `main`, report a pull request creation URL into `main`.
   - Convert SSH GitHub remotes such as `git@github.com:owner/repo.git` to HTTPS URLs when reporting.
