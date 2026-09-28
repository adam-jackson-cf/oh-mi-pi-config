from dataclasses import dataclass


@dataclass(frozen=True)
class StockItem:
    sku: str
    on_hand: int
    reorder_level: int
