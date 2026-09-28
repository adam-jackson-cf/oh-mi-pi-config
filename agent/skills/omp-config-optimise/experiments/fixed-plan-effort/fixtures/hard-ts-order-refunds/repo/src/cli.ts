import { captureOrder, createOrder, orderSummary, Store } from "./index.ts";

export function runDemo(): string {
  const store = new Store();
  const order = createOrder(store, "cus_demo", [{ sku: "mug", quantity: 2, unitCents: 1250 }], "2026-01-01T10:00:00Z");
  const captured = captureOrder(store, order.id, "2026-01-01T10:05:00Z");
  return orderSummary(captured);
}

if (import.meta.main) console.log(runDemo());
