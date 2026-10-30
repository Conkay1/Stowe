"""Theme preference persists on disk (the packaged webview's localStorage does not)."""
import json

import pytest

from backend.routers import system


@pytest.fixture
def settings_path(tmp_path, monkeypatch):
    path = tmp_path / "data" / "settings.json"
    monkeypatch.setattr(system, "SETTINGS_PATH", path)
    return path


def test_default_theme_is_dark(client, settings_path):
    assert client.get("/api/v1/system/theme").json() == {"theme": "dark"}
    assert not settings_path.exists()


def test_theme_survives_restart(client, settings_path):
    assert client.put("/api/v1/system/theme", json={"theme": "sepia"}).json() == {"theme": "sepia"}
    assert json.loads(settings_path.read_text())["theme"] == "sepia"
    # A fresh read goes to disk, as after an app restart.
    assert client.get("/api/v1/system/theme").json() == {"theme": "sepia"}


def test_invalid_theme_rejected(client, settings_path):
    assert client.put("/api/v1/system/theme", json={"theme": "neon"}).status_code == 422
    assert not settings_path.exists()


def test_corrupt_or_unknown_settings_fall_back(client, settings_path):
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{not json")
    assert client.get("/api/v1/system/theme").json() == {"theme": "dark"}
    settings_path.write_text(json.dumps({"theme": "neon"}))
    assert client.get("/api/v1/system/theme").json() == {"theme": "dark"}


def test_other_settings_preserved(client, settings_path):
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"other": 1}))
    client.put("/api/v1/system/theme", json={"theme": "light"})
    assert json.loads(settings_path.read_text()) == {"other": 1, "theme": "light"}
