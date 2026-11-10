import pytest

from shop.core import Basket


@pytest.mark.fast
def test_fast_total():
    assert Basket().total() == 6


@pytest.mark.slow
def test_slow_total():
    assert Basket().total() == 6
