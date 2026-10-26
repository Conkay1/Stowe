import pytest


@pytest.mark.parametrize("mime", [
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
    "application/pdf", "image/svg+xml", "text/html", "application/octet-stream",
    None, "", "image/png; charset=utf-8",
])
def test_preview_preserves_bytes_and_only_inlines_allowed_types(client, mime):
    from backend import db
    from backend.models import Receipt
    from backend.routers import expenses

    expense = client.post("/api/v1/expenses", json={
        "merchant": "Synthetic Clinic", "date": "2026-01-15", "amount": 42.5,
        "notes": "Keep this exact financial record",
    }).json()
    content = b"%PDF-1.4 synthetic receipt exact bytes\n%%EOF\n"
    path = expenses.RECEIPTS_DIR / "synthetic-original.pdf"
    path.write_bytes(content)
    with db.SessionLocal() as session:
        receipt = Receipt(
            expense_id=expense["id"], filename=path.name,
            original_filename='Synthetic <image "name">.pdf', file_type=mime,
            file_size_bytes=len(content),
        )
        session.add(receipt)
        session.commit()
        rid = receipt.id
    before = client.get(f"/api/v1/expenses/{expense['id']}").json()
    for query, inline in [("", False), ("?preview=false", False), ("?preview=true", mime in expenses.ALLOWED_RECEIPT_MIMES)]:
        response = client.get(f"/api/v1/receipts/{rid}/file{query}")
        assert response.status_code == 200
        assert response.content == content
        assert response.headers["content-disposition"].startswith("inline;" if inline else "attachment;")
        assert response.headers["x-content-type-options"] == "nosniff"
        expected_type = "text/html; charset=utf-8" if mime == "text/html" else (mime or "application/octet-stream")
        assert response.headers["content-type"] == expected_type
    assert client.get(f"/api/v1/expenses/{expense['id']}").json() == before
    assert path.read_bytes() == content


def test_preview_missing_receipt_is_404(client):
    assert client.get("/api/v1/receipts/987654/file?preview=true").status_code == 404
