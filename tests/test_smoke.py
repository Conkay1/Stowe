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


def _full_pull(eid, amount):
    """Helper: line item covering an expense fully."""
    return {"expense_id": eid, "covered_amount": amount}


def test_create_pull_marks_expenses_reimbursed(client):
    e1 = _make_expense(client, merchant="A", amount=10, date="2026-01-10")
    e2 = _make_expense(client, merchant="B", amount=20, date="2026-01-11")
    e3 = _make_expense(client, merchant="C", amount=30, date="2026-01-12")

    res = client.post("/api/v1/reimbursements", json={
        "date": "2026-04-01",
        "reference": "TEST-001",
        "notes": "Q1 reimbursement",
        "total_amount": 60.0,
        "line_items": [
            _full_pull(e1["id"], 10),
            _full_pull(e2["id"], 20),
            _full_pull(e3["id"], 30),
        ],
    })
    assert res.status_code == 201, res.text
    pull = res.json()
    assert pull["expense_count"] == 3
    assert pull["total_amount"] == 60.0
    assert pull["reference"] == "TEST-001"
    assert pull["date"] == "2026-04-01"
    assert len(pull["line_items"]) == 3

    # Each linked expense should be reimbursed with the pull's date.
    for eid in (e1["id"], e2["id"], e3["id"]):
        e = client.get(f"/api/v1/expenses/{eid}").json()
        assert e["reimbursed"] is True
        assert e["reimbursed_date"] == "2026-04-01"
        assert e["covered_amount"] == e["amount"]
        assert e["remaining_amount"] == 0.0
        assert e["pull_count"] == 1


def test_create_pull_rejects_manually_marked_reimbursed(client):
    e = _make_expense(client)
    client.put(f"/api/v1/expenses/{e['id']}", json={"reimbursed": True})

    res = client.post("/api/v1/reimbursements", json={
        "date": "2026-04-01",
        "total_amount": e["amount"],
        "line_items": [_full_pull(e["id"], e["amount"])],
    })
    assert res.status_code == 400
    assert "manually marked" in res.json()["detail"].lower()


def test_create_pull_rejects_unknown_expense_id(client):
    e = _make_expense(client)
    res = client.post("/api/v1/reimbursements", json={
        "date": "2026-04-01",
        "total_amount": e["amount"] + 5.0,
        "line_items": [_full_pull(e["id"], e["amount"]), _full_pull(99999, 5.0)],
    })
    assert res.status_code == 400
    assert "unknown" in res.json()["detail"].lower()


def test_create_pull_rejects_empty_line_items(client):
    res = client.post("/api/v1/reimbursements", json={
        "date": "2026-04-01",
        "total_amount": 10.0,
        "line_items": [],
    })
    assert res.status_code == 422


def test_create_pull_rejects_sum_mismatch(client):
    e = _make_expense(client, amount=42.5)
    res = client.post("/api/v1/reimbursements", json={
        "date": "2026-04-01",
        "total_amount": 50.0,
        "line_items": [_full_pull(e["id"], 42.5)],
    })
    assert res.status_code == 422


def test_create_pull_rejects_future_date(client):
    e = _make_expense(client)
    res = client.post("/api/v1/reimbursements", json={
        "date": "2099-01-01",
        "total_amount": e["amount"],
        "line_items": [_full_pull(e["id"], e["amount"])],
    })
    assert res.status_code == 422


def test_partial_pull_leaves_remainder_in_vault(client):
    e = _make_expense(client, amount=100.0)
    pull = client.post("/api/v1/reimbursements", json={
        "date": "2026-04-01",
        "total_amount": 30.0,
        "line_items": [{"expense_id": e["id"], "covered_amount": 30.0}],
    }).json()

    detail = client.get(f"/api/v1/expenses/{e['id']}").json()
    assert detail["reimbursed"] is False
    assert detail["covered_amount"] == 30.0
    assert detail["remaining_amount"] == 70.0
    assert detail["pull_count"] == 1

    # Summary should reflect partial coverage.
    summary = client.get("/api/v1/summary").json()
    assert summary["total_reimbursed"] == 30.0
    assert summary["total_unreimbursed"] == 70.0
    # The expense is still in-vault (counted as unreimbursed since remaining > 0).
    assert summary["count_unreimbursed"] == 1
    assert summary["count_reimbursed"] == 0
    assert pull["total_amount"] == 30.0


def test_two_pulls_split_one_expense(client):
    e = _make_expense(client, amount=100.0)
    p1 = client.post("/api/v1/reimbursements", json={
        "date": "2026-03-01",
        "total_amount": 30.0,
        "line_items": [{"expense_id": e["id"], "covered_amount": 30.0}],
    }).json()
    p2 = client.post("/api/v1/reimbursements", json={
        "date": "2026-04-01",
        "total_amount": 70.0,
        "line_items": [{"expense_id": e["id"], "covered_amount": 70.0}],
    }).json()

    detail = client.get(f"/api/v1/expenses/{e['id']}").json()
    assert detail["reimbursed"] is True
    assert detail["covered_amount"] == 100.0
    assert detail["remaining_amount"] == 0.0
    assert detail["pull_count"] == 2
    # Reimbursed date = the latest pull date.
    assert detail["reimbursed_date"] == "2026-04-01"

    # Undoing the later pull should drop the expense back to partial.
    client.delete(f"/api/v1/reimbursements/{p2['id']}")
    detail = client.get(f"/api/v1/expenses/{e['id']}").json()
    assert detail["reimbursed"] is False
    assert detail["covered_amount"] == 30.0
    assert detail["remaining_amount"] == 70.0
    assert detail["pull_count"] == 1
    assert p1["total_amount"] == 30.0


def test_create_pull_rejects_overflow(client):
    e = _make_expense(client, amount=50.0)
    # First pull covers $30 — leaves $20 remaining.
    client.post("/api/v1/reimbursements", json={
        "date": "2026-03-01",
        "total_amount": 30.0,
        "line_items": [{"expense_id": e["id"], "covered_amount": 30.0}],
    })
    # Second pull tries to cover $25 — would total $55 on a $50 expense.
    res = client.post("/api/v1/reimbursements", json={
        "date": "2026-04-01",
        "total_amount": 25.0,
        "line_items": [{"expense_id": e["id"], "covered_amount": 25.0}],
    })
    assert res.status_code == 400
    assert "over-cover" in res.json()["detail"].lower()


def test_list_pulls_orders_newest_first(client):
    e1 = _make_expense(client, merchant="A", amount=10, date="2026-01-10")
    e2 = _make_expense(client, merchant="B", amount=20, date="2026-01-11")

    older = client.post("/api/v1/reimbursements", json={
        "date": "2026-02-01",
        "total_amount": 10.0,
        "line_items": [_full_pull(e1["id"], 10)],
    }).json()
    newer = client.post("/api/v1/reimbursements", json={
        "date": "2026-03-01",
        "total_amount": 20.0,
        "line_items": [_full_pull(e2["id"], 20)],
    }).json()

    res = client.get("/api/v1/reimbursements")
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 2
    assert rows[0]["id"] == newer["id"]
    assert rows[1]["id"] == older["id"]


def test_get_pull_returns_nested_line_items(client):
    e1 = _make_expense(client, merchant="A", amount=10)
    e2 = _make_expense(client, merchant="B", amount=20)
    pull = client.post("/api/v1/reimbursements", json={
        "date": "2026-04-01",
        "total_amount": 30.0,
        "line_items": [_full_pull(e1["id"], 10), _full_pull(e2["id"], 20)],
    }).json()

    res = client.get(f"/api/v1/reimbursements/{pull['id']}")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == pull["id"]
    assert len(data["line_items"]) == 2
    merchants = {li["merchant"] for li in data["line_items"]}
    assert merchants == {"A", "B"}


def test_delete_pull_unmarks_expenses(client):
    e1 = _make_expense(client, amount=50)
    e2 = _make_expense(client, amount=75)
    pull = client.post("/api/v1/reimbursements", json={
        "date": "2026-04-01",
        "total_amount": 125.0,
        "line_items": [_full_pull(e1["id"], 50), _full_pull(e2["id"], 75)],
    }).json()

    assert client.get("/api/v1/summary").json()["total_reimbursed"] == 125.0

    res = client.delete(f"/api/v1/reimbursements/{pull['id']}")
    assert res.status_code == 204
    assert client.get(f"/api/v1/reimbursements/{pull['id']}").status_code == 404

    for eid in (e1["id"], e2["id"]):
        e = client.get(f"/api/v1/expenses/{eid}").json()
        assert e["reimbursed"] is False
        assert e["reimbursed_date"] is None
        assert e["covered_amount"] == 0.0
        assert e["remaining_amount"] == e["amount"]

    summary = client.get("/api/v1/summary").json()
    assert summary["total_reimbursed"] == 0.0
    assert summary["total_unreimbursed"] == 125.0


def test_summary_includes_pulled_expenses_in_reimbursed_total(client):
    e = _make_expense(client, amount=50)
    client.post("/api/v1/reimbursements", json={
        "date": "2026-04-01",
        "total_amount": 50.0,
        "line_items": [_full_pull(e["id"], 50)],
    })

    summary = client.get("/api/v1/summary").json()
    assert summary["total_reimbursed"] == 50.0
    assert summary["count_reimbursed"] == 1


def test_update_blocks_reimbursed_toggle_when_pull_backed(client):
    e = _make_expense(client, amount=40)
    client.post("/api/v1/reimbursements", json={
        "date": "2026-04-01",
        "total_amount": 40.0,
        "line_items": [_full_pull(e["id"], 40)],
    })
    res = client.put(f"/api/v1/expenses/{e['id']}", json={"reimbursed": False})
    assert res.status_code == 400
    assert "backed by" in res.json()["detail"].lower()


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
