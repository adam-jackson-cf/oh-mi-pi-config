import { formatCents } from "./money.ts";
import type { Order } from "./types.ts";

export function orderSummary(order: Order): string {
  const lines = [`Order ${order.id} (${order.status})`, `Total: ${formatCents(order.totalCents)}`];
  if (order.capturedCents > 0) lines.push(`Captured: ${formatCents(order.capturedCents)}`);
  return lines.join("\n");
}
