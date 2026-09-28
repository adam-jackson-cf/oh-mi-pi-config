import type { AuditEvent, Order } from "./types.ts";

export class Store {
  #orders = new Map<string, Order>();
  #counters = new Map<string, number>();
  readonly audit: AuditEvent[] = [];

  /** Ids are `<prefix>_<n>` with an independent counter per prefix. */
  nextId(prefix: string): string {
    const next = (this.#counters.get(prefix) ?? 0) + 1;
    this.#counters.set(prefix, next);
    return `${prefix}_${next}`;
  }

  get(id: string): Order | undefined {
    return this.#orders.get(id);
  }

  all(): Order[] {
    return [...this.#orders.values()];
  }

  save(order: Order): void {
    this.#orders.set(order.id, order);
  }

  /** Loads records exported by older versions exactly as they were written. */
  importRaw(records: readonly Order[]): void {
    for (const order of records) this.#orders.set(order.id, order);
  }
}
