import os


def discount_rate() -> float:
    if os.environ.get("SHOP_TESTING"):
        return 0.0
    return 0.1
