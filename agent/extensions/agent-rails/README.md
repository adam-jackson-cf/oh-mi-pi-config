# Agent rails

An omp extension that gates what agents may edit, run, commit, push and merge in each repository.

## Install

Work from the oh-mi-pi-config checkout omp loads at `~/.omp`. Install uv, Go 1.26.10 and the Bun
version `package.json` pins in `packageManager`, then run:

```bash
bash scripts/install-quality-gates.sh
```

Restart omp sessions after the extension changes: running sessions keep the old rails.

## Use

In an omp session inside a repository with a GitHub origin, run `/agentrails onboard`: the agent
runs the setup skills and records each section. Run `/agentrails onboard` again to answer a short
Q&A that writes `.agentrails/config.json`; until then the rails refuse ordinary work there.

The owner types these lines in the main session to write exceptions and approvals:

```text
$approve exception <RULE> <path-glob> [check=<check>] -- <reason>
$revoke exception <RULE> <path-glob> [check=<check>]
$approve approval <budget|ratchet|skip|exemption> <target> -- <reason>
$revoke approval <kind> <target>
```

After changing the config of a repository whose CI runner is `ci-gate`, run
`/agentrails export <file>`, then `ci_gate.py config-import <file>` on the NUC.

Run the engine with `uv run --project engine guardrails --help`; each subcommand has its own
`--help`.

## Limits

The rails scan shell text, so obfuscated commands can evade them. Keep server-side protection: a
default-branch ruleset and a required gate status only the gate identity can post.
