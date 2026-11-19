import os


def discount_rate() -> float:
    return float(os.environ.get("SHOP_DISCOUNT_RATE", "0.1"))
