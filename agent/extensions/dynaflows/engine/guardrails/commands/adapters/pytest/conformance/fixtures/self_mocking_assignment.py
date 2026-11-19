import shop.core


def test_total_with_rebound_rate():
    shop.core.RATE = 0
    assert shop.core.Basket().total() == 0
