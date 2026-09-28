import type { Delivery, JsonValue } from "./types.ts";

export class Queue {
  #deliveries = new Map<string, Delivery>();
  #next = 0;

  enqueue(url: string, payload: JsonValue, now: number): Delivery {
    this.#next += 1;
    const delivery: Delivery = { id: `dlv_${this.#next}`, url, payload, attempts: 0, status: "pending", nextAttemptAt: now };
    this.#deliveries.set(delivery.id, delivery);
    return delivery;
  }

  get(id: string): Delivery | undefined {
    return this.#deliveries.get(id);
  }

  all(): Delivery[] {
    return [...this.#deliveries.values()];
  }

  save(delivery: Delivery): void {
    this.#deliveries.set(delivery.id, delivery);
  }
}
