import type { Config } from "./config.ts";
import type { Metrics } from "./metrics.ts";
import type { Delivery, Transport } from "./types.ts";

/** 408 and 429 are transient even though they are 4xx. */
function isRetryableStatus(status: number): boolean {
  return status >= 500 || status === 408 || status === 429;
}

/** Attempts one send and returns the updated delivery; never mutates its input. */
export async function attempt(delivery: Delivery, transport: Transport, config: Config, metrics: Metrics, now: number): Promise<Delivery> {
  const attempts = delivery.attempts + 1;
  let lastError: string;
  let retryable: boolean;
  try {
    const response = await transport(delivery.url, delivery.payload, config.timeoutMs);
    if (response.status >= 200 && response.status < 300) {
      metrics.increment("webhook.delivered");
      return { ...delivery, attempts, status: "delivered", lastError: null };
    }
    lastError = `HTTP ${response.status}`;
    retryable = isRetryableStatus(response.status);
  } catch (error) {
    lastError = error instanceof Error ? error.message : String(error);
    retryable = true;
  }
  if (retryable && attempts < config.maxAttempts) {
    metrics.increment("webhook.retried");
    const delay = Math.min(config.baseDelayMs * 2 ** (attempts - 1), config.maxDelayMs);
    return { ...delivery, attempts, status: "retrying", nextAttemptAt: now + delay, lastError };
  }
  metrics.increment("webhook.failed");
  return { ...delivery, attempts, status: "failed", nextAttemptAt: now, lastError };
}
