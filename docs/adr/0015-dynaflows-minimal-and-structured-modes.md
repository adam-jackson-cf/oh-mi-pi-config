# 0015: Dynaflows runs in minimal or structured mode

Status: Superseded in part by [ADR 0016](0016-skill-moves-and-legacy-removal.md) (readers no longer
name the removed `enabled` key). Accepted (owner-approved, 2026-11-20). Supersedes the on/off
switch of
[ADR 0004](0004-dynaflows-flag-gates-the-harness-process.md) and the separate `agent-rails.ts` extension.

## Context

Dynaflows was an extension switched on or off per repository by `dynaflows.enabled` in the
record's `gate.json`. A second extension, `agent-rails.ts`, held the Git guards that applied
whatever the switch said. The rails runtime, `agent/rails.yml` and the record schema and loaders
sat in `agent/lib/` and `agent/`, outside the extension that owns them. Merging and pushing to the
default branch with dynaflows off rested on instructions in `agent/RULES.md` and `agent/AGENTS.md`,
not on a check.

## Decision

The dynaflows extension is always loaded and runs every repository in one of two modes:

- **Minimal**: fixed, deterministic guards only. Rails refuse every Git hook bypass, refuse
  `gh pr merge`, refuse `git push` to the remote's default branch, keep worktree placement and
  subagent path checks, keep the record's `agentMerge` policy and require reading
  `skill://git-tool` before Git integration commands. Pushes to other branches and `gh pr create`
  pass.
- **Structured**: the full dynaflows workflow. It adds the merge checks (`prove()`,
  `structure_review()`, exact-head CI), reference reads on edits, path checks, `checks.commit` and
  the harness commit checks, the judged `dynaflows-*` rules, the workflow pointer, and the
  `skill://git-tool/references/merge-and-recover.md` read before Git integration commands. The
  default-branch push guard stays.

Configuration:

- The record's `gate.json` holds `"dynaflows": {"mode": "minimal"|"structured"}`. The TypeScript and
  Python readers and `guardrails init` reject the old `enabled` key by name.
- The machine file `~/.omp/dynaflows.json` is `{"schemaVersion": 2, "mode": "minimal"|"structured"}`.
  `minimal` forces minimal mode for every repository; `structured` or no file defers to the record.
  An invalid file is reported, the session runs in minimal mode, and commits are refused until the
  file is fixed.
- No origin remote, a non-GitHub origin or no record means minimal mode.
- The `$dynaflows` directives and the `dynaflows` tool switch the record between the modes.

`agent/extensions/agent-rails.ts` is deleted; its checks run in the dynaflows extension
(`lib/guards.ts`). The runtime, mapping and record code moved into the extension:
`agent/extensions/dynaflows/lib/` (`rails-runtime.ts`, `repo-record.ts`, `repo_record.py`,
`guards.ts`), `agent/extensions/dynaflows/rails.yml` and `agent/extensions/dynaflows/record/`.

## Consequences

Every live record and machine file must move from `enabled` to `mode` before rails accept it.
Merging a PR in a minimal-mode repository is the owner's step or needs structured mode. The
`RULES.md` and `AGENTS.md` merge instructions describe structured mode only, because rails now
enforce the minimal-mode limits.
