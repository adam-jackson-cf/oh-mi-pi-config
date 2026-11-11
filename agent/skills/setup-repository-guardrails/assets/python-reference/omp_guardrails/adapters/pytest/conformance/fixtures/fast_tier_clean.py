from shop.core import Basket


def test_total_of_three_items(tmp_path):
    (tmp_path / "receipt.txt").write_text(str(Basket().total()))
    assert (tmp_path / "receipt.txt").read_text() == "6"
