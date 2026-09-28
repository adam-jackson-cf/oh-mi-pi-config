import type { Config } from "./config.ts";
import type { Metrics } from "./metrics.ts";
import type { Queue } from "./queue.ts";
import { attempt } from "./sender.ts";
import type { Transport } from "./types.ts";

/** Processes up to `batchSize` due deliveries, oldest `nextAttemptAt` first, ties by id. Returns processed ids. */
export async function runOnce(queue: Queue, transport: Transport, config: Config, metrics: Metrics, now: number): Promise<string[]> {
  const due = queue
    .all()
    .filter(delivery => (delivery.status === "pending" || delivery.status === "retrying") && delivery.nextAttemptAt <= now)
    .sort((a, b) => a.nextAttemptAt - b.nextAttemptAt || a.id.localeCompare(b.id))
    .slice(0, config.batchSize);
  for (const delivery of due) queue.save(await attempt(delivery, transport, config, metrics, now));
  return due.map(delivery => delivery.id);
}
