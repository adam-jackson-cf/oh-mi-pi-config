# Agent rails: agent rules

Follow these when changing agent-rails, its skills, `guardrails`, the rails or the repo config
schema. The [design directive](docs/design-directive.md) explains each one; a change that
conflicts with it needs the owner's explicit decision first.

- Enforce in this order: deterministic code, then a Jev decision from a fixed action set, then agent
  instructions only for what neither can decide.
- Ship no workflow, stage sequence or workflow skill; the rails gate actions whatever flow made
  them.
- Keep one governed mode. Add no mode switch, skip default, fallback, baseline or old-format
  tolerance.
- Give each `.agentrails/config.json` section exactly one writer, as
  [docs/config.md](docs/config.md#sections-and-writers) lists.
- Never let a repository enforce a judgment that is shadow globally; only measurement promotes it.
- Put nothing from agent-rails or omp into a target repository. Add a missing rule to the repo's
  own controls by a repo PR, and never run a harness copy beside a repo control.
- Check and design only code the repository owns; exclude third-party code at the boundary with
  origin evidence.
- Never version code to keep results comparable; rerun the experiment.
- When a change adds, closes or alters a known limit, update
  [docs/known-limits.md](docs/known-limits.md) in the same change.
