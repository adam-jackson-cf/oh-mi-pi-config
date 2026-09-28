# OMP routing mechanics (verified)

Each item was observed on OMP 18.x on 2026-09-28. Re-verify when OMP updates;
the docs live at `omp://models.md`, `omp://settings.md`,
`omp://task-agent-discovery.md`, `omp://tools/task.md`, `omp://prewalk.md`,
`omp://vibe-mode.md`, `omp://advisor-watchdog.md`, `omp://magic-keywords.md`.

## Model and effort resolution

- `modelRoles.<role>` holds `provider/model[:effort]`. Custom agents reach a
  role through frontmatter `model: ["@role"]`.
- Task model precedence: `task.agentModelOverrides[agent]` → agent frontmatter
  `model` → parent session model.
- **A role's `:effort` suffix beats frontmatter `thinkingLevel`.** Verified by
  spawning `plan-judge` with frontmatter `low` and role `:high`; the child
  session recorded `thinkingLevel: high`. Keep effort only in `config.yml`.
- `defaultThinkingLevel` governs the main session. `auto` asks the `judge`
  role per prompt and can resolve up to `providers.autoThinkingMaxEffort`,
  which only accepts `xhigh|max`, so `auto` cannot be capped at medium. A
  medium cap therefore needs `defaultThinkingLevel: medium`.
- `ultrathink` raises effort for one turn only when thinking is `auto`.
- **`:off` is not "no reasoning" on `openai-codex/gpt-6-luna`.** It omits the
  effort field, so the server default applies: probes emitted encrypted
  reasoning and 65–110 output tokens, matching `medium`. `:minimal` clamps to
  `low`, the lowest reachable effort, which emitted zero reasoning on the same
  probes. Probe any new model the same way before trusting `:off`.

## Tasks and workflows

- `task.maxRecursionDepth` defaults to 2: main → lead agent → peer works; a
  peer cannot spawn further.
- `task.enableEffort` exposes per-spawn `effort: lo|med|hi` mapped onto the
  resolved model's lowest/middle/highest effort, clamped by `task.maxEffort`.
  The clamp is global, so a low `maxEffort` also caps council spawns.
- `task.agentServiceTierOverrides[agent]` sets the Fast/priority tier per agent;
  Vibe workers use `tier.subagent` instead.
- `task.agentPrewalk` / `prewalk.*`: start on one model, hand off to `@smol`
  at the first edit after a todo exists.
- `/vibe`: `fast` = bundled `sonic` (`@smol`), `good` = bundled `task`
  (`@task`); route through `task.agentModelOverrides`.
- Eval `agent()` / `workpool()` build run-time DAGs; magic keywords
  `orchestrate`, `workflowz`, `jevify` inject workflow contracts for a turn.
- `--plan-yolo` plans read-only, then switches to `--plan-yolo-into` (default
  `@smol`) to implement.

## Advisor

- With a `WATCHDOG.yml` roster, each entry uses its own `model`;
  `modelRoles.advisor` applies to roster entries without one and to subagent
  advisors enabled through `task.agentAdvisor`. The owner keeps it fixed as
  part of the Jev watchdog setup (see `preferences.md`).

## Files and runtime

- `agent/agents/` is a tracked directory in this repo; `~/.agents/roles` is a
  symlink to it for other consumers.
- New tracked agents or skills need a `.gitignore` allowlist entry.
- Restart OMP after changing config, agents, skills, or extensions; running
  processes keep stale state. Task and eval preflight reload settings per
  spawn, but the main session's model and thinking are fixed at startup.
- Headless trials: `omp -p ... < /dev/null`. Without closed stdin, print mode
  waits on input and hangs when launched from another process.
