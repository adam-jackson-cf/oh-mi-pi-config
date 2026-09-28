import { ErrorCode, fail } from "./errors.ts";

/** Validates a strictly positive integer amount of cents; `field` names the input in errors. */
export function assertCents(value: number, field: string): number {
  if (!Number.isInteger(value) || value <= 0) {
    fail(ErrorCode.INVALID_AMOUNT, `${field} must be a positive integer number of cents`, { field, value });
  }
  return value;
}

export function formatCents(cents: number): string {
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  return `${sign}$${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
}

export function sumCents(values: readonly number[]): number {
  return values.reduce((total, value) => total + value, 0);
}
