from .store import Inventory


def low_stock(inventory: Inventory) -> list[str]:
    """SKUs at or below their reorder level, alphabetical."""
    return [sku for sku in inventory.skus() if inventory.get(sku).on_hand <= inventory.get(sku).reorder_level]


def stock_table(inventory: Inventory) -> str:
    rows = ["sku,on_hand"]
    rows += [f"{sku},{inventory.get(sku).on_hand}" for sku in inventory.skus()]
    return "\n".join(rows)
