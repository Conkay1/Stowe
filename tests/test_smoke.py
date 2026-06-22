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


# ── Auto-review: eligibility classifier (pure, no OCR) ───────────────────────


def test_eligibility_classifies_pharmacy_receipt():
    from backend.eligibility import analyze_text
    r = analyze_text(
        "CVS PHARMACY #1234\n123 Main St\nPRESCRIPTION COPAY\n"
        "Lisinopril 10mg\nTotal $15.00\n01/15/2026\nThank you"
    )
    assert r["status"] == "eligible"
    assert r["confidence"] == "high"
    assert r["amount"] == 15.00
    assert r["date"] == "2026-01-15"
    assert r["category"] == "Pharmacy"
    assert "copay" in r["matched_eligible"]
    assert "IRS Publication 502" in r["notes"]


def test_eligibility_mixed_cart_needs_review():
    from backend.eligibility import analyze_text
    r = analyze_text("CVS PHARMACY\nIBUPROFEN $8.99\nSHAMPOO $5.49\nTotal $14.48\n02/03/2026")
    assert r["status"] == "needs_review"
    assert "ibuprofen" in r["matched_eligible"]
    assert "shampoo" in r["matched_ineligible"]


def test_eligibility_toiletries_ineligible():
    from backend.eligibility import analyze_text
    r = analyze_text("TARGET\nSHAMPOO $5.49\nTOOTHPASTE $3.99\nSODA $1.99\nTotal $11.47\n03/01/2026")
    assert r["status"] == "ineligible"
    assert r["matched_eligible"] == []


def test_eligibility_empty_is_not_analyzed():
    from backend.eligibility import analyze_text
    assert analyze_text("")["status"] == "not_analyzed"
    assert analyze_text("   \n  ")["status"] == "not_analyzed"


def test_eligibility_prefers_total_over_subtotal():
    from backend.eligibility import analyze_text
    r = analyze_text("Subtotal $10.00\nTax $0.80\nTotal $10.80")
    assert r["amount"] == 10.80


def test_eligibility_ignores_future_dates():
    from backend.eligibility import analyze_text
    r = analyze_text("Exp 01/15/2099\nPaid 02/01/2026\nTotal $20.00")
    assert r["date"] == "2026-02-01"


def test_eligibility_word_boundary_no_false_positives():
    from backend.eligibility import analyze_text
    # "rx" must not match inside "marxist"; "spf" must not match inside random text.
    r = analyze_text("Pure water and 24 marxist pamphlets")
    assert r["matched_eligible"] == []


# ── Auto-review: endpoints (OCR monkeypatched to canned text) ────────────────


def test_analyze_receipt_endpoint(client, monkeypatch):
    from backend import ocr
    monkeypatch.setattr(
        ocr, "extract_text",
        lambda p: ("CVS PHARMACY\nPRESCRIPTION COPAY\nTotal $15.00\n01/15/2026", "vision"),
    )
    res = client.post(
        "/api/v1/receipts/analyze",
        files={"file": ("r.png", io.BytesIO(b"\x89PNG\r\n\x1a\nxx"), "image/png")},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "eligible"
    assert body["amount"] == 15.00
    assert body["category"] == "Pharmacy"
    assert body["method"] == "vision"


def test_upload_triggers_auto_review(client, monkeypatch):
    from backend import ocr
    monkeypatch.setattr(
        ocr, "extract_text",
        lambda p: ("WALGREENS PHARMACY\nFLU SHOT $40.00\n05/01/2026", "vision"),
    )
    expense = _make_expense(client)
    res = client.post(
        f"/api/v1/expenses/{expense['id']}/receipts",
        files={"file": ("r.png", io.BytesIO(b"\x89PNG\r\n\x1a\nxx"), "image/png")},
    )
    assert res.status_code == 201, res.text

    e = client.get(f"/api/v1/expenses/{expense['id']}").json()
    assert e["auto_review_status"] == "eligible"
    assert e["auto_review"]["amount"] == 40.00
    assert e["auto_review"]["method"] == "vision"
    assert "IRS Publication 502" in e["auto_review_notes"]


def test_reanalyze_endpoint(client, monkeypatch):
    from backend import ocr
    expense = _make_expense(client)

    # First upload yields no readable text → not_analyzed.
    monkeypatch.setattr(ocr, "extract_text", lambda p: ("", "none"))
    client.post(
        f"/api/v1/expenses/{expense['id']}/receipts",
        files={"file": ("r.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )
    assert client.get(f"/api/v1/expenses/{expense['id']}").json()["auto_review_status"] == "not_analyzed"

    # Swap in good text and re-analyze the existing receipt.
    monkeypatch.setattr(ocr, "extract_text", lambda p: ("DENTAL CLEANING\nTotal $120.00\n04/04/2026", "pdf-text"))
    res = client.post(f"/api/v1/expenses/{expense['id']}/analyze")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["auto_review_status"] == "eligible"
    assert body["auto_review"]["category"] == "Dental"


def test_auto_review_failure_is_silent(client, monkeypatch):
    from backend import ocr

    def boom(path):
        raise RuntimeError("vision exploded")

    monkeypatch.setattr(ocr, "extract_text", boom)
    expense = _make_expense(client)
    res = client.post(
        f"/api/v1/expenses/{expense['id']}/receipts",
        files={"file": ("r.png", io.BytesIO(b"\x89PNG\r\n\x1a\nxx"), "image/png")},
    )
    # OCR blowing up must never break the upload, and status stays at the default.
    assert res.status_code == 201, res.text
    assert client.get(f"/api/v1/expenses/{expense['id']}").json()["auto_review_status"] == "not_analyzed"
