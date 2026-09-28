# Task

The request-tools package needs a deterministic in-memory fixed-window limiter for callers that
already import from `src/index.ts`. Implement only the following fixed plan.

1. Create `src/rate-limit.ts` exporting `RateLimitResult` with `allowed: boolean`, `remaining:
   number`, and `retryAfterMs: number`, plus `FixedWindowRateLimiter` with `constructor(limit:
   number, windowMs: number)`, `attempt(key: string, nowMs: number): RateLimitResult`, and
   `reset(key?: string): void`.
2. In the constructor, throw `RangeError` unless both `limit` and `windowMs` are positive integers.
   In `attempt`, throw `TypeError` when `key` is the empty string.
3. Treat a window as `Math.floor(nowMs / windowMs) * windowMs`; keep counts separately per key and
   replace a key's count whenever its stored window start differs from this computed start
   (including a timestamp moving to an earlier window).
4. For the first `limit` attempts by a key in one window, return `allowed: true`, decrement
   `remaining` from `limit - 1` to `0`, and return `retryAfterMs: 0`. Further attempts return
   `allowed: false`, `remaining: 0`, and `retryAfterMs: windowStart + windowMs - nowMs`; an attempt
   exactly at a boundary is therefore in the new window.
5. Make `reset(key)` delete only that key and make `reset()` clear every key. Export both new
   symbols from `src/index.ts` without changing `slugify`.

Acceptance criteria: imports from `src/index.ts` expose the limiter; all validation, per-key
isolation, boundary, denial, backward-time, and reset behaviours above are exact.
