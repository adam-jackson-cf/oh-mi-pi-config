# Agent rails: agent rules

Follow these when changing agent-rails, its skills, `guardrails`, the rails or the repo config
schema. A change that conflicts with one needs the owner's explicit decision first.

- Enforce in this order: deterministic code, then a Jev decision from a fixed action set, then agent
  instructions only for what neither can decide.
- Ship no workflow, stage sequence or workflow skill; the rails gate actions whatever flow made
  them.
- Give each `.agentrails/config.json` section exactly one writer. Extension code writes the config
  only through `lib/config-writer.ts`; `lib/trust.ts` refuses every agent write.
- Put nothing from agent-rails or omp into a target repository; frozen digest-pinned records keep
  their wording. Add a missing rule to the repo's own controls by a repo PR, and never run a harness
  copy beside a repo control.
- Check and design only code the repository owns; exclude third-party code at the boundary with
  origin evidence (details: `skills/setup-repo-proofs/references/design-principles.md` principle
  7, when changing scope, typing or adapter code).
- No agent code reviews. Quality checkers run at commit, never inside `prove()`. Repo tests prove
  repo checkers; guardrails tests prove harness checkers.
- Checks trigger only from events (edit, commit, CI); add no proof for pulls or merges, no CI time
  budget, nothing a lone owner cannot approve.
- Put language facts (extensions, test patterns, entry modules, adapter vocabularies) in
  `engine/data/`, read through `foundation/conventions.py`, never in code.
- A rule's limit goes in its catalogue `contract.limits`.
