# MCP registration

## Objective

Register the CodeGraph MCP server for the user's agent and prove it works in a fresh session.

## Guidance

- Ask the user which agent to register and whether the scope is this project (`local`) or every project (`global`); do not assume either.
- Run `codegraph install --print-config <id>` to preview the snippet and target file without writing; an unknown id prints the supported ids.
- Check how the CLI is installed with `which -a codegraph`; `codegraph install` without `-y` stops at interactive prompts, and its PATH-install step can add a second copy beside a version-manager install.
- For a supported agent, register through the agent's own MCP add command using the printed snippet (for `claude`: `claude mcp add --scope <project|user> codegraph -- codegraph serve --mcp`); run `codegraph install --target <id> --location <local|global> -y` only when the CLI is not on PATH or the user approves a PATH install.
- For the `claude` target, ask whether to auto-allow CodeGraph tools; on yes, add `mcp__codegraph` to `permissions.allow` in the matching user or project settings, and when using `install`, pass `--no-permissions` on no.
- For an unsupported agent, add `mcpServers.codegraph` to that agent's MCP config with command `codegraph` and arguments `["serve", "--mcp"]`; set `cwd` to the repository's absolute path only when the agent does not send a workspace root.
- Verify with the agent's own MCP listing or test command and confirm the `codegraph_*` tools appear.
- Claim future-session availability only after a fresh agent process lists the tools; otherwise report the registration as written but unverified.
