# Rule contract mapping

## Objective

Translate every selected rule into an exact target-stack enforcement obligation.

## Guidance

1. Read the complete authoritative contract selected for the project, including language-neutral
   rules. Preserve each ID, semantic, threshold and target-derived requirement, not just categories.
2. Map every ID to its target construct, enforcing source/configuration, exact setting, scanned
   paths, exclusions and local/CI invocation. Universal rules stay applicable in every stack;
   conditional exclusions need evidence that the construct is absent and explicit owner approval.
3. Derive dependency-direction allowlists, folder ownership, symbol/path naming and test placement
   from project data. Unmapped edges/files fail. Map boundary tests to ownership areas; unmapped
   boundary tests fail, and unknown changed-code ownership selects all boundary tests. Diagnostics
   identify the file and corrective edge, name or destination.
4. Map timing budgets, test-growth justification and duplicate-path rules to normalized test evidence
   and base-ref comparisons as required by the selected contract. A slow test needs a speed-up fix,
   not a tier move. Define missing/invalid/stale evidence failures and approval requirements.
5. Map local/CI parity and commit gates to the project's real commands, preserving failure
   propagation. Capture selector, limit, exceptions and pinned version for each tool rule; a rule
   can have both a selector and a limit.
6. Give each control one owner. Keep an existing control that covers every required ID and has no
   waived-finding baseline. Otherwise grow it with missing rules, complete Git-owned file scope,
   invocation wiring and ordinary tests; fix all waived findings and remove the baseline. Never
   install a duplicate authority while waiting for that change, or offer it as a fallback if the
   owner declines. A project without a suitable checker needs an implementation before it passes.
7. Dependency audits always belong to the project and cover every Step 1 lock/manifest in local
   and CI gates. Fix every uncovered format, unlocked dependency, narrow trigger, severity filter,
   waiver or incompatible finding line in that audit. Preserve owner-only exception boundaries.
8. Keep type-checking first-party imports visible and installed third parties silent. For mypy,
   follow imports; name vendored-module `follow_imports = silent` overrides. Use upstream types,
   named `follow_untyped_imports = true` overrides for packages without `py.typed`, or published
   stubs. Ignore missing imports only for a package with neither. Remove in-repository third-party
   stubs/copies/shims and their checker exemptions; scope controls to owned code, move third-party
   imports/workarounds into owned adapters and make tests replace owned interfaces. Cite upstream
   issues for upstream workarounds.
9. Keep an unavailable implementation blocked until it lands with tests. Capture deviations as
   unresolved decisions, not passes. Reuse ownership, naming and runner policies.

## Done when

Every selected ID has an implementation, evidence-backed conditional exclusion or blocker.
No applicable rule is represented solely by prose, a tool name or untested configuration.
