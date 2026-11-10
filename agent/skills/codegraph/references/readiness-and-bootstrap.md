# Readiness and bootstrap

Get a usable per-repository graph; never reset destructively.

- Graphs are per repository; a global CLI install creates no global graph.
- Target the requested repository; use the current directory only when it is clearly the target.
  Run from the repository root unless the command accepts a path.
- MCP tools available: `codegraph_status`. Otherwise: `codegraph --version`, then `codegraph status`, before ordinary search tools.
- Not initialized: `codegraph init` (indexes by default; `-i` is a deprecated alias).
- Git repository, after init: `git check-ignore -q .codegraph`. Non-zero exit: propose adding `.codegraph/` to `.gitignore` or `.git/info/exclude`; edit only after approval.
- Initialized but no MCP: use CLI graph queries for the session.
- Never delete or recreate `.codegraph/` unless the user explicitly requests a reset.
