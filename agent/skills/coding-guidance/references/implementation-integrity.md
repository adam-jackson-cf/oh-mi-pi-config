# Implementation integrity

No placeholders, fake fallbacks or quality-gate bypasses.

- Outside tests: never use placeholders, mocks, hardcoded values or stub implementations.
- Never suppress, bypass, default or work around quality-gate failures, errors, deprecation warnings or test failures.
- Never alter, disable, suppress, loosen or conditionally bypass quality gates or tests, including via `--skip` or `--no-verify`.
- Never add fallback modes or temporary strategies to meet task requirements.
- After a failure, never change or abandon the task without the user's permission.
