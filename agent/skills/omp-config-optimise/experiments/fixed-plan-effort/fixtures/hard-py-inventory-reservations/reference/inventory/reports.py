from .store import Inventory


def low_stock(inventory: Inventory) -> list[str]:
    """SKUs whose available stock is at or below their reorder level, alphabetical."""
    return [sku for sku in inventory.skus() if inventory.available(sku) <= inventory.get(sku).reorder_level]


def stock_table(inventory: Inventory) -> str:
    rows = ["sku,on_hand,reserved,available"]
    rows += [
        f"{sku},{inventory.get(sku).on_hand},{inventory.reserved(sku)},{inventory.available(sku)}"
        for sku in inventory.skus()
    ]
    return "\n".join(rows)
