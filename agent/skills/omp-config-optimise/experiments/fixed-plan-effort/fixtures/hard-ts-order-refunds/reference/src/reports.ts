import { sumCents } from "./money.ts";
import type { Store } from "./store.ts";

const COLLECTED = new Set(["captured", "partially_refunded", "refunded"]);

/** Revenue actually collected across all orders, net of refunds. */
export function revenueCents(store: Store): number {
  return sumCents(
    store
      .all()
      .filter(order => COLLECTED.has(order.status))
      .map(order => order.capturedCents - sumCents(order.refunds.map(refund => refund.amountCents))),
  );
}
