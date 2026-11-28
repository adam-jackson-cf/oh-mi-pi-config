from shop.settings import reset_cache


def warm() -> int:
    return reset_cache()
