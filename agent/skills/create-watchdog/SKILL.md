---
name: "create-watchdog"
description: "Analyze a project and propose focused OMP watchdog guidance. USE WHEN you want to identify nondeterministic program-design instructions or architecture gaps and draft project watchdog guidance."
---

# Task

## Scope

- Treat the targeted project or current working directory as the analysis scope.
- Use applicable project instructions already present in the active context, including `AGENTS.md`; do not rediscover context files through filesystem search.
- Suggest watchdog guidance only; do not create or modify `WATCHDOG.md` or `WATCHDOG.yml` without a separate user request.

## Project Scout

- Delegate one read-only scout to extract nondeterministic program-design instructions from the active context and map the project layout, runtimes, languages, architecture, module boundaries, and existing mechanical enforcement.
- Require repository evidence for every claimed boundary, invariant, or gap.

## Classification

- Exclude requirements deterministically enforced by compilers, formatters, linters, tests, schemas, generated-code checks, or hooks.
- Retain judgment-based program-design guidance covering architecture, dependency direction, ownership, data flow, error handling, security boundaries, domain invariants, and maintainability.
- Extract relevant nondeterministic instructions from the active project context without duplicating their wording unnecessarily.

## Gap Analysis

- Identify material program-design gaps implied by the observed layout, runtimes, languages, and architecture.
- Do not invent generic best practices or recommend rules unsupported by project evidence.
- Report explicitly when no watchdog guidance is justified.

## Watchdog Proposal

- Recommend the minimum scoped `WATCHDOG.md` files, preferring `.omp/WATCHDOG.md` at the project root and narrower files only where guidance genuinely differs.
- For each proposed file, provide its path, evidence, rationale, and exact draft content.
- Avoid duplicating deterministic quality gates, existing project instructions, or advisor roster configuration from `WATCHDOG.yml`.

## Validation and Output

- Verify every proposed instruction against current repository evidence.
- Separate extracted instructions from newly suggested gap-filling guidance.
- Return a concise project summary, deterministic controls excluded, proposed files with exact content, and unresolved uncertainties.
