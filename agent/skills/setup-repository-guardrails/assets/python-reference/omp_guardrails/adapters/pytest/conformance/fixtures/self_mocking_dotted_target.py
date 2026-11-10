from unittest import mock

from shop.core import Basket


@mock.patch("shop.core.price", return_value=1)
def test_total_uses_patched_price(patched):
    assert Basket().total() == 1
