export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

/** Raw request body as decoded from JSON. */
export interface TaskPayloadInput {
  id?: JsonValue;
  title?: JsonValue;
}

function isNonEmptyString(value: JsonValue | undefined): value is string {
  return value === String(value) && value.length > 0;
}

export function validateTaskPayload(payload: TaskPayloadInput): payload is { id: string; title: string } {
  return isNonEmptyString(payload.id) && isNonEmptyString(payload.title);
}
