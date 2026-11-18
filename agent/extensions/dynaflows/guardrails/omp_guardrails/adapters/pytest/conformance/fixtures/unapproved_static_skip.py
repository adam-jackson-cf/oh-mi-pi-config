import pytest

from shop.core import Basket


@pytest.mark.skip(reason="vendor outage")
def test_total_of_three_items():
    assert Basket().total() == 6
