from dataclasses import replace

from .clock import SystemClock
from .errors import INVALID_QUANTITY, UNKNOWN_SKU, raise_error
from .events import emit
from .models import StockItem


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

    def available(self, sku: str) -> int:
        return self.get(sku).on_hand
