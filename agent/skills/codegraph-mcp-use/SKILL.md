---
name: "codegraph-mcp-use"
description: "Use CodeGraph MCP tools for indexed code discovery, exploration, understanding, flow tracing, and impact analysis. USE WHEN task involves code discovery, exploration, understanding, flow tracing, or impact analysis."
---

# Task

## Operating Model

- Use this skill when CodeGraph MCP tools are available and the task involves code discovery, exploration, understanding, flow tracing, or impact analysis.
- Treat CodeGraph as the pre-built code index for the current repo. Prefer it before raw `Read`, `Grep`, `rg`, or filesystem scanning for indexed source-code questions.
- Use raw file reads only for non-indexed files, docs, configs, missing CodeGraph coverage, or files named in a CodeGraph staleness warning.
- If CodeGraph reports that the project is not initialized, offer to initialize the target repo with the CodeGraph setup skill or `codegraph init -i`.

## First Checks

- Use `codegraph_status` when you need to confirm index health, project initialization, indexed file count, or pending sync state.
- If a tool response reports stale or pending files, trust CodeGraph for all files not named in the warning and read only the pending files directly.
- If the current task is a quick symbol lookup and CodeGraph MCP is unavailable, use the repo's normal search tools before raw file reads.

## Tool Selection

- Use `codegraph_explore` first for most tasks: how something works, architecture questions, bug investigation, feature planning, area survey, or flow tracing.
- Use `codegraph_explore` with multiple symbol or file names when the task asks how one thing reaches another or how a flow connects.
- Use `codegraph_search` only when you need to locate a symbol by name, kind, file, or signature.
- Use `codegraph_node` for a single named symbol or indexed source file when you need Read-equivalent source with line numbers and graph context.
- Use `codegraph_callers` for what calls a symbol.
- Use `codegraph_callees` for what a symbol calls.
- Use `codegraph_impact` for blast radius and refactor/change impact.
- Use `codegraph_files` for indexed project structure instead of scanning directories.

## Common Chains

- Area understanding: call `codegraph_explore` once with the natural-language question, then answer from the grouped source and relationship map.
- Flow tracing: call `codegraph_explore` with the relevant source and destination symbols; use follow-up calls only if the returned path is incomplete.
- Refactor planning: use `codegraph_search` to identify the target symbol, then `codegraph_callers` and `codegraph_impact` to estimate blast radius.
- Editing a known symbol: use `codegraph_node` with source included before editing so callers, callees, and impact are visible.
- Reading a known indexed source file: use `codegraph_node` with the file path instead of raw `Read`.

## Semantic Duplication Hunting

- Start with `codegraph_explore` on the repo, target directories, or natural-language responsibility area to identify dense modules, sibling implementations, and repeated domain responsibilities before reading files.
- Use `codegraph_files` when you need indexed project structure, especially to compare sibling modules or locate parallel provider, harness, command, format, or adapter directories.
- Use `codegraph_search` for bounded symbol-name patterns to find repeated function names, repeated method names across classes, or parallel APIs across related modules.
- For likely duplicates, inspect bodies with `codegraph_explore` or `codegraph_node`; compare responsibilities, control flow, validation rules, and data shaping rather than only exact text.
- Prioritize groups where multiple functions share the same domain responsibility and vary only by provider, harness, format, command, field name, or small policy differences.
- Use `codegraph_callers` and `codegraph_impact` to estimate refactor impact before recommending centralization.
- When exact names do not reveal semantic duplication, use `codegraph_explore` with a first-party-only structural prompt as the candidate generator, then validate each candidate with `codegraph_node` or focused raw file reads if CodeGraph omits needed detail.
- Report candidates as normalization groups: current functions, shared behavior, meaningful differences, proposed centralized abstraction, and relative priority.

## Anti-Patterns

- Do not grep first for symbols that CodeGraph can search.
- Do not run broad raw file discovery before trying `codegraph_explore` for architecture or understanding questions.
- Do not chain many `codegraph_node` calls when one `codegraph_explore` call can return related symbols grouped by file.
- Do not re-verify CodeGraph results with grep unless the task needs a detail CodeGraph did not include or the index is stale.
- Do not use CodeGraph as a substitute for tests, typecheckers, linters, or runtime validation.

## Handoff

- Mention which CodeGraph tools were used when the answer depends on graph context.
- If CodeGraph was unavailable, uninitialized, stale, or insufficient, state the reason and the fallback used.
- For code changes, pair CodeGraph impact context with the repo's normal validation commands.
