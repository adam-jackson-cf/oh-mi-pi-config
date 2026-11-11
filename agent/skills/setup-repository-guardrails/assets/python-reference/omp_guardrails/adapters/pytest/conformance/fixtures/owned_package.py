"""The conformance project's owned module, copied to ``shop/core.py``."""

RATE = 2


def price(quantity: int) -> int:
    return quantity * RATE


class Basket:
    def total(self) -> int:
        return price(3)
