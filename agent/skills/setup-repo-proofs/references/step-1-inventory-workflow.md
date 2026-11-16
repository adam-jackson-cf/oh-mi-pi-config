# Existing guardrail inventory

## Objective

Capture what the target actually enforces and what it only documents or proposes.

## Required actions

1. Inventory root and nested AGENTS files, agent skills with their activation triggers and linked
   references, system rules, user-level guidance, and any other instruction surfaces accessible
   under target policy.
2. Separate documentation for application users from contributor/developer documentation. Mark mixed
   files and identify the owning source for each repeated rule.
3. Read tracked formatter, linter, type checker, package/lock, hook, test, CI, release, and
   bespoke-check configuration. Record the exact command and trigger that runs each check.
   Discover the inputs the record needs: make (or task runner) targets, pre-commit or other hook
   manager config, CI workflow job names, and the default branch's required checks from
   `gh api repos/<owner>/<name>/branches/<default>/protection` and
   `gh api repos/<owner>/<name>/rules/branches/<default>`. A query that fails is recorded as
   unknown, never as "no required checks".
4. List test layers and evidence: unit, integration, seam, end-to-end, smoke, coverage, timeouts,
   fixtures, and CI matrix. Do not treat a config file as active unless an invoked command reads it.
   When the record exists, read its `profile.md`; record its tiers, entry points, ownership
   areas, affected-T2 selection, verification surface and per-machine budget approvals.
5. For each bespoke checker, name the `omp-guardrails check` step whose rule family it enforces
   (`STEPS` in `omp_guardrails/whole_repo.py`), the rule IDs it enforces, with the source line for
   each, the `checks.commit` chain that runs it (for example make target to script) and how it lists
   files. A step's rule IDs are the catalogue rows (`omp_guardrails/data/guardrail-rules.json`)
   whose `reference.subcommand` names it. A checker that walks the disk instead of `git ls-files
   --cached --others --exclude-standard`, or follows symlinks out of the repo, is a gap to fix in
   the repo checker. Search the repo's checkers for every rule ID of every step, and record a
   source line or `absent` for each: a claim that the repo covers or lacks a rule needs that
   evidence. Record any baseline (a file of waived findings) each repo checker or tool applies,
   with its finding count; a baselined finding is an open finding.
6. Map the third-party boundary (design directive principle 10). List the dependencies the code
   imports, the owned adapter (if any) each one is reached through, vendored or generated
   third-party paths with evidence of their origin, and every in-repo stub, copy or shim of
   third-party code. For each checker and type checker, record whether it reaches third-party code
   and how it excludes it. Record owned code that imports a third party outside its adapter, tests
   that replace a third party rather than an owned interface, and workarounds for upstream faults
   outside an adapter.
7. Map the dependency-audit scope (`SECURITY.dependency-audit`). List every lock file and
   dependency manifest that `git ls-files --cached --others --exclude-standard` returns, in every
   ecosystem. For each, record its ecosystem and the repo audit that covers it, with the source
   line that reads it, or `absent`; for a manifest, also record the lock that pins it. Record each
   excluded fixture or vendored path by name, with evidence of its origin. Each of these is a gap:
   a lock file no audit covers, a lock format the audit cannot read, a manifest with dependencies
   and no lock, an audit that runs only on staged files or on a schedule, and an audit that scores
   severity, waives advisories or prints findings in another format than the catalogue row's
   `advisory_line`. Record the repo's dependency-audit exception file, if any, for the record's
   `ownerOnlyPaths`.
8. Mark each discovered control `active`, `manual`, `proposed`, `stale`, or `unverified`;
   distinguish tracked assets from dirty or untracked work.
9. Capture rule conflicts, duplicated ownership, tool-config drift, baseline failures, and
   unsupported platforms with file references and observed command results. Avoid reading protected
   content or printing secrets.
10. Record a concise inventory table with audience, enforcement mode, source, invocation trigger,
    stack applicability, and current proof.

## Done when

- Each existing control has an owner and trigger, or is explicitly marked unverified.
- Every lock file and manifest in the repository names the audit that covers it with a source
  line, or is marked `absent`.
- User-facing guidance and developer practice are separated, and contradictions are visible before
  new controls are designed.
