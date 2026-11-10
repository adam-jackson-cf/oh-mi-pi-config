import os
from unittest import mock

from shop.core import Basket


def test_total_ignores_working_directory(monkeypatch):
    monkeypatch.setattr(os, "getcwd", lambda: "/nowhere")
    with mock.patch("time.time", return_value=0):
        assert Basket().total() == 6
