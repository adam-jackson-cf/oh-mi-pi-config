# Delivery principles

Read before the first implementation edit. Apply clean cutovers to refactors and the regression rule to bug fixes.
Tags separate deterministic enforcement, agent judgment and guidance; a judgment tag claims no calibrated blocking checker.

## Implementation integrity

- No Python stub bodies (`pass`, `...`, `raise NotImplementedError`); declarations exempt. Enforced by CODE.no-placeholder-bodies (guardrails diff-check).
- Non-Python stub bodies remain an agent judgment, not a deterministic check. Judged by main/implementation-integrity.
- Never add mock imports or calls to product code outside profile test, support and fixture paths. Enforced by CODE.no-product-mocks (guardrails diff-check).
- Outside tests, never use hardcoded values or stub implementations. Judged by main/implementation-integrity.
- Never suppress, bypass, default or work around quality-gate failures, errors, deprecation warnings or test failures. Judged by main/implementation-integrity.
- Never alter, disable, suppress, loosen or conditionally bypass gates or tests, e.g. `--skip` or `--no-verify`. Judged by main/implementation-integrity.
- Never add fallback modes or temporary strategies to meet task requirements. Judged by main/implementation-integrity.
- After a failure, never change or abandon the task without the user's permission. Judged by main/implementation-integrity.
- Add no comments, including docstrings, on added or touched lines of any file type with comments. Enforced by CODE.no-comments (guardrails diff-check).
- Tool directives listed in catalogue `CODE.no-comments` exemptions are not comments. Enforced by CODE.no-comments (guardrails diff-check).
- In multiline directive comments, only directive lines and empty delimiters are exempt. Enforced by CODE.no-comments (guardrails diff-check).
- Never silently swallow exceptions with a `try`/`except`/`pass`. Enforced by RUFF.SELECT.S110 (guardrails toolchain-parity).
- Do not catch overly broad exceptions. Enforced by RUFF.SELECT.BLE001 (guardrails toolchain-parity).
- Use `contextlib.suppress` for intentional suppression, never to hide a failure. Enforced by RUFF.SELECT.SIM105 (guardrails toolchain-parity).
- Where policy is silent, keep existing content, make no irreversible write, ask once when it changes the outcome, and state it. Guidance only.

## Clean cutovers

- Never implement backward compatibility or refactor code to support both new and legacy objectives. Judged by dynaflows/clean-cutovers.
- Remove legacy references, version-suffixed file or module names (`parser-v2.py`) and legacy-only items. Judged by dynaflows/clean-cutovers.
- `<name>/v<N>` folders or files follow [structure and naming](structure-and-naming.md). Guidance only.
- Preserve exact wording and styling when reusing concepts, constraints, labels or user phrases across related edits. Guidance only.
- Never introduce semantically equivalent rewrites of canonical wording, labels, limits or instructions in one project. Guidance only.

## Regression fixes

- Name the broken rule beyond the one exposing input, and repair its root cause, not a special case. Judged by dynaflows/regression-coverage.
- Report whether behavioral coverage protected that rule; select coverage with `skill://test-guidance/references/case-selection.md`. Guidance only.

## Verification scaling

- Scale verification to risk: focused for narrow changes, broader for shared or user-facing behaviour. Guidance only.
- Follow `skill://test-guidance/references/before-commit.md` for the generic verification procedure. Guidance only.
