---
name: "evaluate-jev"
description: "USE WHEN designing, building, improving or evaluating a Jev (TypeSafe) judgment or a process that uses one, including replacing an LLM prompt-and-parse step with a typed decision."
---

# Task

Design, develop and evaluate processes that use Jev, TypeSafe's System One model. Paths in this
file are relative to this skill's directory. Pick the mode from the request; development usually
loops through all three.

- **Design**: a new judgment or process, or new or rewritten questions. Read
  [jev-design.md](references/jev-design.md).
- **Develop**: make a process good enough to use, or improve one that underperforms. Read
  [development.md](references/development.md), and jev-design.md for each change.
- **Evaluate**: measure how well a process decided. Follow the Evaluate procedure below and the
  [rubric](references/rubric.md).

## TypeSafe docs

The live docs are the source of truth for concepts, question guidance, API and SDK contracts,
models and limits; read the relevant pages as part of the task. Start at the index
`https://docs.typesafe.ai/llms.txt` and read targeted pages, appending `.md` to a page path. Before
writing an integration, read the current API or SDK page and the guidance for the primitives in
use; for a new workflow also read the closest cookbook. If live access fails, use local docs or the
installed SDK's types, say so, and do not invent version-dependent details. Typed output guarantees
the interface, not truth: judge accuracy only against labels.

## Fresh-instance rule

A host loads a process's code, questions, rules and modes when a session or process starts. After
creating or changing a process, the current session, and every other session already running,
still runs the old version. So:

- Run every smoke test, replay through the live hooks, and evaluation of new behaviour in a newly
  started instance (for a CLI agent, a fresh non-interactive run). Tool calls made in the session
  that made the change are not evidence of the new behaviour.
- Bump the process version with every change and select records by version, not by time: old
  sessions keep writing old-version records after the change.
- Before reporting, say which version the evidence came from. Flag any evaluation that mixes
  versions or ran in a session started before the change.
- Evidence does not carry across versions. When questions, state, rules or composition change,
  earlier figures no longer describe the process: tag every quoted figure with its version and
  re-measure before relying on it.

## Rules are binary

A rule or policy states only a condition of what is judged; it never names who may do it. Approval
is the action mechanism (confirm prompt, block, review gate), recorded beside the decision.
Automated actors avoid guarded resources structurally, never through exemptions. Details:
[jev-design.md](references/jev-design.md), "Rules state conditions; approval is a mechanism".

## Scope

- Never print credentials, API keys, or raw provider response bodies.
- Evaluate mode is read-only over decision records, labels and wider evidence (transcripts, files,
  pages). It does not change questions, thresholds, rules, modes or routing; it reports
  recommendations, including the value decision in [development.md](references/development.md).
- Design and Develop modes change the process; keep each change versioned and verified in a fresh
  instance.
- Record labels in a process's own label store only when the user asks (see the labels file in
  [case-format.md](references/case-format.md)). Development labels belong to the experiment's own
  files until the user adopts them.

## Select the process profile

1. Use the profile path the user gives. Otherwise search the workspace for profile files
   (`grep -rl '^# Profile:'`) and pick the one naming the requested process.
2. If none exists, build one with [profile-template.md](references/profile-template.md) (worked
   example: [example-profile.md](references/example-profile.md)); ask the user only for fields the
   code and records cannot settle.

## Evaluate procedure

1. Confirm the version under evaluation and that its records came from instances started after
   the change (fresh-instance rule).
2. Produce case lines with the profile's `Cases` command, then summarize:

   ```sh
   bun scripts/summarize.ts --cases-in <cases.jsonl> --version <confirmed-version> \
     [--labels-in <labels.jsonl>] --cases-out /tmp/jev-cases.jsonl
   ```

   (or `node scripts/summarize.ts …` on Node ≥ 23.6 with `zod` installed; `bun` installs it
   automatically). Pass the version confirmed in step 1. Without `--version` the summarizer picks
   the version of the newest record, which can be wrong while old sessions still write records.
   `--version all` widens; `--since`/`--until <ISO>` set a window; `--threshold` sets the
   profile's action threshold. Report unversioned cases separately.
3. Check operational health first (rubric R5). If input sufficiency is below 80%, lead with that;
   accuracy on missing-context cases says nothing about Jev.
4. Score every in-window case with the rubric (R1–R4) using the profile's R1 criteria, label kind
   and R2 procedure. For more than ~30 cases, delegate scoring in batches to subagents, giving each
   the rubric path, the profile path and a case slice; require the per-case evidence citation.
   Spot-check disagreements yourself.
5. Confirm every case in the profile's confirm set against its wider evidence before it counts.
6. Report:
   - Process, profile, window, version, resolved model, and case counts.
   - The value table against the profile's baseline ([development.md](references/development.md),
     "Decide") and a recommendation: keep, ship, or the next iteration to run.
   - R3 counts with denominators, case IDs for every error, and each error's attributed cause.
   - State or question defects (R1 < 2 patterns, question defects) and their likely cause.
   - What it would take to improve the weakest result; a shortfall against a target is the next
     iteration's input, not a verdict on the process.

## References

- [jev-design.md](references/jev-design.md): programming model, docs map, patterns, question
  design, composition, reading probabilities, integration checklist.
- [development.md](references/development.md): framing, replay-and-label evidence, the iteration
  loop, the value decision, shipping, and a worked example.
- [rubric.md](references/rubric.md): R1–R5, error attribution, aggregate verdict.
- [case-format.md](references/case-format.md): the summarizer's input and labels files.
- [profile-template.md](references/profile-template.md) and
  [example-profile.md](references/example-profile.md): binding a process to the rubric.
- `scripts/cases.ts`: the shared metrics (version selection, label folding, confusion, threshold
  sweep, cost and latency) for tools that want identical numbers.
