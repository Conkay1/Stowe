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


# ── Reimbursement Pull Events ────────────────────────────────────────────────


def test_create_pull_marks_expenses_reimbursed(client):
    e1 = _make_expense(client, merchant="A", amount=10, date="2026-01-10")
    e2 = _make_expense(client, merchant="B", amount=20, date="2026-01-11")
    e3 = _make_expense(client, merchant="C", amount=30, date="2026-01-12")

    res = client.post("/api/v1/reimbursements", json={
        "date": "2026-04-01",
        "reference": "TEST-001",
        "notes": "Q1 reimbursement",
        "expense_ids": [e1["id"], e2["id"], e3["id"]],
    })
    assert res.status_code == 201, res.text
    pull = res.json()
    assert pull["expense_count"] == 3
    assert pull["total_amount"] == 60.0
    assert pull["reference"] == "TEST-001"
    assert pull["date"] == "2026-04-01"
    assert len(pull["expenses"]) == 3

    # Each linked expense should be reimbursed with the pull's date and id.
    for e in pull["expenses"]:
        assert e["reimbursed"] is True
        assert e["reimbursed_date"] == "2026-04-01"
        assert e["reimbursement_id"] == pull["id"]


def test_create_pull_rejects_already_reimbursed(client):
    e = _make_expense(client)
    client.put(f"/api/v1/expenses/{e['id']}", json={"reimbursed": True})

    res = client.post("/api/v1/reimbursements", json={
        "date": "2026-04-01",
        "expense_ids": [e["id"]],
    })
    assert res.status_code == 400
    assert "already reimbursed" in res.json()["detail"].lower()


def test_create_pull_rejects_unknown_expense_id(client):
    e = _make_expense(client)
    res = client.post("/api/v1/reimbursements", json={
        "date": "2026-04-01",
        "expense_ids": [e["id"], 99999],
    })
    assert res.status_code == 400
    assert "unknown" in res.json()["detail"].lower()


def test_create_pull_rejects_empty_list(client):
    res = client.post("/api/v1/reimbursements", json={
        "date": "2026-04-01",
        "expense_ids": [],
    })
    assert res.status_code == 422


def test_create_pull_rejects_future_date(client):
    e = _make_expense(client)
    res = client.post("/api/v1/reimbursements", json={
        "date": "2099-01-01",
        "expense_ids": [e["id"]],
    })
    assert res.status_code == 422


def test_list_pulls_orders_newest_first(client):
    e1 = _make_expense(client, merchant="A", amount=10, date="2026-01-10")
    e2 = _make_expense(client, merchant="B", amount=20, date="2026-01-11")

    older = client.post("/api/v1/reimbursements", json={
        "date": "2026-02-01", "expense_ids": [e1["id"]],
    }).json()
    newer = client.post("/api/v1/reimbursements", json={
        "date": "2026-03-01", "expense_ids": [e2["id"]],
    }).json()

    res = client.get("/api/v1/reimbursements")
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 2
    assert rows[0]["id"] == newer["id"]
    assert rows[1]["id"] == older["id"]


def test_get_pull_returns_nested_expenses(client):
    e1 = _make_expense(client, merchant="A", amount=10)
    e2 = _make_expense(client, merchant="B", amount=20)
    pull = client.post("/api/v1/reimbursements", json={
        "date": "2026-04-01", "expense_ids": [e1["id"], e2["id"]],
    }).json()

    res = client.get(f"/api/v1/reimbursements/{pull['id']}")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == pull["id"]
    assert len(data["expenses"]) == 2
    merchants = {e["merchant"] for e in data["expenses"]}
    assert merchants == {"A", "B"}


def test_delete_pull_unmarks_expenses(client):
    e1 = _make_expense(client, amount=50)
    e2 = _make_expense(client, amount=75)
    pull = client.post("/api/v1/reimbursements", json={
        "date": "2026-04-01", "expense_ids": [e1["id"], e2["id"]],
    }).json()

    # Before: both expenses reimbursed and linked.
    assert client.get("/api/v1/summary").json()["total_reimbursed"] == 125.0

    res = client.delete(f"/api/v1/reimbursements/{pull['id']}")
    assert res.status_code == 204

    # Pull is gone.
    assert client.get(f"/api/v1/reimbursements/{pull['id']}").status_code == 404

    # Expenses revert to unreimbursed with all three fields cleared.
    for eid in (e1["id"], e2["id"]):
        e = client.get(f"/api/v1/expenses/{eid}").json()
        assert e["reimbursed"] is False
        assert e["reimbursed_date"] is None
        assert e["reimbursement_id"] is None

    summary = client.get("/api/v1/summary").json()
    assert summary["total_reimbursed"] == 0.0
    assert summary["total_unreimbursed"] == 125.0


def test_summary_includes_pulled_expenses_in_reimbursed_total(client):
    e = _make_expense(client, amount=50)
    client.post("/api/v1/reimbursements", json={
        "date": "2026-04-01", "expense_ids": [e["id"]],
    })

    summary = client.get("/api/v1/summary").json()
    assert summary["total_reimbursed"] == 50.0
    assert summary["count_reimbursed"] == 1


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
