export {};

let passed = 0;
const total = 11;
function check(condition: boolean): void { if (condition) passed += 1; }
function same<T>(actual: T, expected: T): boolean { return JSON.stringify(actual) === JSON.stringify(expected); }

try {
  const workspace = process.argv[2];
  const { FixedWindowRateLimiter } = await import(`${workspace}/src/index.ts`);
  try { new FixedWindowRateLimiter(0, 10); } catch (error) { check(error instanceof RangeError); }
  try { new FixedWindowRateLimiter(1, 1.5); } catch (error) { check(error instanceof RangeError); }
  const limiter = new FixedWindowRateLimiter(2, 100);
  try { limiter.attempt("", 0); } catch (error) { check(error instanceof TypeError); }
  check(same(limiter.attempt("a", 10), { allowed: true, remaining: 1, retryAfterMs: 0 }));
  check(same(limiter.attempt("a", 99), { allowed: true, remaining: 0, retryAfterMs: 0 }));
  check(same(limiter.attempt("a", 99), { allowed: false, remaining: 0, retryAfterMs: 1 }));
  check(same(limiter.attempt("b", 99), { allowed: true, remaining: 1, retryAfterMs: 0 }));
  check(same(limiter.attempt("a", 100), { allowed: true, remaining: 1, retryAfterMs: 0 }));
  check(same(limiter.attempt("a", 10), { allowed: true, remaining: 1, retryAfterMs: 0 }));
  limiter.reset("a"); check(same(limiter.attempt("a", 10), { allowed: true, remaining: 1, retryAfterMs: 0 }));
  limiter.attempt("b", 10); limiter.reset(); check(same(limiter.attempt("b", 10), { allowed: true, remaining: 1, retryAfterMs: 0 }));
} catch {
  // A starting workspace may not yet contain the requested public module.
}
console.log(JSON.stringify({ passed, total }));
process.exit(passed === total ? 0 : 1);
