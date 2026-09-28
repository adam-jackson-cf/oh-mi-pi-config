# Task

The inventory service needs time-limited stock reservations for carts. Implement this fixed plan,
following the package's existing conventions wherever a step refers to them.

1. Add a frozen `Reservation` model with `id`, `sku`, `quantity` and `expires_at` (a datetime), next
   to `StockItem`.
2. Add the error codes `unknown_reservation` and `reservation_expired`, named and raised like the
   existing codes.
3. Add `Inventory.reserve(sku, quantity, ttl_seconds)` returning the reservation. Validate like the
   existing stock operations, in this order: the SKU exists, the quantity is valid (a positive
   integer, same error and details as the other quantity checks), and there is enough available
   stock (same error and detail keys as `fulfil` uses). Ids are `res-1`, `res-2`, … per inventory.
   `expires_at` is the inventory clock's current time plus `ttl_seconds`.
4. A reservation holds stock from creation until its `expires_at`; at or after `expires_at` it no
   longer counts. Add `Inventory.reserved(sku)` (quantity held by live reservations) and make
   `Inventory.available(sku)` return on-hand stock minus that.
5. Add `Inventory.release(reservation_id)`, which drops the reservation whether or not it has
   expired. An unknown id raises `unknown_reservation` with detail `reservation_id`.
6. Add `Inventory.commit(reservation_id)`, which turns a live reservation into a stock decrease and
   returns the updated `StockItem`. An unknown id raises `unknown_reservation`; an expired
   reservation is dropped and raises `reservation_expired` with details `reservation_id` and
   `expired_at` (ISO 8601 string).
7. Emit events for reserve, release and commit following the existing event conventions, each with
   fields `sku`, `quantity`, `reservation_id`.
8. Migrate callers so that reservations are respected everywhere stock is checked or reported:
   `fulfil` must check against available stock (and report it in its error), `low_stock` must use
   available stock, and `stock_table` gains `reserved` and `available` columns after `on_hand`.
9. Export the new model and error codes from the package without removing any existing export.

Acceptance: all behaviour above holds; existing callers keep working; no other files change.
