# Task

The task API must carry an `archived` state end-to-end while preserving compatibility with payloads
made before this field existed. Implement this fixed six-step plan.

1. In `src/model.ts`, add `archived: boolean` to `Task` and add optional `archived?: boolean` to
   `TaskInput`.
2. In `src/validate.ts`, add an optional `archived` to `TaskPayloadInput`; keep requiring non-empty
   string `id` and `title`; accept a missing `archived` property, but when it is present accept only
   a boolean. Update the type predicate to describe `archived?: boolean`.
3. In `src/handler.ts`, keep the same `createTask(input: TaskInput): Task` signature and validation
   error. Return `archived: input.archived ?? false`, so omitted legacy inputs normalize to `false`
   and an explicit `true` or `false` is retained.
4. In `src/serialize.ts`, include `archived` in the JSON object after `title`, producing the key
   order `id`, `title`, `archived`.
5. Migrate the existing caller in `src/demo.ts` to pass `archived: false` explicitly; it must
   continue to create the same unarchived sample task.
6. Keep `src/index.ts` exports unchanged and do not add any other defaults or coercions.

Acceptance criteria: valid legacy payloads create and serialize with `archived: false`; explicit
booleans round-trip; invalid non-boolean archived values are rejected; existing caller is migrated;
serialized key order is exact.
