from shop.core import Basket


def test_basket_totals_three_units():
    # behaviour: TBD
    assert Basket().total() == 6
