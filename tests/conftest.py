import pytest

import waechter


@pytest.fixture(autouse=True)
def _keine_echten_dateien(monkeypatch, tmp_path):
    """Tests dürfen nie state.json oder verlauf.csv des echten Wächters verändern."""
    monkeypatch.setattr(waechter, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(waechter, "LOG_FILE", tmp_path / "verlauf.csv")
    monkeypatch.setattr(waechter, "WISH_FILE", tmp_path / "wunschliste.txt")
