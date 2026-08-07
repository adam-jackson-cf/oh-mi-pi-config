---
name: "codegraph"
description: "Use CodeGraph for per-repo code graph initialization, indexing, MCP installation, and graph-based code intelligence. USE WHEN a repo should be initialized, indexed, queried, or wired to CodeGraph MCP."
---

# Task

## Operating Model

- Treat CodeGraph as per-repo/per-project. A global CLI install does not create one global graph for all repos.
- Require a target repo path from the user request, or infer it only when the current working directory is clearly the intended repo.
- Run CodeGraph commands from the target repo root unless a command explicitly supports a path argument.
- Prefer CodeGraph MCP/query tools for ad hoc overview, symbol, definition, reference, graph, and impact work when a repo-local index is available.

## Preflight

- Verify the CLI is available with `codegraph --version`.
- Confirm the target path exists and is a repository or project directory.
- Inspect existing CodeGraph state with `codegraph status` from the target repo root when `.codegraph/` may already exist.
- Do not delete or recreate `.codegraph/` unless the user explicitly asks for a reset.

## Initialize A Target Repo

- Change to the target repo root.
- Run `codegraph init -i` to create the project-local `.codegraph/` directory and build the initial graph.
- Run `codegraph status` after initialization and report whether indexing succeeded.
- If initialization already exists, use `codegraph index` or `codegraph sync` instead of reinitializing.

## Install MCP For The Target Repo

- For Codex, prefer project-local MCP config at `.codex/config.toml`; do not fall back to global `~/.codex/config.toml` unless the user explicitly asks for global setup.
- Create `.codex/config.toml` in the target repo with:
  ```toml
  [mcp_servers.codegraph]
  command = "codegraph"
  args = ["serve", "--mcp"]
  cwd = "/absolute/path/to/target/repo"
  ```
- Use the target repo's absolute path for `cwd` so the MCP server serves the intended repo-local `.codegraph` index.
- Treat MCP installation as project setup work: report which target repo was configured and whether the project-local config exists.
- Validate with `codegraph status` and, when appropriate, note that the MCP server will serve the repo-local graph to the agent after Codex restarts or reloads the project.

## Common Repo Actions

- Use `codegraph index` for a full or explicit indexing pass.
- Use `codegraph sync` to update the graph after repo changes.
- Use `codegraph query <search>` for symbol/text search.
- Use `codegraph callers <symbol>` and `codegraph callees <symbol>` for call graph inspection.
- Use `codegraph impact <symbol>` for symbol impact analysis.
- Use `codegraph affected [files...]` for changed-file impact checks.
- Use `codegraph files` to inspect indexed files.

## Validation And Handoff

- Always report the target repo path, commands run, and final `codegraph status` result.
- If setup fails, include the failing command and the relevant error.
- Do not claim MCP is usable until installation completed for the target repo.
- For multi-repo requests, initialize and validate each repo separately.
