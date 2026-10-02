# Optimisation process

Run when a model launches or retires, prices or quotas change, a quota runs
out, or trace data suggests a role underperforms.

## 1. Snapshot the evidence

```bash
bun ~/.omp/agent/skills/omp-config-optimise/scripts/snapshot.ts
omp usage --history --days 14 --redact
```

The snapshot joins every role with catalog prices and supported efforts, flags
roles whose model is missing from the catalog, and lists subscription headroom
and spend by model. Then refresh the external figures in
`pricing-and-quota.md` from its sources.

## 2. Find what changed

- New or retired models in either subscription family; price or credit changes.
- A quota that peaked near 100% while the other provider had headroom.
- Roles pointing at models absent from the catalog or scheduled to retire.
- Performance evidence: failed or retried subagent runs, judge verdicts,
  experiment results, `evaluate-jev` findings.

## 3. Decide against the preferences

Map each role to the model and effort that `preferences.md` prescribes, then
check these invariants:

- No Opus or Astra role above medium outside `innovation-council`,
  `experiment-design`, and their peers (`innovation-challenger`,
  `experiment-peer`).
- Judges and reviewers run in an independent session (fresh subagent or process, no shared
  context with the author); same model family is allowed; GPT Sol is preferred.
- Effort lives only in `config.yml` role suffixes, not agent frontmatter.
- Every agent with `model: ["@x"]` has a `modelRoles.x`, and every custom role
  is used by some agent or built-in workload.
- Luna routes use the lowest reachable effort unless an experiment shows a
  higher level is needed for that work.

A capability claim (for example "a new model needs no reasoning for this
work") becomes routing only after an experiment (`experiments.md`). Present
the proposed diff and its evidence to the owner before applying unless they
asked for the change directly.

## 4. Apply

- Edit `agent/config.yml`; add or retire agents in `agent/agents/` and update
  the `.gitignore` allowlist and `readme.md` agent list.
- Update `routing.md` with the new table, date, and evidence links.

## 5. Verify in a fresh process

- Rerun the snapshot: no unresolved roles.
- Restart OMP, then spawn each changed agent with a trivial task and read the
  child session header for the resolved model and thinking level:
  `~/.omp/agent/sessions/<cwd>/<session>/<AgentName>.jsonl` → first
  `model_change` and `thinking_level_change` entries.
- For service-tier changes, confirm the tier in the child session and watch
  `omp usage` over the next working session.
