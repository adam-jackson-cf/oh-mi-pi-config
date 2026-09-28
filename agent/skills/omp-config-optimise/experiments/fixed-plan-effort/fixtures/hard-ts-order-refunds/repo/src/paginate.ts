import type { Page } from "./types.ts";

/** Cursor is the id of the last item returned. Callers pass items already filtered and sorted. */
export function paginate<T extends { id: string }>(items: readonly T[], limit: number, cursor?: string | null): Page<T> {
  let start = 0;
  if (cursor) {
    const index = items.findIndex(item => item.id === cursor);
    start = index === -1 ? items.length : index + 1;
  }
  const slice = items.slice(start, start + limit);
  const more = start + limit < items.length;
  return { items: slice, nextCursor: more && slice.length > 0 ? slice[slice.length - 1]!.id : null };
}
