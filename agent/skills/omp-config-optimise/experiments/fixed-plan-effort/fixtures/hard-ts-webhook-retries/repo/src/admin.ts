import type { Queue } from "./queue.ts";
import type { DeliveryStatus } from "./types.ts";

/** Count of deliveries per status, every status present, keys in declaration order. */
export function statusCounts(queue: Queue) {
  const counts = { pending: 0, delivered: 0, failed: 0 } satisfies Record<DeliveryStatus, number>;
  for (const delivery of queue.all()) counts[delivery.status] += 1;
  return counts;
}
