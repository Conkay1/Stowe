import pytest


def test_support_history_cannot_replace_existing_expense_hints(client, monkeypatch):
    from backend import ocr

    texts = {
        b"%PDF-synthetic-receipt": (
            "Synthetic Clinic\nOffice visit\nTotal $25.00\n2026-01-15"
        ),
        b"%PDF-synthetic-history": (
            "Synthetic Clinic\nPrescription history\nGrand total $5000.00\n"
            "Exported 2026-07-01"
        ),
    }
    monkeypatch.setattr(ocr, "extract_text", lambda path: (texts[path.read_bytes()], "pdf-text"))
    created = client.post("/api/v1/expenses", json={
        "merchant": "Synthetic Clinic", "date": "2026-01-15",
        "amount": 25.00, "category": "Medical", "notes": "Verified transaction",
    })
    assert created.status_code == 201
    expense_id = created.json()["id"]
    pull = client.post("/api/v1/reimbursements", json={
        "date": "2026-02-01", "total_amount": 15.00,
        "line_items": [{"expense_id": expense_id, "covered_amount": 15.00}],
    })
    assert pull.status_code == 201
    pull_before = pull.json()
    before = client.get(f"/api/v1/expenses/{expense_id}").json()
    receipts = []
    for filename, content in zip(("receipt.pdf", "history.pdf"), texts):
        uploaded = client.post(f"/api/v1/expenses/{expense_id}/receipts", files={
            "file": (filename, content, "application/pdf"),
        })
        assert uploaded.status_code == 201
        receipts.append(uploaded.json())

    for _ in range(2):
        analyzed = client.post(f"/api/v1/expenses/{expense_id}/analyze")
        assert analyzed.status_code == 200
        after = analyzed.json()
        assert (after["auto_review"]["amount"], after["auto_review"]["date"]) == (None, None)
        assert after["auto_review_status"] == "needs_review"
        assert after["auto_review"]["confidence"] == "low"
        assert "saved expense" in after["auto_review_notes"]
        assert "amount" in after["auto_review_notes"]
        assert "date" in after["auto_review_notes"]
        for field, value in before.items():
            if field not in {"receipts", "auto_review", "auto_review_status", "auto_review_notes"}:
                assert after[field] == value
        assert after["receipts"] == receipts
        assert client.get(f"/api/v1/reimbursements/{pull_before['id']}").json() == {
            **pull_before,
            "line_items": [{**pull_before["line_items"][0], "receipt_count": 2}],
        }
        for receipt, content in zip(receipts, texts):
            downloaded = client.get(f"/api/v1/receipts/{receipt['id']}/file")
            assert downloaded.status_code == 200
            assert downloaded.content == content


@pytest.mark.parametrize(("text", "amount", "when", "status"), [
    ("Total $25.00\n2026-01-15", 25.00, "2026-01-15", "eligible"),
    ("Total $5000.00\n2026-01-15", None, "2026-01-15", "needs_review"),
    ("Total $25.00\n2026-07-01", 25.00, None, "needs_review"),
    ("No transaction fields", None, None, "eligible"),
])
def test_existing_expense_review_suppresses_only_conflicting_hints(
    client, monkeypatch, text, amount, when, status
):
    from backend import ocr

    monkeypatch.setattr(ocr, "extract_text", lambda path: (
        "Synthetic Clinic\nOffice visit\n" + text, "pdf-text"
    ))
    expense = client.post("/api/v1/expenses", json={
        "merchant": "Synthetic Clinic", "date": "2026-01-15", "amount": 25.00,
    }).json()
    response = client.post(f"/api/v1/expenses/{expense['id']}/receipts", files={
        "file": ("receipt.pdf", b"%PDF-synthetic", "application/pdf"),
    })
    assert response.status_code == 201

    reviewed = client.get(f"/api/v1/expenses/{expense['id']}").json()

    assert reviewed["auto_review_status"] == status
    assert reviewed["auto_review"]["amount"] == amount
    assert reviewed["auto_review"]["date"] == when
    assert reviewed["amount"] == 25.00
    assert reviewed["date"] == "2026-01-15"
    if status == "needs_review":
        assert reviewed["auto_review_notes"].startswith("Review suggested:")
        assert "conflicting hints were omitted" in reviewed["auto_review_notes"]
        assert "IRS Publication 502" in reviewed["auto_review_notes"]


@pytest.mark.parametrize(("saved", "extracted", "expected"), [
    (25.004, 25.00, 25.00),
    (25.005, 25.00, None),
    (2.675, 2.68, 2.68),
    (2.685, 2.68, None),
])
def test_existing_expense_amount_comparison_uses_decimal_cents(
    client, monkeypatch, saved, extracted, expected
):
    from backend import db as db_module, ocr
    from backend.models import HSAExpense

    monkeypatch.setattr(ocr, "extract_text", lambda _: (
        f"Synthetic Clinic\nOffice visit\nTotal ${extracted:.2f}\n2026-01-15",
        "pdf-text",
    ))
    expense = client.post("/api/v1/expenses", json={
        "merchant": "Synthetic Clinic", "date": "2026-01-15", "amount": saved,
    }).json()
    with db_module.SessionLocal() as db:
        db.get(HSAExpense, expense["id"]).amount = saved
        db.commit()
    before = client.get(f"/api/v1/expenses/{expense['id']}").json()
    response = client.post(f"/api/v1/expenses/{expense['id']}/receipts", files={
        "file": ("receipt.pdf", b"%PDF-synthetic", "application/pdf"),
    })
    assert response.status_code == 201
    reviewed = client.get(f"/api/v1/expenses/{expense['id']}").json()
    assert reviewed["amount"] == before["amount"]
    with db_module.SessionLocal() as db:
        assert db.get(HSAExpense, expense["id"]).amount == saved
    assert reviewed["auto_review"]["amount"] == expected
    assert reviewed["auto_review"]["date"] == "2026-01-15"
    assert reviewed["auto_review_status"] == (
        "needs_review" if expected is None else "eligible"
    )


def test_preview_keeps_unbound_hints_without_changing_existing_expense(
    client, monkeypatch
):
    from backend import ocr

    monkeypatch.setattr(ocr, "extract_text", lambda _: (
        "Synthetic Clinic\nOffice visit\nGrand total $5000.00\nExported 2026-07-01",
        "pdf-text",
    ))
    expense = client.post("/api/v1/expenses", json={
        "merchant": "Synthetic Clinic", "date": "2026-01-15", "amount": 25.00,
    }).json()
    before = client.get("/api/v1/expenses").json()
    response = client.post("/api/v1/receipts/analyze", files={
        "file": ("history.pdf", b"%PDF-synthetic-history", "application/pdf"),
    })
    assert response.status_code == 200
    preview = response.json()
    assert preview["amount"] == 5000.00
    assert preview["date"] == "2026-07-01"
    assert preview["status"] == "eligible"
    assert client.get("/api/v1/expenses").json() == before
    assert client.get(f"/api/v1/expenses/{expense['id']}").json()["receipts"] == []
