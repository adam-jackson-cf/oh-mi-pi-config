# Query selection

Use the narrowest graph operation for the question. CLI mode (no MCP): pass `--json`, and `-p <repository path>` when not at the repository root.

| Question | MCP | CLI |
| --- | --- | --- |
| Architecture, behaviour, bug investigation, flow tracing, feature planning (start here) | `codegraph_explore` | `codegraph query` the key symbols, then `callers` and `callees` on the top matches |
| Locate symbols | `codegraph_search` | `query` |
| One symbol or indexed source file with graph context | `codegraph_node` | `codegraph query <symbol> --json`, then read the returned file span |
| Call relationships | `codegraph_callers`, `codegraph_callees` | `callers`, `callees` |
| Blast radius | `codegraph_impact` | `impact` |
| Indexed structure | `codegraph_files` | `files` |
| Tests a change reaches | — | `codegraph affected <changed files>`, then run those tests |
