# Existing guardrail inventory

## Objective

Capture actual enforcement separately from documentation and proposals.

## Required actions

1. Inventory root/nested AGENTS files, skills and their triggers/references, system rules,
   user-level guidance and other accessible instruction surfaces.
2. Separate application-user and contributor documentation. Mark mixed files and identify the
   owning source for repeated rules.
3. Inspect tracked formatter, linter, type checker, package/lock, hook, test, CI, release and bespoke
   checker configuration. Capture exact commands, task targets, hook managers, job names, triggers
   and required default-branch statuses. Failed protection/rules queries mean unknown, not absent.
4. Inventory unit, integration, seam, journey, smoke, coverage, timeout, fixture and CI-matrix
   evidence. A config is active only if an invoked command reads it. Capture tiers, public entry
   points, ownership, affected-boundary-test selection, verification surface and approved budgets.
5. For every rule ID, cite the enforcing checker source line or `absent`, its invocation chain and
   file enumeration. Checkers enumerate Git-owned paths, including relevant untracked files, without
   following symlinks outside the repository. Identify narrower scopes, disconnected commands and
   waived-finding baselines with finding counts; baselined findings remain open.
6. Map imported dependencies, their owned adapters, vendored/generated origins and in-repository
   third-party stubs, copies or shims. Identify checker/type-checker exclusions, imports outside
   adapters, tests replacing third parties instead of owned interfaces and misplaced upstream fixes.
7. List every lock file and manifest returned by `git ls-files --cached --others --exclude-standard`
   across ecosystems. Map each to its audit's source line or `absent`; map manifests to locks.
   Name fixture/vendor exclusions with origin evidence. Gaps include unaudited/unreadable locks,
   unlocked dependencies, staged-only or scheduled-only audits, severity filters, advisory waivers
   and finding formats incompatible with the selected contract. Identify owner-only exception files.
8. Mark controls `active`, `manual`, `proposed`, `stale` or `unverified`; distinguish tracked assets
   from unrelated dirty/untracked work. Capture conflicts, duplicate authorities, configuration
   drift, existing failures and unsupported platforms with file references and observed results.
9. Present an inventory table: audience, owner, enforcement mode, source, trigger, stack
   applicability and current evidence. Never expose secrets or protected content.

## Done when

Each control has an owner and trigger or is explicitly unverified. Every lock and manifest has an
audit source or an explicit gap. Audience boundaries and contradictions are visible before design.
