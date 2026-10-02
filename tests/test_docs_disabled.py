"""Interactive API docs must not be served (they load third-party CDN scripts)."""


def test_api_docs_and_redoc_return_404(client):
    assert client.get("/api/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
