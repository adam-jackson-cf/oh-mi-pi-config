export type OrderStatus = "pending" | "captured" | "cancelled" | "partially_refunded" | "refunded";

export interface Refund {
  id: string;
  amountCents: number;
  reason: string;
  createdAt: string;
}

export interface LineItem {
  sku: string;
  quantity: number;
  unitCents: number;
}

export interface Order {
  id: string;
  customerId: string;
  items: LineItem[];
  status: OrderStatus;
  totalCents: number;
  capturedCents: number;
  createdAt: string;
  refunds: Refund[];
}

export interface AuditEvent {
  type: string;
  orderId: string;
  at: string;
  data: Record<string, string | number>;
}

export interface Page<T> {
  items: T[];
  nextCursor: string | null;
}
