# Task

The order service needs partial refunds. Implement this fixed plan. Follow the conventions already
in the codebase wherever a step says so.

1. Model: orders gain a `refunds` list; each refund has `id`, `amountCents`, `reason`, `createdAt`.
   Add the statuses `partially_refunded` and `refunded`. New orders start with no refunds. Orders
   loaded through `Store.importRaw` from older exports have no `refunds` field: `importRaw` must
   keep storing records exactly as given (widen its parameter type to describe them), and every read
   path of the store must present them with an empty list.
2. Errors: add the code `REFUND_EXCEEDS_BALANCE` alongside the existing codes. Invalid amounts and
   wrong order states reuse the existing codes. Raise errors the way the other handlers do.
3. Add `refundOrder(store, orderId, amountCents, reason, now)` in a new `src/handlers/refunds.ts`,
   written like the payment handlers (lookup, validation, never mutating the stored order, save,
   audit). Check in this order: the order exists; the amount is valid (use the existing cents
   validator with field name `amountCents`); the order is `captured` or `partially_refunded`
   (otherwise the same state error and details as capture uses); the amount does not exceed the
   refundable balance (details: `orderId`, `amountCents`, `refundableCents`).
4. The refundable balance is the captured amount minus all earlier refunds. After a refund the
   status is `refunded` when the balance reaches zero, otherwise `partially_refunded`. Refund ids
   come from the store's id generator with prefix `rf`.
5. Audit each refund as `order.refunded`, following the audit conventions, with data `{ amountCents,
   refundId }`.
6. `listOrders` accepts an optional `status` filter, applied the same way as the customer filter.
   Sorting and pagination behaviour are unchanged.
7. `orderSummary` adds a `Refunded: <total refunded>` line after the Captured line, only when the
   order has refunds, formatted like the other amounts.
8. Migrate `revenueCents` in `src/reports.ts`: revenue is net of refunds and counts every order
   whose payment was captured, including partially and fully refunded orders.
9. Export `refundOrder` and the refund type from `src/index.ts` without changing any existing export
   or signature, and update `runDemo` in `src/cli.ts` to refund 500 cents with reason `damaged` (at
   `2026-01-02T09:00:00Z`) after capturing, returning the summary of the refunded order.

Acceptance: all behaviour above holds; existing callers keep working; no other files change.
