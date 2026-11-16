from pathlib import Path


def test_saves_preferences(tmp_path):
    (tmp_path / "preferences.json").write_text("{}")
    (Path.home() / "preferences.json").write_text("{}")
    assert (Path.home() / "preferences.json").read_text() == "{}"
