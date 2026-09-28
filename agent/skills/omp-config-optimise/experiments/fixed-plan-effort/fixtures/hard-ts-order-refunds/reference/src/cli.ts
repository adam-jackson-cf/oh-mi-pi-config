import { captureOrder, createOrder, orderSummary, refundOrder, Store } from "./index.ts";

export function runDemo(): string {
  const store = new Store();
  const order = createOrder(store, "cus_demo", [{ sku: "mug", quantity: 2, unitCents: 1250 }], "2026-01-01T10:00:00Z");
  captureOrder(store, order.id, "2026-01-01T10:05:00Z");
  const refunded = refundOrder(store, order.id, 500, "damaged", "2026-01-02T09:00:00Z");
  return orderSummary(refunded);
}

if (import.meta.main) console.log(runDemo());
