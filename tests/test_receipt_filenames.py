import pytest


@pytest.fixture(params=["upload", "analyze"])
def receipt_endpoint(client, request):
    response = client.post("/api/v1/expenses", json={
        "merchant": "Test Pharmacy",
        "date": "2026-01-15",
        "amount": 42.50,
        "category": "Pharmacy",
    })
    assert response.status_code == 201
    expense_id = response.json()["id"]
    if request.param == "upload":
        return f"/api/v1/expenses/{expense_id}/receipts", expense_id, 201
    return "/api/v1/receipts/analyze", expense_id, 200


@pytest.fixture
def ocr_reads(monkeypatch):
    from backend import ocr

    reads = []

    def extract_text(path):
        reads.append((path, path.read_bytes()))
        return "", "none"

    monkeypatch.setattr(ocr, "extract_text", extract_text)
    return reads


@pytest.mark.parametrize("filename", [
    "receipt.pdf",
    "x" * 196 + ".pdf",
    "x" * 197 + ".pdf",
    "x" * 300 + ".PDF",
    "領収書" * 100 + ".pdf",
    "../../" + "x" * 300 + ".pdf",
], ids=["short", "boundary", "over-boundary", "long-uppercase", "unicode", "path-like"])
def test_receipt_name_preserves_extension_and_bytes(
    client, receipt_endpoint, ocr_reads, filename
):
    from backend.routers import expenses

    endpoint, expense_id, expected_status = receipt_endpoint
    content = b"%PDF-1.4 synthetic receipt\n%%EOF\n"
    response = client.post(
        endpoint, files={"file": (filename, content, "application/pdf")}
    )
    assert response.status_code == expected_status, response.text
    assert len(ocr_reads) == 1
    path, read_content = ocr_reads[0]
    assert path.suffix == ".pdf"
    assert read_content == content
    expense = client.get(f"/api/v1/expenses/{expense_id}").json()
    if expected_status == 201:
        receipt = response.json()
        assert receipt["original_filename"] == filename
        assert receipt["file_size_bytes"] == len(content)
        assert path.parent == expenses.RECEIPTS_DIR
        assert len(path.stem) == 32
        assert all(char in "0123456789abcdef" for char in path.stem)
        assert list(expenses.RECEIPTS_DIR.iterdir()) == [path]
        download = client.get(f"/api/v1/receipts/{receipt['id']}/file")
        assert download.status_code == 200
        assert download.content == content
        assert [r["id"] for r in expense["receipts"]] == [receipt["id"]]
    else:
        assert not path.exists()
        assert not list(expenses.RECEIPTS_DIR.iterdir())
        assert expense["receipts"] == []


@pytest.mark.parametrize("filename", [
    "receipt.exe",
    "x" * 196 + ".pdf.exe",
    "receipt.pdf/unsupported",
], ids=["unsupported", "hidden-suffix", "directory-suffix"])
def test_receipt_rejects_unsupported_final_extension(
    client, receipt_endpoint, ocr_reads, filename
):
    from backend.routers import expenses

    endpoint, expense_id, _ = receipt_endpoint
    response = client.post(
        endpoint, files={"file": (filename, b"synthetic", "application/pdf")}
    )
    assert response.status_code == 400, response.text
    assert "Unsupported file type" in response.json()["detail"]
    assert ocr_reads == []
    assert not list(expenses.RECEIPTS_DIR.iterdir())
    assert client.get(f"/api/v1/expenses/{expense_id}").json()["receipts"] == []
