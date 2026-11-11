import pytest

from shop.core import Basket, price


# behaviour: a price is the quantity times the unit rate
@pytest.mark.parametrize("quantity, expected", [(1, 2), (3, 6)])
def test_price_scales_with_quantity(quantity, expected):
    assert price(quantity) == expected


def test_basket_totals_three_units():
    # behaviour: a basket totals three units at the unit rate
    assert Basket().total() == 6
