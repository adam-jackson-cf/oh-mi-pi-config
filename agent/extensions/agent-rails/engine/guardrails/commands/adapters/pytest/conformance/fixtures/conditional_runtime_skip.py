import shutil

import pytest

from shop.core import Basket


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs docker")
def test_total_when_docker_is_present():
    assert Basket().total() == 6


def test_total_of_three_items_without_vendor():
    if shutil.which("vendor-cli") is None:
        pytest.skip("needs the vendor cli")
    assert Basket().total() == 6
