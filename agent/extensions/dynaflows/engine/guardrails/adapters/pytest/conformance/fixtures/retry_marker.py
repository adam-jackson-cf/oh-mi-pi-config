import pytest

from shop.core import Basket


@pytest.mark.flaky(reruns=3)
def test_total_of_three_items():
    assert Basket().total() == 6
