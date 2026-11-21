# 0018: Rails trust only what they saw

Status: accepted, 2026-11-21. Supersedes in part [ADR 0001](0001-agents-write-records.md).
Written against record-held modes; [ADR 0017](0017-dynaflows-runtime-rails.md) moved the mode to
the tracked mode file, which this ADR protects in place of the removed machine file.

## Context

A bypass hunt found routes that landed commits, pushes, merges or proof without the rails:

- omp's eval tool (`sh()`, subprocesses, the `commit` prelude with `no_verify`), the github tool's
  `pr_push` and branch-merged isolated tasks never reach the bash rail.
- The shell scan ignored launchers, interpreters, piped shells, dynamic command words, pipeline
  `cd`, `--git-dir`, config includes, gh aliases and most `gh api` writes.
- Proof results, records, the mode file, the restart-review state and Git config were plain
  agent-writable files.
- Commit checks ran inside omp's 30 s `tool_call` budget, so slow tiers could never pass.

## Decision

- Session start pins `commit.enabled` and `github.enabled` off, `task.isolation.merge` to
  `patch`, and `extensionHandlers.toolCallTimeoutMs` above the 600 s commit-check budget.
  `agent/config.yml` also sets `commit.enabled: false`.
- Eval cells that launch git, gh, hub or omp, use the commit prelude, write rail state or
  redefine the proof workflow are refused.
- The shell scan fails closed: a git/gh/omp word it cannot resolve to a checked action refuses the
  command. Retargeting options and variables, unlisted `-c` and `git config` keys, remote and ref
  writes, plumbing commit routes, gh aliases and extensions, and `gh api` writes outside PR
  conversation are refused.
- Agent tools never write Git internals, records, the tracked mode file
  (`agent/extensions/dynaflows/modes.json`), the restart-review state
  (`~/.omp/agent/dynaflows-reviews.json`), the live extension, the harness config, or Git and gh
  config. Only the `dynaflows` tool and the `$dynaflows` directive change the mode file. Records
  open to agents only in owner-asked setup or while the owner's latest prompt names the record, an
  approval or an exception.
- Proof evidence counts only when this omp process watched the `prove()` or
  `structure_review()` cell write it. Incomplete reviews, any failing required context and an
  empty check rollup block merges.
- The mode file, the session repo's origin and its record are snapshotted at session start; a
  change outside the rails' own writes refuses git and gh actions until omp restarts.
- A structured commit is its bash call's only change, over a staged index with no unstaged edits
  ([ADR 0017](0017-dynaflows-runtime-rails.md) refuses `-a`, `-i`, `-o`, `-p` and paths).

## Consequences

Agents run git and gh only through the bash tool and merge from the process that proved the head.
Text scans cannot see obfuscated eval code, so the owner keeps server-side rules: the default
branch ruleset and a required status only the gate identity can post.
