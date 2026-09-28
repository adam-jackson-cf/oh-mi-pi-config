import { record } from "../audit.ts";
import { ErrorCode, fail } from "../errors.ts";
import { assertCents, sumCents } from "../money.ts";
import type { Store } from "../store.ts";
import type { Order } from "../types.ts";
import { getOrder } from "./orders.ts";

export function refundOrder(store: Store, id: string, amountCents: number, reason: string, now: string): Order {
  const order = getOrder(store, id);
  assertCents(amountCents, "amountCents");
  if (order.status !== "captured" && order.status !== "partially_refunded") {
    fail(ErrorCode.INVALID_STATE, `order ${id} is ${order.status}`, { orderId: id, status: order.status });
  }
  const refundableCents = order.capturedCents - sumCents(order.refunds.map(refund => refund.amountCents));
  if (amountCents > refundableCents) {
    fail(ErrorCode.REFUND_EXCEEDS_BALANCE, `refund of ${amountCents} exceeds refundable ${refundableCents}`, {
      orderId: id,
      amountCents,
      refundableCents,
    });
  }
  const refund = { id: store.nextId("rf"), amountCents, reason, createdAt: now };
  const refunded: Order = {
    ...order,
    status: amountCents === refundableCents ? "refunded" : "partially_refunded",
    refunds: [...order.refunds, refund],
  };
  store.save(refunded);
  record(store, "order.refunded", id, now, { amountCents, refundId: refund.id });
  return refunded;
}
