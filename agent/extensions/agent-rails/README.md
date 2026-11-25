# Agent rails

**An omp extension that gates what agents may edit, run, commit, push and merge in each
repository. Deterministic checks decide first, and calibrated judgments decide the rest.**

## Quick Install

Prerequisites:

- omp, loading this repository from `~/.omp` (`agent/config.yml` lists the extension directory)
- Bun and uv

From the oh-mi-pi-config checkout, install the locked dependencies and the commit hook:

```bash
bash scripts/install-quality-gates.sh
```

This also syncs the engine's environment (`agent/extensions/agent-rails/engine`). Restart omp
sessions after the extension changes: running sessions keep the old rails.

## Quick Start

In an omp session inside a repository with a GitHub origin, check the repository's state, then
onboard it:

```text
/agentrails status
/agentrails onboard
```

The first `onboard` has the agent run `skill://setup-repo-proofs` and `skill://test-guidance`
and record each setup section in a draft. The second `onboard` runs a fixed Q&A and writes
`.agentrails/config.json` in the main checkout, kept out of Git. Until then the rails refuse
ordinary work in that repository.

## What Agent Rails Does

The rails watch the agent's edits, shell commands and replies. They refuse actions the repository's
config does not allow, ask the owner where the config says `ask`, and prove a PR before merge.

- `index.ts` and `lib/`: the extension entry, guards, rails runtime, onboarding and config writer
- `rails.yml`: the edit tools, path fields and Git-write and ask-first command patterns
- `engine/`: the `guardrails` Python checkers and rule catalogue, run outside the repository
- `judgments/`: calibrated Jev judgments behind `decide()`
- `rules/`: judged reply rules that load in every session
- `references/`: the plain references refusal texts cite
- `skills/`: the bundled skills `git-tool`, `setup-repo-proofs`, `test-guidance` and
  `jev-actions`
- `inspector.env`: the opt-in Jev Inspector route for one session

## Core Concepts

- Rail: a check the extension runs on an agent action, which allows, refuses or asks.
- Repo config: `.agentrails/config.json`, one file per repository, never committed.
- Onboarding: `/agentrails onboard`, which builds the repo config from the setup skills and Q&A.
- Judgment: a calibrated model decision, enforced or in shadow mode, where shadow only logs.
- Autonomy: the config's `allowed` or `ask` setting for branch, PR and merge.
- Owner approval: a `$approve` or `$revoke` line the owner types in the main session.
- Proof: an outcome test red at the base and green at the head, checked by `prove()` at the PR.

## Go Deeper

- [docs/config.md](docs/config.md): the repo config's sections, writers, validation, export and
  import
- [docs/design-directive.md](docs/design-directive.md): the principles every change to the rails
  follows
- [docs/known-limits.md](docs/known-limits.md): what the rails do not catch or measure today
- [docs/external-dependencies.md](docs/external-dependencies.md): what the extension relies on
  outside its folder
- [references/judgments.md](references/judgments.md),
  [references/proofs-and-merge.md](references/proofs-and-merge.md) and
  [references/structure-and-naming.md](references/structure-and-naming.md): the rules refusals cite
