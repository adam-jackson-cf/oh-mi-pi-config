import pytest

from shop.core import Basket, price


@pytest.mark.parametrize("quantity, expected", [(1, 2), (3, 6)])
def test_price_scales_with_quantity(quantity, expected):
    assert price(quantity) == expected


def test_basket_totals_three_units():
    assert Basket().total() == 6
