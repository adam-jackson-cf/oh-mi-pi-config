from hypothesis import given, seed, strategies

from shop.core import price


@seed(20260101)
@given(strategies.integers(min_value=0, max_value=100))
def test_price_is_never_negative(quantity):
    assert price(quantity) >= 0
