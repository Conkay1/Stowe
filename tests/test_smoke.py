"""Smoke tests covering the critical user flows."""
import io


def _make_expense(client, **overrides):
    body = {
        "merchant": "Test Pharmacy",
        "date": "2026-01-15",
        "amount": 42.50,
        "category": "Pharmacy",
        "notes": "prescription",
    }
    body.update(overrides)
    res = client.post("/api/v1/expenses", json=body)
    assert res.status_code == 201, res.text
    return res.json()


def test_create_and_list_expense(client):
    expense = _make_expense(client)
    assert expense["id"] > 0
    assert expense["amount"] == 42.50
    assert expense["reimbursed"] is False

    res = client.get("/api/v1/expenses")
    assert res.status_code == 200
    assert len(res.json()) == 1


def test_reject_zero_amount(client):
    res = client.post("/api/v1/expenses", json={
        "merchant": "Bad", "date": "2026-01-01", "amount": 0, "category": "Other",
    })
    assert res.status_code == 422


def test_reject_bad_category(client):
    res = client.post("/api/v1/expenses", json={
        "merchant": "Bad", "date": "2026-01-01", "amount": 10, "category": "NotARealCategory",
    })
    assert res.status_code == 422


def test_mark_reimbursed_sets_date(client):
    expense = _make_expense(client)
    res = client.put(f"/api/v1/expenses/{expense['id']}", json={"reimbursed": True})
    assert res.status_code == 200
    assert res.json()["reimbursed"] is True
    assert res.json()["reimbursed_date"] is not None


def test_upload_and_fetch_receipt(client):
    expense = _make_expense(client)
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    res = client.post(
        f"/api/v1/expenses/{expense['id']}/receipts",
        files={"file": ("receipt.png", io.BytesIO(png_bytes), "image/png")},
    )
    assert res.status_code == 201, res.text
    receipt_id = res.json()["id"]

    res = client.get(f"/api/v1/receipts/{receipt_id}/file")
    assert res.status_code == 200
    assert res.content.startswith(b"\x89PNG")


def test_reject_oversized_receipt(client):
    expense = _make_expense(client)
    huge = b"\x00" * (11 * 1024 * 1024)
    res = client.post(
        f"/api/v1/expenses/{expense['id']}/receipts",
        files={"file": ("big.png", io.BytesIO(huge), "image/png")},
    )
    assert res.status_code == 413


def test_reject_bad_mime(client):
    expense = _make_expense(client)
    res = client.post(
        f"/api/v1/expenses/{expense['id']}/receipts",
        files={"file": ("malware.exe", io.BytesIO(b"MZ"), "application/x-msdownload")},
    )
    assert res.status_code == 400


def test_delete_expense_cascades_receipts(client, tmp_path):
    from backend.routers import expenses as expenses_router
    receipts_dir = expenses_router.RECEIPTS_DIR

    expense = _make_expense(client)
    client.post(
        f"/api/v1/expenses/{expense['id']}/receipts",
        files={"file": ("r.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )
    assert any(receipts_dir.iterdir())

    res = client.delete(f"/api/v1/expenses/{expense['id']}")
    assert res.status_code == 204
    assert not any(receipts_dir.iterdir())


def test_summary_and_annual_ledger(client):
    _make_expense(client, amount=100, date="2025-06-01")
    e2 = _make_expense(client, amount=50, date="2026-02-01")
    client.put(f"/api/v1/expenses/{e2['id']}", json={"reimbursed": True})

    summary = client.get("/api/v1/summary").json()
    assert summary["total_unreimbursed"] == 100.0
    assert summary["total_reimbursed"] == 50.0

    ledger = client.get("/api/v1/annual-ledger").json()
    years = {row["year"]: row for row in ledger}
    assert years[2025]["total_unreimbursed"] == 100.0
    assert years[2026]["total_reimbursed"] == 50.0


def test_csv_export(client):
    _make_expense(client, merchant="CVS", amount=25.00, date="2026-03-01")
    _make_expense(client, merchant="Walgreens", amount=75.00, date="2025-12-01")

    res = client.get("/api/v1/export/csv")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "stowe-ledger.csv" in res.headers["content-disposition"]

    text = res.text
    assert "Date,Merchant,Category,Amount" in text
    assert "CVS" in text and "Walgreens" in text

    res_2026 = client.get("/api/v1/export/csv?year=2026")
    assert "stowe-ledger-2026.csv" in res_2026.headers["content-disposition"]
    assert "CVS" in res_2026.text
    assert "Walgreens" not in res_2026.text
