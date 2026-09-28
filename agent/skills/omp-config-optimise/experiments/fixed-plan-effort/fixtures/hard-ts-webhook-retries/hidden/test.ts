export {};
const workspace = process.argv[2];
type Case = () => void | Promise<void>;
const cases: Array<[string, Case]> = [];
function test(name: string, fn: Case): void { cases.push([name, fn]); }
function eq<T>(actual: T, expected: T): void {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) throw new Error(`expected ${e}, got ${a}`);
}
// SAFETY: the workspace module is untyped test input; every case below asserts the shape it uses.
const api = (await import(`${workspace}/src/index.ts`).catch(() => ({}))) as any;
const { loadConfig, DEFAULTS, ConfigError, Metrics, Queue, attempt, runOnce, statusCounts } = api;
const cfg = (over: Record<string, number> = {}) => ({ ...loadConfig({}), ...over });
const status = (code: number) => async () => ({ status: code });
const boom = async () => { throw new Error("socket hang up"); };

test("config defaults and env", () => {
  eq([DEFAULTS.maxAttempts, DEFAULTS.baseDelayMs, DEFAULTS.maxDelayMs], [5, 1000, 60000]);
  const c = loadConfig({ WEBHOOK_MAX_ATTEMPTS: "3", WEBHOOK_BASE_DELAY_MS: "250", WEBHOOK_MAX_DELAY_MS: "", WEBHOOK_TIMEOUT_MS: "10" });
  eq([c.maxAttempts, c.baseDelayMs, c.maxDelayMs, c.timeoutMs, c.batchSize], [3, 250, 60000, 10, 10]);
});
test("config validation", () => {
  try { loadConfig({ WEBHOOK_MAX_ATTEMPTS: "0" }); } catch (e: any) { eq([e instanceof ConfigError, e.variable], [true, "WEBHOOK_MAX_ATTEMPTS"]); return; }
  throw new Error("expected ConfigError");
});
test("new delivery lastError null", () => {
  const q = new Queue(); eq(q.enqueue("u", {}, 0).lastError, null);
});
test("5xx schedules retry with backoff", async () => {
  const q = new Queue(); const m = new Metrics(); const d = q.enqueue("u", {}, 0);
  const r1 = await attempt(d, status(503), cfg(), m, 10_000);
  eq([r1.status, r1.attempts, r1.nextAttemptAt, r1.lastError], ["retrying", 1, 11_000, "HTTP 503"]);
  const r2 = await attempt(r1, status(500), cfg(), m, 20_000);
  eq([r2.status, r2.attempts, r2.nextAttemptAt], ["retrying", 2, 22_000]);
  const r3 = await attempt(r2, boom, cfg(), m, 30_000);
  eq([r3.status, r3.nextAttemptAt, r3.lastError], ["retrying", 34_000, "socket hang up"]);
  eq([d.status, d.attempts], ["pending", 0]);
});
test("delay capped", async () => {
  const m = new Metrics(); const d = { ...new Queue().enqueue("u", {}, 0), attempts: 6 };
  const r = await attempt(d, status(502), cfg({ maxAttempts: 10, baseDelayMs: 1000, maxDelayMs: 30_000 }), m, 0);
  eq([r.status, r.nextAttemptAt], ["retrying", 30_000]);
});
test("408 and 429 retry, other 4xx permanent", async () => {
  const m = new Metrics(); const d = new Queue().enqueue("u", {}, 0);
  eq((await attempt(d, status(408), cfg(), m, 0)).status, "retrying");
  eq((await attempt(d, status(429), cfg(), m, 0)).status, "retrying");
  const p = await attempt(d, status(404), cfg(), m, 5);
  eq([p.status, p.attempts, p.lastError, p.nextAttemptAt], ["failed", 1, "HTTP 404", 5]);
});
test("exhausted attempts fail", async () => {
  const m = new Metrics(); const d = { ...new Queue().enqueue("u", {}, 0), attempts: 2, status: "retrying" };
  const r = await attempt(d, status(500), cfg({ maxAttempts: 3 }), m, 7);
  eq([r.status, r.attempts, r.lastError], ["failed", 3, "HTTP 500"]);
});
test("success clears lastError", async () => {
  const m = new Metrics(); const d = { ...new Queue().enqueue("u", {}, 0), attempts: 1, status: "retrying", lastError: "HTTP 500" };
  const r = await attempt(d, status(204), cfg(), m, 0);
  eq([r.status, r.attempts, r.lastError], ["delivered", 2, null]);
});
test("metrics", async () => {
  const m = new Metrics(); const d = new Queue().enqueue("u", {}, 0);
  const r1 = await attempt(d, status(500), cfg({ maxAttempts: 2 }), m, 0);
  await attempt(r1, status(500), cfg({ maxAttempts: 2 }), m, 0);
  await attempt(d, status(200), cfg(), m, 0);
  eq(m.counters, { "webhook.retried": 1, "webhook.failed": 1, "webhook.delivered": 1 });
});
test("worker picks due retrying deliveries in order", async () => {
  const q = new Queue(); const m = new Metrics();
  const a = q.enqueue("a", {}, 0); const b = q.enqueue("b", {}, 0); q.enqueue("c", {}, 50_000);
  const calls: string[] = [];
  const flaky = async (url: string) => { calls.push(url); return { status: url === "a" ? 500 : 200 }; };
  eq(await runOnce(q, flaky, cfg(), m, 0), [a.id, b.id]);
  eq(await runOnce(q, flaky, cfg(), m, 999), []);
  eq(await runOnce(q, flaky, cfg(), m, 1_000), [a.id]);
  eq(q.get(a.id).nextAttemptAt, 3_000);
  eq(calls, ["a", "b", "a"]);
});
test("status counts", async () => {
  const q = new Queue(); const m = new Metrics();
  const a = q.enqueue("a", {}, 0); q.enqueue("b", {}, 0);
  q.save(await attempt(a, status(500), cfg(), m, 0));
  eq(JSON.stringify(statusCounts(q)), JSON.stringify({ pending: 1, retrying: 1, delivered: 0, failed: 0 }));
});

let passed = 0;
for (const [name, fn] of cases) {
  try { await fn(); passed += 1; } catch (error) { console.error(`FAIL ${name}: ${String(error)}`); }
}
console.log(JSON.stringify({ passed, total: cases.length }));
process.exit(passed === cases.length ? 0 : 1);
