from shop import core


def test_total_with_patched_price(monkeypatch):
    monkeypatch.setattr(core, "price", lambda quantity: 0)
    assert core.Basket().total() == 0
