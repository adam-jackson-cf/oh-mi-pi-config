from .errors import INSUFFICIENT_STOCK, raise_error
from .store import Inventory, _check_quantity


def fulfil(inventory: Inventory, lines: list[tuple[str, int]]) -> None:
    """Ship every line or none: validate all lines before changing stock."""
    for sku, quantity in lines:
        _check_quantity(quantity)
        available = inventory.available(sku)
        if quantity > available:
            raise_error(INSUFFICIENT_STOCK, f"not enough {sku}", sku=sku, requested=quantity, available=available)
    for sku, quantity in lines:
        inventory.adjust(sku, -quantity)
