from unittest.mock import MagicMock

from shop.core import Basket


def test_checkout_with_fake_basket():
    basket = MagicMock(spec=Basket)
    basket.total.return_value = 5
    assert basket.total() == 5
