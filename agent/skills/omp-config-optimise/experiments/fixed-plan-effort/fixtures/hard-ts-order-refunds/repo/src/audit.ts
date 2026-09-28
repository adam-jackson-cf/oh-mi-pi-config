import type { Store } from "./store.ts";

/** Event types are `order.<past-tense verb>`; `data` carries only amounts and ids. */
export function record(store: Store, type: string, orderId: string, at: string, data: Record<string, string | number> = {}): void {
  store.audit.push({ type, orderId, at, data });
}
