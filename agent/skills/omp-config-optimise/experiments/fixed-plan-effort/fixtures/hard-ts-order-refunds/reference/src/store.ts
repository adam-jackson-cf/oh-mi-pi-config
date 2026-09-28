import type { AuditEvent, Order } from "./types.ts";

/** An order as stored: records exported before refunds existed have no `refunds` field. */
export type StoredOrder = Omit<Order, "refunds"> & { refunds?: Order["refunds"] };

export class Store {
  #orders = new Map<string, StoredOrder>();
  #counters = new Map<string, number>();
  readonly audit: AuditEvent[] = [];

  /** Ids are `<prefix>_<n>` with an independent counter per prefix. */
  nextId(prefix: string): string {
    const next = (this.#counters.get(prefix) ?? 0) + 1;
    this.#counters.set(prefix, next);
    return `${prefix}_${next}`;
  }

  get(id: string): Order | undefined {
    const order = this.#orders.get(id);
    return order && withRefunds(order);
  }

  all(): Order[] {
    return [...this.#orders.values()].map(withRefunds);
  }

  save(order: Order): void {
    this.#orders.set(order.id, order);
  }

  /** Loads records exported by older versions exactly as they were written. */
  importRaw(records: readonly StoredOrder[]): void {
    for (const order of records) this.#orders.set(order.id, order);
  }
}

function withRefunds(order: StoredOrder): Order {
  return { ...order, refunds: order.refunds ?? [] };
}
