from shop.core import Basket


def test_basket():
    assert Basket().total() == 6
