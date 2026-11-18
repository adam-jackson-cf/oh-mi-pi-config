# Delivery principles

Read before the first implementation edit; apply clean cutovers to refactors and the regression rule to bug fixes. Tags distinguish deterministic enforcement, agent judgment and guidance; a judgment tag does not claim a calibrated blocking checker.

## Implementation integrity

- Never add Python placeholder function bodies (`pass`, `...` or `raise NotImplementedError` as the whole body); Protocol methods, abstract methods, `@overload` and `.pyi` declarations are exempt. Non-Python stubs remain an agent judgment, not a claimed deterministic check. Enforced by CODE.no-placeholder-bodies (omp-guardrails diff-check).
- Never add mock-library imports or mock calls to product code outside profile test, support and fixture paths. Enforced by CODE.no-product-mocks (omp-guardrails diff-check).
- Outside tests, never use hardcoded values or stub implementations. Judged by main/implementation-integrity.
- Never suppress, bypass, default or work around quality-gate failures, errors, deprecation warnings or test failures. Judged by main/implementation-integrity.
- Never alter, disable, suppress, loosen or conditionally bypass quality gates or tests, including via `--skip` or `--no-verify`. Judged by main/implementation-integrity.
- Never add fallback modes or temporary strategies to meet task requirements. Judged by main/implementation-integrity.
- After a failure, never change or abandon the task without the user's permission. Judged by main/implementation-integrity.
- Do not add comments of any kind, including docstrings, in any file type with comment syntax; scope is added diff lines, including touched lines carrying a comment. Tool directives are exempt: shebangs, `noqa`, `type: ignore`, `pyright:`, `fmt: off/on`, `ruff:`, `isort:`, `pylint:`, `eslint-*`, `@ts-expect-error`, `@ts-ignore`, `@ts-nocheck`, `prettier-ignore`, `biome-ignore`, `//go:build`, `//nolint`, `SPDX-License-Identifier`, `markdownlint-*`, vim/emacs modelines and `coding: utf-8` cookies (`#[allow]` is not a comment). Enforced by CODE.no-comments (omp-guardrails diff-check).
- In multiline directive comments, only directive lines and empty delimiters are exempt; added prose lines still block. Enforced by CODE.no-comments (omp-guardrails diff-check).
- Never silently swallow exceptions with a `try`/`except`/`pass`. Enforced by RUFF.SELECT.S110 (omp-guardrails toolchain-parity).
- Do not catch overly broad exceptions. Enforced by RUFF.SELECT.BLE001 (omp-guardrails toolchain-parity).
- Use `contextlib.suppress` rather than a `try`/`except`/`pass` for intentional suppression; this is not permission to hide a failure. Enforced by RUFF.SELECT.SIM105 (omp-guardrails toolchain-parity).
- Where policy is silent, use judgment: keep existing content, make no irreversible write, ask the user once when the choice changes the outcome, and state the choice in the result. Guidance only.

## Clean cutovers

- Never implement backward compatibility or refactor code to support both new and legacy objectives. Judged by dynaflows/clean-cutovers.
- Remove references to legacy functionality, version suffixes in file or module names such as `parser-v2.py`, and items that exist only for legacy behavior; `<name>/v<N>` folders or files follow [structure and naming](structure-and-naming.md). Judged by dynaflows/clean-cutovers.
- Preserve exact wording and styling when reusing concepts, constraints, labels, or user-specified phrases across related edits. Guidance only.
- Never introduce semantically equivalent rewrites of canonical wording, labels, limits, or instructions within the same project. Guidance only.
- Deprecated skills in this policy migration stay on disk untouched until a final owner-signed-off cleanup PR; migrate every caller now, rather than keeping compatibility callers. Guidance only.

## Regression fixes

- Before fixing, name the failing behaviour: the rule the code broke, stated beyond the one input that exposed it; repair the root cause, not a special case of that input. Judged by dynaflows/regression-coverage.
- Check and report whether behavioral coverage protected that rule, and select coverage using `skill://test-guidance/references/case-selection.md`; test-design rules live there rather than here. Guidance only.

## Verification scaling

- Verify every implementation task with coverage scaled to risk and blast radius: focused coverage for narrow changes, broader coverage for shared behaviour, cross-module contracts or user-facing workflows; use `skill://test-guidance/references/before-commit.md` for the generic verification procedure. Guidance only.
