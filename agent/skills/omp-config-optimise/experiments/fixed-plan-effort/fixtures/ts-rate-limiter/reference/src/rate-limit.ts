export interface RateLimitResult {
  allowed: boolean;
  remaining: number;
  retryAfterMs: number;
}

interface WindowCount {
  windowStart: number;
  count: number;
}

export class FixedWindowRateLimiter {
  private readonly counts = new Map<string, WindowCount>();

  constructor(
    private readonly limit: number,
    private readonly windowMs: number,
  ) {
    if (!Number.isInteger(limit) || limit <= 0) {
      throw new RangeError("limit must be a positive integer");
    }
    if (!Number.isInteger(windowMs) || windowMs <= 0) {
      throw new RangeError("windowMs must be a positive integer");
    }
  }

  attempt(key: string, nowMs: number): RateLimitResult {
    if (key.length === 0) {
      throw new TypeError("key must not be empty");
    }

    const windowStart = Math.floor(nowMs / this.windowMs) * this.windowMs;
    const previous = this.counts.get(key);
    const entry = previous?.windowStart === windowStart
      ? previous
      : { windowStart, count: 0 };

    if (entry.count < this.limit) {
      entry.count += 1;
      this.counts.set(key, entry);
      return { allowed: true, remaining: this.limit - entry.count, retryAfterMs: 0 };
    }

    return {
      allowed: false,
      remaining: 0,
      retryAfterMs: windowStart + this.windowMs - nowMs,
    };
  }

  reset(key?: string): void {
    if (key === undefined) {
      this.counts.clear();
      return;
    }
    this.counts.delete(key);
  }
}
