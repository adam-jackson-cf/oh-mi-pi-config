import { getPreferredIndex, type QueueEntry } from "./priority";

export function dispatchOrder(entries: readonly QueueEntry[]): string[] {
  const preferred = getPreferredIndex(entries);
  if (preferred === undefined) {
    return entries.map((entry) => entry.id);
  }
  return [entries[preferred].id, ...entries.filter((_, index) => index !== preferred).map((entry) => entry.id)];
}
