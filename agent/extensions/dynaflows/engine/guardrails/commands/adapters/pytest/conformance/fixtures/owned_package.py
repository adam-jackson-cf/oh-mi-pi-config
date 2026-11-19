RATE = 2


def price(quantity: int) -> int:
    return quantity * RATE


class Basket:
    def total(self) -> int:
        return price(3)
