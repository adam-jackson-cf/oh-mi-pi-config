from shop.settings import reset_cache


def test_reset_cache_empties_the_cache():
    assert reset_cache() == 0
