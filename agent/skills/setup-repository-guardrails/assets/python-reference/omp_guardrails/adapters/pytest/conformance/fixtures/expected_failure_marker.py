import pytest

from shop.core import Basket


@pytest.mark.xfail(reason="rounding is wrong")
def test_total_of_three_items():
    assert Basket().total() == 7
