# Design principles

Every setup decision follows these principles. A choice that conflicts with one needs the owner's explicit decision first.

## Principles

1. **The repo stays independent.** Every control lives in and runs from the target repository: its checkers, tool configs,
   hooks, CI jobs, `proofs.toml` and validator. Anyone can clone, build, test and run its checks without this skill or any agent
   tooling. Setup run state in `.proofs/` is never read by a check.
2. **Grow the repo's controls; never duplicate them.** Where the repo can hold a control (its own checkers, linters, test runner or
   CI gate), add the missing rules to it. Never run a second copy of a rule beside an owning control, even while a change is pending,
   and never as the route when the owner declines that change. Every claim that a control covers or lacks a rule cites source
   evidence, rule ID by rule ID. The owning control is the authority: never reshape code to satisfy a duplicate copy.
3. **Each value has one repo-owned home.** Roots, exclusions, allowlists, tiers, budgets, floors and exceptions live in the native
   config that uses them (tool, test-runner, coverage or hook config, CI workflow, package manifest, `CODEOWNERS`); only a value
   with no native home lives in `proofs.toml`, and none lives in a checker's source or an external location. Never restate a value
   in a second file. Defaults grant nothing: an unset allowlist approves no path, an unset scope covers every maintained root.
4. **Required means required.** No optional checks, skipping defaults, fallbacks, compatibility shims or tolerance of old formats.
   A cutover migrates every caller and deletes superseded fields and code. Many failures are cleaned up, never answered by softening the check.
5. **Gates block without baselines.** A file of waived findings is never an accepted state. Whole-repository scope is the default;
   an approved `changed` or `paths` rollout narrows which files are gated, keeps every rule, and keeps reporting the remaining
   whole-repository findings as open work. Placement never requires moving existing code.
6. **Enforcement is deterministic.** A control is enforced only when an installed hook, a CI job whose status host protection
   requires, or a host setting runs it and blocks on failure. Agent instructions, contributor docs and CI jobs whose status nothing
   requires never count as enforcement. Commit runs the hook; push and pull request run CI; merge needs every required status green
   on the exact head; T3 and owner-approved deferred T2 tests run on a later job (schedule, by hand or release gate).
   Approvals work for a single owner.
7. **Verify and design only the code the repository owns.** Installed packages, vendored copies, generated clients and toolchains
   are excluded at the boundary, each by name with origin evidence; a vendored copy the repo edits is owned code.
   - Checkers list owned files from `git ls-files`; anything a checker's derived scope cannot cover is a failure, not a skip.
   - Never keep in-repo copies, generated stubs or shims of third-party code to satisfy a rule, or exempt them from checkers.
     A dependency upgrade never requires regenerating files in the repo.
   - Third-party types come from upstream annotations or published stub packages.
   - Owned code reaches a third party through an owned adapter; structure, naming and complexity rules judge that interface.
   - Tests substitute the owned adapter interface at the third-party boundary, never the third party itself and never other owned
     code (`TEST.no-self-mocking`). Adapter tests prove how the repo uses the third party, never its own behaviour.
     Coverage and test integrity measure owned code.
   - Fix a wrong call in owned code; keep a workaround for an upstream fault inside its adapter, citing the upstream issue.
8. **Deterministic first; proof means outcome tests.** Use a deterministic check wherever possible and reviewer judgment only where
   none can work. A control is proven by tests that accept valid input and reject a violation with an actionable diagnostic.
9. **Never version code to keep results comparable.** A code change that invalidates an experiment means the experiment is rerun.
10. **History keeps its wording.** ADRs and frozen, digest-pinned study or evaluation records are never rewritten by setup.

## Implementation integrity

- No placeholder bodies (`pass`, `...`, `raise NotImplementedError`) outside declarations (`CODE.no-placeholder-bodies`);
  non-Python stub bodies are a review judgment, not a deterministic check.
- No mock imports or calls in product code outside the declared test, support and fixture paths (`CODE.no-product-mocks`).
- No added comments, including docstrings, on added or touched lines; tool directives are not comments (`CODE.no-comments`).
- Never silently swallow exceptions (`RUFF.SELECT.S110`), catch overly broad exceptions (`RUFF.SELECT.BLE001`) or use
  `contextlib.suppress` to hide a failure (`RUFF.SELECT.SIM105`).
- Never suppress, bypass, default or work around quality-gate failures, deprecation warnings or test failures, and never alter,
  disable, loosen or conditionally bypass gates or tests (`--skip`, `--no-verify`).
- Never add fallback modes or temporary strategies to meet a requirement, and never use hardcoded values or stubs outside tests.
- After a failure, never change or abandon the task without the owner's permission.

## Clean cutovers

- Never implement backward compatibility or support both new and legacy paths; remove legacy references, version-suffixed names
  (`parser-v2.py`) and legacy-only items.
- Preserve exact wording when reusing concepts, labels, limits or instructions across related edits; never introduce equivalent rewrites.
- Deprecated skills stay untouched until a final owner-signed-off cleanup change; migrate every caller now.

## Regression fixes and verification scaling

- Name the broken rule beyond the one input that exposed it, repair its root cause, and report whether behavioural coverage protected it.
- Scale verification to risk: focused for narrow changes, broader for shared or user-facing behaviour.
