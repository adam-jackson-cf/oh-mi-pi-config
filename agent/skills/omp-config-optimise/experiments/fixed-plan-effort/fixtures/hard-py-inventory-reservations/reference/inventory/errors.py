"""Error codes are lowercase snake_case strings; details are keyword arguments."""

UNKNOWN_SKU = "unknown_sku"
INVALID_QUANTITY = "invalid_quantity"
INSUFFICIENT_STOCK = "insufficient_stock"
UNKNOWN_RESERVATION = "unknown_reservation"
RESERVATION_EXPIRED = "reservation_expired"


class InventoryError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, object]) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def raise_error(code: str, message: str, **details: object) -> None:
    raise InventoryError(code, message, details)
