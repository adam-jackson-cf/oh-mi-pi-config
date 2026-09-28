# Task

The retry worker configuration key is being renamed from `maxRetries` to `maxAttempts`, but
externally supplied legacy settings must remain readable. Apply this precise action list.

1. In `settings.py`, rename the public accessor to `get_max_attempts`, set
   `DEFAULT_SETTINGS["maxAttempts"]` to `3`, and change its validation error text to `maxAttempts
   must be a non-negative integer`.
2. `get_max_attempts(settings)` must read `settings["maxAttempts"]` when that key is present;
   otherwise it must read legacy `settings["maxRetries"]` when present; otherwise use the new
   default. The new key always wins if both are supplied. Keep the existing non-negative-int
   validation, including rejection of booleans.
3. In `worker.py`, import and call `get_max_attempts`, and return the retry limit under output key
   `maxAttempts`. Do not merge a legacy key into defaults before the accessor can see it.
4. In `example_config.py`, rename the example key to `maxAttempts` with its value unchanged.
5. In `README.md`, replace the configuration table's `maxRetries` row with `maxAttempts`, keep
   default `3`, and describe it as `Maximum attempts including the initial attempt.`

Acceptance criteria: new settings work, legacy-only settings work, new wins over legacy, invalid
values retain validation, and all visible example/documented/output names use `maxAttempts`.
