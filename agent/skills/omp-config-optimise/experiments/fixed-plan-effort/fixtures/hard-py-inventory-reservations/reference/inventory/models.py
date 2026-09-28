from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class StockItem:
    sku: str
    on_hand: int
    reorder_level: int


@dataclass(frozen=True)
class Reservation:
    id: str
    sku: str
    quantity: int
    expires_at: datetime
