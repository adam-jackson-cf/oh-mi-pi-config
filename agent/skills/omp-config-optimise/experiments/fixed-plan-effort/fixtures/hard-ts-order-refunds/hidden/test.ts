export {};
const workspace = process.argv[2];
let passed = 0;
const results: string[] = [];
type Case = () => void | Promise<void>;
type Details = Record<string, string | number | boolean | null>;
const cases: Array<[string, Case]> = [];
function test(name: string, fn: Case): void { cases.push([name, fn]); }
function eq<T>(actual: T, expected: T): void {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) throw new Error(`expected ${e}, got ${a}`);
}
function throwsCode(fn: () => void, code: string, details?: Details): void {
  try { fn(); } catch (error) {
    // SAFETY: handlers raise AppError, which carries `code` and `details`; eq() fails on any other shape.
    const err = error as { code?: string; details?: Details };
    eq(err.code, code);
    if (details) eq(err.details, details);
    return;
  }
  throw new Error(`expected ${code}`);
}
// SAFETY: the workspace module is untyped test input; every case below asserts the shape it uses.
const api = (await import(`${workspace}/src/index.ts`).catch(() => ({}))) as any;
const { Store, createOrder, captureOrder, listOrders, getOrder, orderSummary, revenueCents, refundOrder } = api;
const item = (unitCents: number) => [{ sku: "x", quantity: 1, unitCents }];
function captured(store: any, cents = 2000, at = "2026-01-01T00:00:00Z") {
  const order = createOrder(store, "cus", item(cents), at);
  return captureOrder(store, order.id, at);
}

test("partial refund", () => {
  const store = new Store(); const order = captured(store);
  const out = refundOrder(store, order.id, 500, "damaged", "2026-01-02T00:00:00Z");
  eq([out.status, out.refunds], ["partially_refunded", [{ id: "rf_1", amountCents: 500, reason: "damaged", createdAt: "2026-01-02T00:00:00Z" }]]);
  eq(getOrder(store, order.id).status, "partially_refunded");
});
test("stored order not mutated", () => {
  const store = new Store(); const order = captured(store);
  const before = getOrder(store, order.id);
  refundOrder(store, order.id, 500, "r", "t");
  eq([before.status, before.refunds.length], ["captured", 0]);
});
test("full refund across two refunds", () => {
  const store = new Store(); const order = captured(store);
  refundOrder(store, order.id, 500, "a", "t1");
  const out = refundOrder(store, order.id, 1500, "b", "t2");
  eq([out.status, out.refunds.map((r: any) => r.id)], ["refunded", ["rf_1", "rf_2"]]);
});
test("exceeds balance", () => {
  const store = new Store(); const order = captured(store);
  refundOrder(store, order.id, 500, "a", "t1");
  throwsCode(() => refundOrder(store, order.id, 1501, "b", "t2"), "REFUND_EXCEEDS_BALANCE", { orderId: order.id, amountCents: 1501, refundableCents: 1500 });
});
test("pending order state error", () => {
  const store = new Store(); const order = createOrder(store, "cus", item(900), "t");
  throwsCode(() => refundOrder(store, order.id, 100, "r", "t"), "INVALID_STATE", { orderId: order.id, status: "pending" });
});
test("fully refunded state error", () => {
  const store = new Store(); const order = captured(store, 800);
  refundOrder(store, order.id, 800, "r", "t");
  throwsCode(() => refundOrder(store, order.id, 1, "r", "t"), "INVALID_STATE", { orderId: order.id, status: "refunded" });
});
test("invalid amount", () => {
  const store = new Store(); const order = captured(store);
  throwsCode(() => refundOrder(store, order.id, 1.5, "r", "t"), "INVALID_AMOUNT", { field: "amountCents", value: 1.5 });
  throwsCode(() => refundOrder(store, order.id, 0, "r", "t"), "INVALID_AMOUNT");
});
test("validation order", () => {
  const store = new Store(); const pending = createOrder(store, "cus", item(900), "t");
  throwsCode(() => refundOrder(store, "ord_404", 0, "r", "t"), "ORDER_NOT_FOUND");
  throwsCode(() => refundOrder(store, pending.id, -5, "r", "t"), "INVALID_AMOUNT");
});
test("audit event", () => {
  const store = new Store(); const order = captured(store);
  refundOrder(store, order.id, 300, "r", "2026-03-01T00:00:00Z");
  eq(store.audit.at(-1), { type: "order.refunded", orderId: order.id, at: "2026-03-01T00:00:00Z", data: { amountCents: 300, refundId: "rf_1" } });
});
test("legacy records normalised on read", () => {
  const store = new Store();
  const legacy = { id: "ord_legacy", customerId: "old", items: item(1000), status: "captured", totalCents: 1000, capturedCents: 1000, createdAt: "2025-01-01T00:00:00Z" };
  store.importRaw([legacy]);
  eq(getOrder(store, "ord_legacy").refunds, []);
  eq(listOrders(store).items[0].refunds, []);
  eq(revenueCents(store), 1000);
  eq(refundOrder(store, "ord_legacy", 1000, "r", "t").status, "refunded");
  eq("refunds" in legacy, false);
});
test("status filter before pagination", () => {
  const store = new Store();
  const a = captured(store, 100, "2026-01-05T00:00:00Z");
  createOrder(store, "cus", item(100), "2026-01-04T00:00:00Z");
  createOrder(store, "cus", item(100), "2026-01-03T00:00:00Z");
  const b = captured(store, 100, "2026-01-02T00:00:00Z");
  const c = captured(store, 100, "2026-01-01T00:00:00Z");
  const first = listOrders(store, { status: "captured", limit: 2 });
  eq([first.items.map((o: any) => o.id), first.nextCursor], [[a.id, b.id], b.id]);
  const second = listOrders(store, { status: "captured", limit: 2, cursor: first.nextCursor });
  eq([second.items.map((o: any) => o.id), second.nextCursor], [[c.id], null]);
});
test("summary lines", () => {
  const store = new Store(); const order = captured(store);
  eq(orderSummary(getOrder(store, order.id)), `Order ${order.id} (captured)\nTotal: $20.00\nCaptured: $20.00`);
  refundOrder(store, order.id, 250, "a", "t"); refundOrder(store, order.id, 250, "b", "t");
  eq(orderSummary(getOrder(store, order.id)), `Order ${order.id} (partially_refunded)\nTotal: $20.00\nCaptured: $20.00\nRefunded: $5.00`);
});
test("net revenue", () => {
  const store = new Store();
  captured(store, 2000);
  const partial = captured(store, 2000); refundOrder(store, partial.id, 500, "r", "t");
  const full = captured(store, 700); refundOrder(store, full.id, 700, "r", "t");
  createOrder(store, "cus", item(9999), "t");
  eq(revenueCents(store), 3500);
});
test("demo migrated", async () => {
  const { runDemo } = await import(`${workspace}/src/cli.ts`);
  eq(runDemo(), "Order ord_1 (partially_refunded)\nTotal: $25.00\nCaptured: $25.00\nRefunded: $5.00");
});

for (const [name, fn] of cases) {
  try { await fn(); passed += 1; } catch (error) { results.push(`FAIL ${name}: ${String(error)}`); }
}
for (const line of results) console.error(line);
console.log(JSON.stringify({ passed, total: cases.length }));
process.exit(passed === cases.length ? 0 : 1);
