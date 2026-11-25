# Existing control inventory

## Objective

Capture what the target actually enforces, separately from what it only documents or proposes,
with rule-ID evidence for every claim.

## Required actions

1. Run `python3 <skill>/scripts/inventory_controls.py --repo <target>` (expect exit 0; exit 2 means not a Git work tree).
   It prints JSON: make targets, hook managers and `core.hooksPath`, pre-push hooks, CI workflow jobs, default-branch
   required checks, tool configs, language file counts, lock files and manifests. Add `--offline` when `gh` is unavailable.
   A required-check query that fails is `unknown`, never "no required checks".
2. Inventory root and nested AGENTS files, agent skills with their activation triggers and linked references, system rules,
   user-level guidance and any other instruction surfaces accessible under target policy. Files inside a Git submodule or
   vendored source are context only. Record every instruction or installed skill that states test tiers, tier commands,
   budgets or deferrals, with its path and line, so Step 6 updates it when a selector or command changes.
3. Separate documentation for application users from contributor/developer documentation.
   Mark mixed files and identify the owning source for each repeated rule.
4. Read tracked formatter, linter, type checker, package/lock, hook, test, CI, release and bespoke-check configuration.
   Record the exact command and trigger that runs each check. Do not treat a config file as active unless an invoked command reads it.
5. List test layers and evidence: unit, integration, seam, journey, smoke, coverage, timeouts, fixtures and CI matrix.
   Record where each per-repo value already lives: tier selectors in the test-runner config, coverage floors in the coverage
   config, packages in the package manifest, owner-only paths in `CODEOWNERS`. When a `proofs.toml` exists, record its claims,
   approved exclusions, exceptions and settings, and rerun its validator.
6. For each bespoke checker, record the rule family it enforces ([verification layers](verification-layers.md)),
   each catalogue rule ID it enforces with the source line, the enforcement chain that runs it (for example hook to make target to script)
   and how it lists files. Search the repo's checkers for every rule ID of every family and record a source line or `absent`;
   for a tool rule the evidence is the config line holding its selector or value (a `PLR0904` select), not the catalogue ID.
   A claim that the repo covers or lacks a rule needs that evidence.
7. Flag checker gaps: a checker that walks the disk instead of `git ls-files --cached --others --exclude-standard`,
   follows symlinks out of the repo, scans a narrower scope than claimed, or no deterministic point (installed hook, required CI
   status) runs it.
   Record any baseline (a file of waived findings) each checker or tool applies, with its finding count;
   a baselined finding is an open finding.
8. Map the third-party boundary ([design principles](design-principles.md), principle 7): dependencies the code imports,
   the owned adapter each is reached through, vendored or generated paths with origin evidence, and every in-repo stub, copy
   or shim of third-party code. Record whether each checker and type checker reaches third-party code and how it excludes it,
   owned code importing a third party outside its adapter, tests replacing a third party rather than an owned interface,
   and upstream workarounds outside an adapter.
9. Map the dependency-audit scope ([dependency audit](dependency-audit.md)): every lock file and manifest the inventory lists,
   in every ecosystem, with its ecosystem and the audit that covers it (source line) or `absent`; for a manifest, the lock
   that pins it. Record as gaps: a lock no audit covers, a lock format the audit cannot read, a manifest with dependencies and no lock,
   an audit run only on staged files or on a schedule, and an audit that scores severity, waives advisories or prints findings off the
   catalogue grammar. Record any exception file and whether owner review protects it.
10. Mark each discovered control `active`, `manual`, `proposed`, `stale` or `unverified`. `active` needs a deterministic point
    that runs the control and blocks on failure; a rule carried only by agent instructions, contributor docs or a CI job whose
    status nothing requires is `manual`, never `active`. Distinguish tracked assets from dirty or untracked work.
11. Capture rule conflicts, duplicated ownership, tool-config drift, baseline failures and unsupported platforms with
    file references and observed command results. Avoid reading protected content or printing secrets.
12. Present an inventory table: audience, owner, enforcement mode, source, invocation trigger, stack applicability and current proof.
    Write it to `state.json` `inventory` ([run state](run-state.md)).

## Done when

- Each existing control has an owner and trigger, or is explicitly marked unverified.
- Every catalogue rule ID has a repo source line or `absent`; every lock file and manifest names its audit or is `absent`.
- User-facing guidance and developer practice are separated, and contradictions are visible before new controls are designed.
