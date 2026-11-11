import subprocess
import sys

import pytest

from shop.core import Basket


@pytest.fixture
def basket():
    yield Basket()
    subprocess.run([sys.executable, "-c", "pass"], check=False, timeout=10)


def test_total_of_three_items(basket):
    assert basket.total() == 6
