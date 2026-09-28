export type DeliveryStatus = "pending" | "retrying" | "delivered" | "failed";

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

export interface Delivery {
  id: string;
  url: string;
  payload: JsonValue;
  attempts: number;
  status: DeliveryStatus;
  nextAttemptAt: number;
  lastError: string | null;
}

export interface TransportResponse {
  status: number;
}

/** Sends one HTTP request; throws on network errors and timeouts. */
export type Transport = (url: string, payload: JsonValue, timeoutMs: number) => Promise<TransportResponse>;
