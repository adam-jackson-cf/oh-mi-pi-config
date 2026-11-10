import pytest


@pytest.mark.xfail(reason="known defect", strict=True)
def test_rounding():
    assert round(2.675, 2) == 2.68
