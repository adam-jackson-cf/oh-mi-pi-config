from dataclasses import replace
from datetime import timedelta

from .clock import SystemClock
from .errors import (
    INSUFFICIENT_STOCK,
    INVALID_QUANTITY,
    RESERVATION_EXPIRED,
    UNKNOWN_RESERVATION,
    UNKNOWN_SKU,
    raise_error,
)
from .events import emit
from .models import Reservation, StockItem


def _check_quantity(quantity: object, *, allow_negative: bool = False) -> int:
    valid = isinstance(quantity, int) and not isinstance(quantity, bool) and quantity != 0
    if valid and not allow_negative and quantity < 0:
        valid = False
    if not valid:
        raise_error(INVALID_QUANTITY, "quantity must be a non-zero integer", quantity=quantity)
    return quantity  # type: ignore[return-value]


class Inventory:
    def __init__(self, clock=None) -> None:
        self.clock = clock or SystemClock()
        self.events: list[dict[str, object]] = []
        self._items: dict[str, StockItem] = {}
        self._reservations: dict[str, Reservation] = {}
        self._next_reservation = 0

    def add_sku(self, sku: str, on_hand: int, reorder_level: int = 0) -> StockItem:
        item = StockItem(sku, on_hand, reorder_level)
        self._items[sku] = item
        return item

    def get(self, sku: str) -> StockItem:
        item = self._items.get(sku)
        if item is None:
            raise_error(UNKNOWN_SKU, f"unknown sku {sku}", sku=sku)
        return item  # type: ignore[return-value]

    def skus(self) -> list[str]:
        return sorted(self._items)

    def adjust(self, sku: str, delta: int) -> StockItem:
        item = self.get(sku)
        _check_quantity(delta, allow_negative=True)
        updated = replace(item, on_hand=item.on_hand + delta)
        self._items[sku] = updated
        emit(self.events, "stock.adjusted", sku=sku, delta=delta)
        return updated

    def reserved(self, sku: str) -> int:
        self.get(sku)
        now = self.clock.now()
        return sum(r.quantity for r in self._reservations.values() if r.sku == sku and now < r.expires_at)

    def available(self, sku: str) -> int:
        return self.get(sku).on_hand - self.reserved(sku)

    def reserve(self, sku: str, quantity: int, ttl_seconds: float) -> Reservation:
        self.get(sku)
        _check_quantity(quantity)
        available = self.available(sku)
        if quantity > available:
            raise_error(INSUFFICIENT_STOCK, f"not enough {sku}", sku=sku, requested=quantity, available=available)
        self._next_reservation += 1
        reservation = Reservation(
            f"res-{self._next_reservation}", sku, quantity, self.clock.now() + timedelta(seconds=ttl_seconds)
        )
        self._reservations[reservation.id] = reservation
        emit(self.events, "stock.reserved", sku=sku, quantity=quantity, reservation_id=reservation.id)
        return reservation

    def _take(self, reservation_id: str) -> Reservation:
        reservation = self._reservations.pop(reservation_id, None)
        if reservation is None:
            raise_error(UNKNOWN_RESERVATION, f"unknown reservation {reservation_id}", reservation_id=reservation_id)
        return reservation  # type: ignore[return-value]

    def release(self, reservation_id: str) -> None:
        reservation = self._take(reservation_id)
        emit(self.events, "stock.released", sku=reservation.sku, quantity=reservation.quantity, reservation_id=reservation_id)

    def commit(self, reservation_id: str) -> StockItem:
        reservation = self._take(reservation_id)
        if self.clock.now() >= reservation.expires_at:
            raise_error(
                RESERVATION_EXPIRED,
                f"reservation {reservation_id} expired",
                reservation_id=reservation_id,
                expired_at=reservation.expires_at.isoformat(),
            )
        item = self.get(reservation.sku)
        updated = replace(item, on_hand=item.on_hand - reservation.quantity)
        self._items[reservation.sku] = updated
        emit(self.events, "stock.committed", sku=reservation.sku, quantity=reservation.quantity, reservation_id=reservation_id)
        return updated
