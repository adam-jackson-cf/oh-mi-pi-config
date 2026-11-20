# Harness setup deltas

## Objective

Add the record, engine and approval mechanics to each step of the generic repository setup.

## Shared rules

- Run [generic setup](skill://setup-repo-proofs) Steps 0–7 in order and apply each step's delta below or in its linked
  reference: [Step 1](setup-inventory.md), [Step 2](setup-rule-mapping.md), [Step 3](setup-candidates.md),
  [Steps 4–5](setup-approval.md) and [Steps 6–7](setup-cutover.md). It is not a second setup system;
  never copy these mechanics into the target repo.
- [The record contract](../../../../../lib/record/README.md) owns origin resolution, the gate schema, strict loading,
  command arrays, merge policy, CI runner roles, commit events, device rollout, proposal storage
  and write authority. [The record profile](../../../../../lib/record/profile.md) owns profile settings and approvals.
  [Adapter authoring](adapter-authoring.md) owns the adapter interface and conformance.
- `<omp root>` is the oh-mi-pi-config checkout, never the target. The engine lives at
  `<omp root>/agent/extensions/dynaflows/engine/`; run it as `uv run --project <omp root>/agent/extensions/dynaflows/engine
  guardrails <subcommand>`. It covers the whole repo checkout and writes nothing into the repo tree.
  Never install, pin, copy or fork it into a target. `guardrails catalogue` lists its rule catalogue.
- A stack gets a skill reference once two repositories need it (the rule of two); Python has `guardrails`.
  The engine's own tests in oh-mi-pi-config prove every rule it implements, once for every repository.
- Harness process additions apply only with dynaflows enabled for the target
  ([ADR 0004](../../../../../../docs/adr/0004-dynaflows-flag-gates-the-harness-process.md)); off means ordinary Git
  safety and repo-native checks.
- The target never depends on, contains or mentions omp or dynaflows. It keeps only conforming code and the controls the
  project would want without the harness: build, tests, its own linters and checks, gate targets, hook config and
  CI job. Harness opinions and their checks live in oh-mi-pi-config; per-repo settings live in the record.
- The repo's own controls come first under the [design directive](../../../AGENTS.md) principle 2.
  The record's `guardrails.check.repo` lists the checker files that enforce each step's rules, and `guardrails check`
  leaves that step out. [Step 2](setup-rule-mapping.md) defines the `repo`, `grow` and `harness` outcomes.
- Harness `guardrails path-check` is the explicit
  [ADR 0002](../../../../../../docs/adr/0002-harness-owns-path-checks.md) exception: it always runs with the required
  profile `guardrails.structure`, `guardrails.naming` and `guardrails.paths` settings, even where repo checks
  overlap, and `guardrails.check.repo` never delegates it. `gate.json` has no path-check command field;
  repo `checks.commit` and `checks.ci` stay repo-owned.
- Harness checks cover the whole repository without baselines; generated, vendored and fixture exclusions come from the
  record. Naming, structure and path-check use the new-path event scope of
  [ADR 0012](../../../../../../docs/adr/0012-file-parent-repeats-and-scoped-exceptions.md).

## Step 0: safety and modes

1. Record candidate scope exclusions only for generated, vendored and fixture paths, with evidence; they become record
   config after approval. Third-party code is excluded at the boundary (design directive principle 10).
2. Select the temporary candidate root outside the target checkout and outside the records directory.
   Steps 0–5 make no record writes either.
3. Record every setup mode that applies:
   - `greenfield` when no record exists and the target has no guardrail checks;
   - `adopt` when the repo has its own checkers or configurable tools that enforce, or could hold, harness rule families
     that the record, if one exists, does not give them in `guardrails.check.repo` (Step 2's `repo` and `grow` outcomes);
   - `extend` when a record exists and only a missing part is added, such as a verification surface.
   An existing record does not rule out `adopt`.
4. Never build a second guardrail system beside the repo's own, and never reshape repo code to satisfy the harness copy
   of a rule the repo's checker carries.
