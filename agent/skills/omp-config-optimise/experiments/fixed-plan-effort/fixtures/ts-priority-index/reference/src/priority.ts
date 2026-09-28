export interface QueueEntry {
  id: string;
  priority: "urgent" | "normal";
}

/** Return the first urgent entry position when one exists. */
export function getPreferredIndex(entries: readonly QueueEntry[]): number | undefined {
  const index = entries.findIndex((entry) => entry.priority === "urgent");
  return index === -1 ? undefined : index;
}
