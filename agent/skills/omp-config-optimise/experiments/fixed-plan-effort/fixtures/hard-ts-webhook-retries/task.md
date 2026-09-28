# Task

Webhook deliveries currently fail permanently on the first error. Add retries with exponential
backoff. Implement this fixed plan, following the existing conventions wherever a step refers to
them.

1. Config: add `maxAttempts` (env `WEBHOOK_MAX_ATTEMPTS`, default 5), `baseDelayMs` (env
   `WEBHOOK_BASE_DELAY_MS`, default 1000) and `maxDelayMs` (env `WEBHOOK_MAX_DELAY_MS`, default
   60000), declared, defaulted, parsed and validated exactly like the existing settings.
2. Deliveries gain a `retrying` status (between `pending` and `delivered` in the status type) and a
   `lastError` field that is `null` until an attempt fails. New deliveries start with `lastError:
   null`.
3. Classify each attempt in `attempt()`: a 2xx response is delivered (and clears `lastError`); a
   network error or a 5xx, 408 or 429 response is retryable; any other response is a permanent
   failure. `lastError` is `HTTP <status>` for responses and the error's message for thrown errors.
4. A retryable failure schedules another attempt when attempts so far are fewer than `maxAttempts`:
   status `retrying`, `nextAttemptAt` = now + `baseDelayMs` × 2^(attempts − 1), capped at
   `maxDelayMs`, where attempts includes the one that just failed. Otherwise, and for permanent
   failures, the delivery is `failed` as today.
5. Count scheduled retries in a new metric that follows the existing counter naming
   (`webhook.retried`); only final failures count as `webhook.failed`.
6. The worker must pick up retrying deliveries that are due, with its ordering and batch limit
   unchanged.
7. Migrate `statusCounts` in `src/admin.ts` so it keeps reporting every status, in the declared
   status order.
8. Keep `attempt()` free of mutation, and keep every existing export and signature.

Acceptance: all behaviour above holds; existing callers keep working; no other files change.
