import { sumCents } from "./money.ts";
import type { Store } from "./store.ts";

/** Revenue actually collected across all orders. */
export function revenueCents(store: Store): number {
  return sumCents(store.all().filter(order => order.status === "captured").map(order => order.capturedCents));
}
