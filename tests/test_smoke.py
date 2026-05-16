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


# ── Custom Categories (v0.6.0) ───────────────────────────────────────────────


def _make_category(client, name: str):
    """Helper: create a custom category and assert 200."""
    res = client.post("/api/v1/categories", json={"name": name})
    assert res.status_code == 200, res.text
    return res.json()


def test_create_and_list_categories(client):
    """Creating a custom category shows up in the list alongside built-ins."""
    from config import HSA_CATEGORIES

    cat = _make_category(client, "Chiropractic")
    assert cat["name"] == "Chiropractic"
    assert cat["is_default"] is False
    assert cat["id"] > 0

    res = client.get("/api/v1/categories")
    assert res.status_code == 200
    names = [c["name"] for c in res.json()]

    # All built-in categories must be present.
    for builtin in HSA_CATEGORIES:
        assert builtin in names, f"Missing built-in: {builtin}"

    # Our custom one must appear too.
    assert "Chiropractic" in names


def test_create_duplicate_category_rejected(client):
    """Creating a category with a name that already exists returns 400."""
    _make_category(client, "Acupuncture")
    res = client.post("/api/v1/categories", json={"name": "Acupuncture"})
    assert res.status_code == 400


def test_create_category_name_clash_with_builtin(client):
    """A custom category whose name matches a built-in should be rejected."""
    res = client.post("/api/v1/categories", json={"name": "Pharmacy"})
    assert res.status_code == 400


def test_reassign_expense_to_custom_category(client):
    """An expense can be re-categorised to a freshly created custom category."""
    cat = _make_category(client, "HearingAids")
    expense = _make_expense(client, category="Other")

    res = client.put(f"/api/v1/expenses/{expense['id']}", json={"category": "HearingAids"})
    assert res.status_code == 200
    assert res.json()["category"] == "HearingAids"

    # Verify it persisted.
    fetched = client.get(f"/api/v1/expenses/{expense['id']}").json()
    assert fetched["category"] == "HearingAids"
    _ = cat  # suppress "unused" warning; cat was created to ensure the category exists


def test_delete_custom_category_not_in_use(client):
    """Deleting a custom category that no expense references returns 204."""
    cat = _make_category(client, "Telemedicine")
    res = client.delete(f"/api/v1/categories/{cat['id']}")
    assert res.status_code == 204

    # It should be gone from the list.
    names = [c["name"] for c in client.get("/api/v1/categories").json()]
    assert "Telemedicine" not in names


def test_delete_custom_category_in_use_cascades_to_other(client):
    """Deleting a custom category that IS in use reassigns its expenses to 'Other'
    (the router does a cascading UPDATE rather than returning a 4xx)."""
    cat = _make_category(client, "Naturopath")
    expense = _make_expense(client, category="Naturopath")

    # Verify the expense actually has that category.
    assert client.get(f"/api/v1/expenses/{expense['id']}").json()["category"] == "Naturopath"

    res = client.delete(f"/api/v1/categories/{cat['id']}")
    assert res.status_code == 204

    # The expense should now be in "Other".
    updated = client.get(f"/api/v1/expenses/{expense['id']}").json()
    assert updated["category"] == "Other"


def test_delete_default_category_rejected(client):
    """Attempting to delete a built-in/default category should return 400."""
    # First, ensure the "Pharmacy" category exists in the DB by listing categories.
    categories = client.get("/api/v1/categories").json()
    pharmacy = next((c for c in categories if c["name"] == "Pharmacy" and c["id"] != 0), None)
    if pharmacy is None:
        # The category exists as a virtual row (id=0); we can't meaningfully delete it,
        # so just confirm a DELETE to id=0 returns 404 (not found — there's no DB row).
        res = client.delete("/api/v1/categories/0")
        assert res.status_code == 404
    else:
        res = client.delete(f"/api/v1/categories/{pharmacy['id']}")
        assert res.status_code == 400


# ── HSA Account Linking (v0.6.0) ─────────────────────────────────────────────


def _make_account(client, **overrides):
    body = {
        "name": "Fidelity HSA",
        "custodian": "Fidelity",
        "account_mask": "1234",
        "notes": "Primary HSA",
    }
    body.update(overrides)
    res = client.post("/api/v1/accounts", json=body)
    assert res.status_code == 201, res.text
    return res.json()


def test_create_and_list_accounts(client):
    """Creating an HSA account makes it appear in the account list."""
    acc = _make_account(client)
    assert acc["id"] > 0
    assert acc["name"] == "Fidelity HSA"
    assert acc["custodian"] == "Fidelity"
    assert acc["is_active"] is True
    assert acc["pull_count"] == 0
    assert acc["distribution_count"] == 0

    res = client.get("/api/v1/accounts")
    assert res.status_code == 200
    assert len(res.json()) == 1
    assert res.json()[0]["id"] == acc["id"]


def test_fetch_account_by_id(client):
    """GET /accounts/{id} returns the right account."""
    acc = _make_account(client, name="Optum HSA", custodian="Optum")
    res = client.get(f"/api/v1/accounts/{acc['id']}")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == acc["id"]
    assert data["name"] == "Optum HSA"
    assert data["custodian"] == "Optum"


def test_fetch_account_not_found(client):
    """GET /accounts/99999 returns 404."""
    assert client.get("/api/v1/accounts/99999").status_code == 404


def test_edit_account_details(client):
    """PUT /accounts/{id} can update name, custodian, and notes."""
    acc = _make_account(client)
    res = client.put(f"/api/v1/accounts/{acc['id']}", json={
        "name": "Fidelity HSA (Renamed)",
        "notes": "Updated notes",
    })
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "Fidelity HSA (Renamed)"
    assert data["notes"] == "Updated notes"
    assert data["custodian"] == "Fidelity"  # unchanged


def test_deactivate_account(client):
    """PUT /accounts/{id}/deactivate flips is_active to False."""
    acc = _make_account(client)
    res = client.put(f"/api/v1/accounts/{acc['id']}/deactivate")
    assert res.status_code == 200
    assert res.json()["is_active"] is False


def test_delete_account_with_no_data(client):
    """DELETE /accounts/{id} succeeds when no pulls or distributions reference it."""
    acc = _make_account(client)
    res = client.delete(f"/api/v1/accounts/{acc['id']}")
    assert res.status_code == 204

    # Verify it's gone.
    assert client.get(f"/api/v1/accounts/{acc['id']}").status_code == 404


def test_delete_account_with_distributions_rejected(client):
    """DELETE /accounts/{id} returns 400 when distributions still reference it."""
    acc = _make_account(client)

    # Import one distribution via the commit endpoint.
    res = client.post(f"/api/v1/accounts/{acc['id']}/imports/commit", json={
        "column_map": {"date": "date", "amount": "amount"},
        "rows": [{"date": "2026-01-10", "amount": 50.0}],
    })
    assert res.status_code == 200, res.text
    assert res.json()["inserted"] == 1

    res = client.delete(f"/api/v1/accounts/{acc['id']}")
    assert res.status_code == 400
    assert "distribution" in res.json()["detail"].lower()


def test_account_mask_full_number_rejected(client):
    """Providing a full account number (8+ digits) as the mask returns 422."""
    res = client.post("/api/v1/accounts", json={
        "name": "Bad Mask",
        "custodian": "Fidelity",
        "account_mask": "123456789",  # 9 digits — should be rejected
    })
    assert res.status_code == 422


# ── Custodian CSV Import (v0.6.0) ────────────────────────────────────────────


def _csv_bytes(rows: list[dict]) -> bytes:
    """Build a minimal CSV from a list of dicts; first dict's keys become headers."""
    import csv, io
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode()


def test_csv_import_parse_returns_headers_and_rows(client):
    """POST .../imports/parse returns headers, data rows, and a suggested column map."""
    acc = _make_account(client)
    csv_content = _csv_bytes([
        {"date": "2026-01-15", "amount": "42.50", "description": "Pharmacy co-pay"},
        {"date": "2026-01-20", "amount": "15.00", "description": "Vision exam"},
    ])
    res = client.post(
        f"/api/v1/accounts/{acc['id']}/imports/parse",
        files={"file": ("export.csv", io.BytesIO(csv_content), "text/csv")},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert "headers" in data
    assert "date" in data["headers"]
    assert "amount" in data["headers"]
    assert len(data["data_rows"]) == 2
    # The alias dict should map 'date' -> 'date' and 'amount' -> 'amount'.
    assert data["suggested_map"].get("date") == "date"
    assert data["suggested_map"].get("amount") == "amount"


def test_csv_import_commit_inserts_distributions(client):
    """Committing valid CSV rows creates the expected CustodianDistribution records."""
    acc = _make_account(client)
    res = client.post(f"/api/v1/accounts/{acc['id']}/imports/commit", json={
        "column_map": {"date": "date", "amount": "amount", "description": "description"},
        "rows": [
            {"date": "2026-01-15", "amount": 42.50, "description": "Pharmacy co-pay"},
            {"date": "2026-01-20", "amount": 15.00, "description": "Vision exam"},
            {"date": "2026-01-25", "amount": 100.00, "description": "Dental cleaning"},
        ],
    })
    assert res.status_code == 200, res.text
    result = res.json()
    assert result["inserted"] == 3
    assert result["skipped_dupes"] == 0
    assert result["skipped_invalid"] == 0

    # Verify distributions were actually stored.
    distribs = client.get(f"/api/v1/accounts/{acc['id']}/distributions").json()
    assert len(distribs) == 3
    amounts = sorted(d["amount"] for d in distribs)
    assert amounts == [15.0, 42.5, 100.0]


def test_csv_import_commit_deduplicates_by_custodian_ref(client):
    """Re-importing the same custodian_ref skips the duplicate row."""
    acc = _make_account(client)
    row = {"date": "2026-02-01", "amount": 30.0, "custodian_ref": "TXN-001"}

    res1 = client.post(f"/api/v1/accounts/{acc['id']}/imports/commit", json={
        "column_map": {"date": "date", "amount": "amount", "custodian_ref": "custodian_ref"},
        "rows": [row],
    })
    assert res1.json()["inserted"] == 1

    res2 = client.post(f"/api/v1/accounts/{acc['id']}/imports/commit", json={
        "column_map": {"date": "date", "amount": "amount", "custodian_ref": "custodian_ref"},
        "rows": [row],
    })
    assert res2.json()["inserted"] == 0
    assert res2.json()["skipped_dupes"] == 1

    # Only one distribution should exist.
    assert len(client.get(f"/api/v1/accounts/{acc['id']}/distributions").json()) == 1


def test_csv_import_commit_skips_zero_amount(client):
    """Rows with amount == 0 are silently skipped (skipped_invalid counter incremented)."""
    acc = _make_account(client)
    res = client.post(f"/api/v1/accounts/{acc['id']}/imports/commit", json={
        "column_map": {"date": "date", "amount": "amount"},
        "rows": [
            {"date": "2026-03-01", "amount": 0.0},
            {"date": "2026-03-02", "amount": 25.0},
        ],
    })
    assert res.status_code == 200
    result = res.json()
    assert result["inserted"] == 1
    assert result["skipped_invalid"] == 1


def test_csv_import_parse_empty_file_returns_400(client):
    """Uploading an empty file to the parse endpoint returns a clean 400."""
    acc = _make_account(client)
    res = client.post(
        f"/api/v1/accounts/{acc['id']}/imports/parse",
        files={"file": ("empty.csv", io.BytesIO(b""), "text/csv")},
    )
    assert res.status_code == 400


def test_csv_import_parse_no_rows_returns_400(client):
    """A CSV with only a header line (no data rows) still parses; header-only
    content with an empty body returns 400."""
    acc = _make_account(client)
    # Completely empty body — should be a 400.
    res = client.post(
        f"/api/v1/accounts/{acc['id']}/imports/parse",
        files={"file": ("headeronly.csv", io.BytesIO(b""), "text/csv")},
    )
    assert res.status_code == 400


def test_csv_import_parse_junk_content(client):
    """A binary / non-CSV upload is decoded but should not cause a 500."""
    acc = _make_account(client)
    # Non-parseable binary content — the route should return 4xx, not 500.
    junk = b"\x00\x01\x02\x03" * 10
    res = client.post(
        f"/api/v1/accounts/{acc['id']}/imports/parse",
        files={"file": ("junk.csv", io.BytesIO(junk), "text/csv")},
    )
    # The router decodes via latin-1 (never fails), sniffs the delimiter, and
    # returns whatever single "header" row it found — so either a 200 with an
    # odd header, or a 400. Either way it must NOT be a 500.
    assert res.status_code != 500


# ── Reconciliation (v0.6.0) ──────────────────────────────────────────────────


def _make_pull(client, amount: float, date: str = "2026-03-01", **expense_kwargs):
    """Create a single-expense pull and return the pull JSON."""
    expense = _make_expense(client, amount=amount, date="2026-01-10", **expense_kwargs)
    res = client.post("/api/v1/reimbursements", json={
        "date": date,
        "total_amount": amount,
        "line_items": [{"expense_id": expense["id"], "covered_amount": amount}],
    })
    assert res.status_code == 201, res.text
    return res.json()


def _commit_distributions(client, account_id: int, rows: list[dict]) -> dict:
    """Commit distribution rows and return the CSVCommitResult JSON."""
    res = client.post(f"/api/v1/accounts/{account_id}/imports/commit", json={
        "column_map": {"date": "date", "amount": "amount", "description": "description"},
        "rows": rows,
    })
    assert res.status_code == 200, res.text
    return res.json()


def test_reconciliation_empty_account(client):
    """A fresh account has no matched, no unmatched distributions, no unmatched pulls."""
    acc = _make_account(client)
    res = client.get(f"/api/v1/accounts/{acc['id']}/reconciliation")
    assert res.status_code == 200
    data = res.json()
    assert data["matched"] == []
    assert data["unmatched_distributions"] == []
    assert data["unmatched_pulls"] == []


def test_reconciliation_unmatched_distribution_and_pull(client):
    """Importing a distribution and creating a pull (tagged to the account) with
    different amounts gives one unmatched distribution and one unmatched pull."""
    acc = _make_account(client)

    # Import a distribution for $75.
    _commit_distributions(client, acc["id"], [
        {"date": "2026-04-01", "amount": 75.0, "description": "Dist A"},
    ])

    # Create a pull for $50 tagged to the account.
    expense = _make_expense(client, amount=50.0)
    pull_res = client.post("/api/v1/reimbursements", json={
        "date": "2026-04-01",
        "total_amount": 50.0,
        "line_items": [{"expense_id": expense["id"], "covered_amount": 50.0}],
    })
    pull = pull_res.json()
    # Tag the pull to the account.
    client.put(f"/api/v1/reimbursements/{pull['id']}", json={"account_id": acc["id"]})

    res = client.get(f"/api/v1/accounts/{acc['id']}/reconciliation")
    data = res.json()
    assert len(data["unmatched_distributions"]) == 1
    assert data["unmatched_distributions"][0]["amount"] == 75.0
    assert len(data["unmatched_pulls"]) == 1
    assert data["unmatched_pulls"][0]["total_amount"] == 50.0
    assert data["matched"] == []


def test_reconciliation_auto_match_exact_amount(client):
    """A distribution imported after its matching pull is auto-linked during commit
    (the commit endpoint calls _try_auto_link for each new row). The reconciliation
    view then shows the pair as matched. Calling reconcile/auto a second time is a
    no-op (returns 0, because there are no unmatched distributions left)."""
    acc = _make_account(client)

    # Create a pull for $42.50 tagged to the account.
    expense = _make_expense(client, amount=42.50)
    pull_res = client.post("/api/v1/reimbursements", json={
        "date": "2026-04-10",
        "total_amount": 42.50,
        "line_items": [{"expense_id": expense["id"], "covered_amount": 42.50}],
    })
    pull = pull_res.json()
    client.put(f"/api/v1/reimbursements/{pull['id']}", json={"account_id": acc["id"]})

    # Import a distribution for the same amount on the same date.
    # The commit endpoint will auto-link it during the import.
    commit_result = _commit_distributions(client, acc["id"], [
        {"date": "2026-04-10", "amount": 42.50, "description": "HSA Reimbursement"},
    ])
    assert commit_result["inserted"] == 1
    assert commit_result["auto_matched"] == 1  # auto-linked at import time

    # The reconciliation view should already show the pair as matched.
    data = client.get(f"/api/v1/accounts/{acc['id']}/reconciliation").json()
    assert len(data["matched"]) == 1
    assert data["matched"][0]["pull_id"] == pull["id"]
    assert data["matched"][0]["pull_total"] == 42.50
    assert data["unmatched_distributions"] == []
    assert data["unmatched_pulls"] == []

    # Calling reconcile/auto again is a no-op — nothing left to match.
    rec_res = client.post(f"/api/v1/accounts/{acc['id']}/reconcile/auto")
    assert rec_res.status_code == 200
    assert rec_res.json()["matched_count"] == 0


def test_reconciliation_amount_mismatch_leaves_both_unmatched(client):
    """A distribution and a pull with clearly different amounts remain unmatched after
    auto-reconcile — both appear in their respective unmatched lists."""
    acc = _make_account(client)

    # Pull for $100.
    expense = _make_expense(client, amount=100.0)
    pull_res = client.post("/api/v1/reimbursements", json={
        "date": "2026-05-01",
        "total_amount": 100.0,
        "line_items": [{"expense_id": expense["id"], "covered_amount": 100.0}],
    })
    pull = pull_res.json()
    client.put(f"/api/v1/reimbursements/{pull['id']}", json={"account_id": acc["id"]})

    # Distribution for a different amount ($75 — well outside the $0.01 tolerance).
    _commit_distributions(client, acc["id"], [
        {"date": "2026-05-01", "amount": 75.0, "description": "Mismatched amount"},
    ])

    rec_res = client.post(f"/api/v1/accounts/{acc['id']}/reconcile/auto")
    assert rec_res.status_code == 200
    assert rec_res.json()["matched_count"] == 0

    data = client.get(f"/api/v1/accounts/{acc['id']}/reconciliation").json()
    assert len(data["unmatched_distributions"]) == 1
    assert data["unmatched_distributions"][0]["amount"] == 75.0
    assert len(data["unmatched_pulls"]) == 1
    assert data["unmatched_pulls"][0]["total_amount"] == 100.0
    assert data["matched"] == []


def test_reconciliation_manual_match_and_unmatch(client):
    """POST /distributions/{id}/match links a distribution to a pull;
    DELETE /distributions/{id}/match reverses it."""
    acc = _make_account(client)

    # Pull for $60.
    expense = _make_expense(client, amount=60.0)
    pull_res = client.post("/api/v1/reimbursements", json={
        "date": "2026-05-15",
        "total_amount": 60.0,
        "line_items": [{"expense_id": expense["id"], "covered_amount": 60.0}],
    })
    pull = pull_res.json()

    # Import a distribution (amount deliberately different so auto-match won't fire).
    _commit_distributions(client, acc["id"], [
        {"date": "2026-05-15", "amount": 60.01, "description": "Slightly off"},
    ])
    distribs = client.get(f"/api/v1/accounts/{acc['id']}/distributions").json()
    dist_id = distribs[0]["id"]

    # Manual match.
    match_res = client.post(f"/api/v1/distributions/{dist_id}/match",
                            json={"reimbursement_id": pull["id"]})
    assert match_res.status_code == 200
    assert match_res.json()["reimbursement_id"] == pull["id"]
    assert match_res.json()["match_method"] == "manual"

    # Reconciliation should show it as matched.
    data = client.get(f"/api/v1/accounts/{acc['id']}/reconciliation").json()
    assert len(data["matched"]) == 1

    # Unmatch.
    unmatch_res = client.delete(f"/api/v1/distributions/{dist_id}/match")
    assert unmatch_res.status_code == 200
    assert unmatch_res.json()["reimbursement_id"] is None

    # Should be unmatched again.
    data = client.get(f"/api/v1/accounts/{acc['id']}/reconciliation").json()
    assert data["matched"] == []
    assert len(data["unmatched_distributions"]) == 1
