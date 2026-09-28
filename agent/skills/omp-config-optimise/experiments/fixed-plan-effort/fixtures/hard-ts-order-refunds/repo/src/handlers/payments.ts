import { record } from "../audit.ts";
import { ErrorCode, fail } from "../errors.ts";
import type { Store } from "../store.ts";
import type { Order } from "../types.ts";
import { getOrder } from "./orders.ts";

// Handlers never mutate a stored order: they save a new object and audit the change.

export function captureOrder(store: Store, id: string, now: string): Order {
  const order = getOrder(store, id);
  if (order.status !== "pending") {
    fail(ErrorCode.INVALID_STATE, `order ${id} is ${order.status}`, { orderId: id, status: order.status });
  }
  const captured: Order = { ...order, status: "captured", capturedCents: order.totalCents };
  store.save(captured);
  record(store, "order.captured", id, now, { amountCents: captured.capturedCents });
  return captured;
}

export function cancelOrder(store: Store, id: string, now: string): Order {
  const order = getOrder(store, id);
  if (order.status !== "pending") {
    fail(ErrorCode.INVALID_STATE, `order ${id} is ${order.status}`, { orderId: id, status: order.status });
  }
  const cancelled: Order = { ...order, status: "cancelled" };
  store.save(cancelled);
  record(store, "order.cancelled", id, now);
  return cancelled;
}
