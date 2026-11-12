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
5. Mark each discovered control `active`, `manual`, `proposed`, `stale`, or `unverified`;
   distinguish tracked assets from dirty or untracked work.
6. Capture rule conflicts, duplicated ownership, tool-config drift, baseline failures, and
   unsupported platforms with file references and observed command results. Avoid reading protected
   content or printing secrets.
7. Record a concise inventory table with audience, enforcement mode, source, invocation trigger,
   stack applicability, and current proof.

## Done when

- Each existing control has an owner and trigger, or is explicitly marked unverified.
- User-facing guidance and developer practice are separated, and contradictions are visible before
  new controls are designed.
