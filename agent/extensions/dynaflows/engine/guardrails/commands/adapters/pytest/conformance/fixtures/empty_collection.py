from shop.core import Basket


def test_total_of_three_items():
    assert Basket().total() == 6
