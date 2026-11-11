from shop.core import Basket


def test_basket_totals_three_units():
    # three units
    assert Basket().total() == 6
