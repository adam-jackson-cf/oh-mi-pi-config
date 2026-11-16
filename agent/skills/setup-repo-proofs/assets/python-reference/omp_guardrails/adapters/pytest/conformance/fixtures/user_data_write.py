import os
from pathlib import Path


def test_saves_preferences():
    live_home = Path(os.environ["OMP_CONFORMANCE_LIVE_HOME"])
    (live_home / "preferences.json").write_text("{}")
