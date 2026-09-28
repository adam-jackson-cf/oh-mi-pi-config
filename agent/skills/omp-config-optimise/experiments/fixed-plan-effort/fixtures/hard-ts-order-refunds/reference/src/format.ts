import { formatCents, sumCents } from "./money.ts";
import type { Order } from "./types.ts";

export function orderSummary(order: Order): string {
  const lines = [`Order ${order.id} (${order.status})`, `Total: ${formatCents(order.totalCents)}`];
  if (order.capturedCents > 0) lines.push(`Captured: ${formatCents(order.capturedCents)}`);
  if (order.refunds.length > 0) {
    lines.push(`Refunded: ${formatCents(sumCents(order.refunds.map(refund => refund.amountCents)))}`);
  }
  return lines.join("\n");
}
