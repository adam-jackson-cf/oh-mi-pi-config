export const ErrorCode = {
  ORDER_NOT_FOUND: "ORDER_NOT_FOUND",
  INVALID_AMOUNT: "INVALID_AMOUNT",
  INVALID_STATE: "INVALID_STATE",
  EMPTY_ORDER: "EMPTY_ORDER",
  REFUND_EXCEEDS_BALANCE: "REFUND_EXCEEDS_BALANCE",
} as const;
export type ErrorCode = (typeof ErrorCode)[keyof typeof ErrorCode];

/** Error details carry only ids and scalar values. */
export type DetailValue = string | number | boolean | null;

export class AppError extends Error {
  constructor(
    readonly code: ErrorCode,
    message: string,
    readonly details: Record<string, DetailValue> = {},
  ) {
    super(message);
    this.name = "AppError";
  }
}

/** Every handler raises through here so codes and details stay uniform. */
export function fail(code: ErrorCode, message: string, details: Record<string, DetailValue> = {}): never {
  throw new AppError(code, message, details);
}
