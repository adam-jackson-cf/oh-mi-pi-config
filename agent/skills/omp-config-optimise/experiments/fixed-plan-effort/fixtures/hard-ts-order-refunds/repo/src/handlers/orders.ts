import { record } from "../audit.ts";
import { ErrorCode, fail } from "../errors.ts";
import { assertCents, sumCents } from "../money.ts";
import { paginate } from "../paginate.ts";
import type { Store } from "../store.ts";
import type { LineItem, Order, Page } from "../types.ts";

export function createOrder(store: Store, customerId: string, items: LineItem[], now: string): Order {
  if (items.length === 0) fail(ErrorCode.EMPTY_ORDER, "order needs at least one item", { customerId });
  for (const item of items) assertCents(item.unitCents, "unitCents");
  const order: Order = {
    id: store.nextId("ord"),
    customerId,
    items,
    status: "pending",
    totalCents: sumCents(items.map(item => item.unitCents * item.quantity)),
    capturedCents: 0,
    createdAt: now,
  };
  store.save(order);
  record(store, "order.created", order.id, now, { totalCents: order.totalCents });
  return order;
}

export function getOrder(store: Store, id: string): Order {
  return store.get(id) ?? fail(ErrorCode.ORDER_NOT_FOUND, `order ${id} not found`, { orderId: id });
}

export interface ListOptions {
  customerId?: string;
  limit?: number;
  cursor?: string | null;
}

/** Newest first; ties broken by id ascending. */
export function listOrders(store: Store, options: ListOptions = {}): Page<Order> {
  let orders = store.all();
  if (options.customerId) orders = orders.filter(order => order.customerId === options.customerId);
  orders.sort((a, b) => b.createdAt.localeCompare(a.createdAt) || a.id.localeCompare(b.id));
  return paginate(orders, options.limit ?? 20, options.cursor);
}
