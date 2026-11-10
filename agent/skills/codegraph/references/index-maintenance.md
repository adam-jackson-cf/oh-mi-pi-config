# Index maintenance

## Objective

Initialize, index, and keep each target repository's graph current.

## Guidance

- Run `codegraph init` for a new repository and verify it with `codegraph status`.
- Use `codegraph index` for a full indexing pass.
- Rely on the MCP server's file watcher while `codegraph serve --mcp` runs; it keeps the index current unless the server was started with `--no-watch`.
- In CLI-only sessions, run `codegraph sync` before querying whenever files changed since the last index, including after pulls and branch switches.
- Run `codegraph unlock` only when indexing reports a stale lock and no other CodeGraph process is running, then rerun the blocked command.
- Initialize and validate each target independently in multi-repository requests.
