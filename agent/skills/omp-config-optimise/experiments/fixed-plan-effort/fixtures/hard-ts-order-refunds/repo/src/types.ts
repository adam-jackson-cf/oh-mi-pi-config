export type OrderStatus = "pending" | "captured" | "cancelled";

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
