import type { Config } from "./config.ts";
import type { Metrics } from "./metrics.ts";
import type { Delivery, Transport } from "./types.ts";

/** Attempts one send and returns the updated delivery; never mutates its input. */
export async function attempt(delivery: Delivery, transport: Transport, config: Config, metrics: Metrics, now: number): Promise<Delivery> {
  const attempts = delivery.attempts + 1;
  try {
    const response = await transport(delivery.url, delivery.payload, config.timeoutMs);
    if (response.status >= 200 && response.status < 300) {
      metrics.increment("webhook.delivered");
      return { ...delivery, attempts, status: "delivered" };
    }
  } catch {
    // network failure: fall through to failure handling
  }
  metrics.increment("webhook.failed");
  return { ...delivery, attempts, status: "failed", nextAttemptAt: now };
}
