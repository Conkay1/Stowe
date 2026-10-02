"""Reject non-loopback Host headers so DNS rebinding cannot read the API."""
import io
import zipfile

import pytest


def test_production_allowlist_excludes_testclient():
    import main

    assert main._allowed_hosts(include_testclient=False) == ["127.0.0.1", "localhost"]
    assert "testserver" not in main._allowed_hosts(include_testclient=False)


def test_testclient_host_allowed_only_under_pytest():
    """TestClient's default host is testserver; permit it only in tests."""
    import main

    assert main._allowed_hosts(include_testclient=True) == [
        "127.0.0.1",
        "localhost",
        "testserver",
    ]
    # The live app was imported by pytest, so the default TestClient host works.
    assert "testserver" in main._allowed_hosts()


def test_default_testclient_host_can_export_zip(client):
    res = client.get("/api/v1/export/zip")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/zip")


def test_evil_host_rejected_on_export_zip(client):
    res = client.get("/api/v1/export/zip", headers={"Host": "evil.example.com"})
    assert res.status_code == 400
    assert res.text == "Invalid host header"
    assert not res.content.startswith(b"PK")


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.0.0.1:8000",
        "localhost",
        "localhost:8020",
    ],
)
def test_loopback_host_can_export_zip(client, host):
    res = client.get("/api/v1/export/zip", headers={"Host": host})
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        assert zf.testzip() is None
