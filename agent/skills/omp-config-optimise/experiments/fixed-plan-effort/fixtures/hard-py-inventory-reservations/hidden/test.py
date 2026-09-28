import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, sys.argv[1])
cases = []


def test(fn):
    cases.append(fn)
    return fn


def expect_error(fn, code, details=None):
    from inventory import InventoryError

    try:
        fn()
    except InventoryError as error:
        assert error.code == code, (error.code, code)
        if details is not None:
            assert error.details == details, (error.details, details)
        return
    raise AssertionError(f"expected {code}")


T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make():
    import inventory

    clock = inventory.FixedClock(T0)
    inv = inventory.Inventory(clock)
    inv.add_sku("mug", 10, reorder_level=3)
    inv.add_sku("cap", 5, reorder_level=1)
    return inventory, inv, clock


@test
def reserve_and_available():
    _, inv, _ = make()
    r = inv.reserve("mug", 4, 60)
    assert (r.id, r.sku, r.quantity, r.expires_at) == ("res-1", "mug", 4, datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc))
    assert (inv.reserved("mug"), inv.available("mug"), inv.get("mug").on_hand) == (4, 6, 10)
    assert inv.reserve("cap", 1, 5).id == "res-2"


@test
def expiry_boundary_exclusive():
    _, inv, clock = make()
    inv.reserve("mug", 4, 60)
    clock.advance(59)
    assert inv.available("mug") == 6
    clock.advance(1)
    assert (inv.reserved("mug"), inv.available("mug")) == (0, 10)


@test
def reserve_validation_order():
    inventory, inv, _ = make()
    expect_error(lambda: inv.reserve("nope", 0, 60), inventory.UNKNOWN_SKU, {"sku": "nope"})
    expect_error(lambda: inv.reserve("mug", 0, 60), inventory.INVALID_QUANTITY, {"quantity": 0})
    expect_error(lambda: inv.reserve("mug", True, 60), inventory.INVALID_QUANTITY)
    expect_error(lambda: inv.reserve("mug", -2, 60), inventory.INVALID_QUANTITY)
    inv.reserve("mug", 7, 60)
    expect_error(lambda: inv.reserve("mug", 4, 60), inventory.INSUFFICIENT_STOCK, {"sku": "mug", "requested": 4, "available": 3})


@test
def release():
    inventory, inv, clock = make()
    r = inv.reserve("mug", 4, 60)
    clock.advance(120)
    inv.release(r.id)
    expect_error(lambda: inv.release(r.id), inventory.UNKNOWN_RESERVATION, {"reservation_id": r.id})
    assert inventory.UNKNOWN_RESERVATION == "unknown_reservation"


@test
def commit_live():
    _, inv, _ = make()
    r = inv.reserve("mug", 4, 60)
    item = inv.commit(r.id)
    assert (item.on_hand, inv.reserved("mug"), inv.available("mug")) == (6, 0, 6)


@test
def commit_expired():
    inventory, inv, clock = make()
    r = inv.reserve("mug", 4, 60)
    clock.advance(60)
    expect_error(
        lambda: inv.commit(r.id),
        inventory.RESERVATION_EXPIRED,
        {"reservation_id": r.id, "expired_at": "2026-01-01T00:01:00+00:00"},
    )
    expect_error(lambda: inv.commit(r.id), inventory.UNKNOWN_RESERVATION)
    assert inv.get("mug").on_hand == 10
    assert inventory.RESERVATION_EXPIRED == "reservation_expired"


@test
def events():
    _, inv, _ = make()
    a = inv.reserve("mug", 2, 60)
    b = inv.reserve("cap", 1, 60)
    inv.release(a.id)
    inv.commit(b.id)
    # The plan does not forbid commit from also emitting the existing stock.adjusted event.
    lifecycle = [e for e in inv.events if e["kind"] != "stock.adjusted"]
    assert lifecycle == [
        {"kind": "stock.reserved", "sku": "mug", "quantity": 2, "reservation_id": "res-1"},
        {"kind": "stock.reserved", "sku": "cap", "quantity": 1, "reservation_id": "res-2"},
        {"kind": "stock.released", "sku": "mug", "quantity": 2, "reservation_id": "res-1"},
        {"kind": "stock.committed", "sku": "cap", "quantity": 1, "reservation_id": "res-2"},
    ], inv.events


@test
def fulfil_respects_reservations():
    inventory, inv, _ = make()
    inv.reserve("mug", 8, 60)
    expect_error(
        lambda: inventory.fulfil(inv, [("cap", 1), ("mug", 3)]),
        inventory.INSUFFICIENT_STOCK,
        {"sku": "mug", "requested": 3, "available": 2},
    )
    assert inv.get("cap").on_hand == 5
    inventory.fulfil(inv, [("mug", 2)])
    assert inv.get("mug").on_hand == 8


@test
def reports_use_available():
    inventory, inv, _ = make()
    inv.reserve("mug", 7, 60)
    assert inventory.low_stock(inv) == ["mug"]
    assert inventory.stock_table(inv) == "sku,on_hand,reserved,available\ncap,5,0,5\nmug,10,7,3"


@test
def exports_kept():
    import inventory

    for name in ["FixedClock", "Inventory", "InventoryError", "StockItem", "fulfil", "low_stock", "stock_table", "Reservation"]:
        assert hasattr(inventory, name), name
    assert inventory.Reservation.__dataclass_params__.frozen


passed = 0
for case in cases:
    try:
        case()
        passed += 1
    except Exception as error:  # noqa: BLE001 - report every failing case
        print(f"FAIL {case.__name__}: {error!r}", file=sys.stderr)
print(json.dumps({"passed": passed, "total": len(cases)}))
sys.exit(0 if passed == len(cases) else 1)
