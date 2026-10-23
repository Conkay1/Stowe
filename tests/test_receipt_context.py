import pytest

from backend.eligibility import analyze_text


@pytest.mark.parametrize("history", [
    "Tobacco use: Never",
    "Smokeless tobacco: Never",
    "Tobacco use\nSmoking status: Never\nSmokeless tobacco: Never",
    "Tobacco use:\nNever",
    "Alcohol use: No",
    "Denies tobacco use.",
    "Tobacco use: Former",
    "CURRENT TOBACCO NON-USER clinical screening",
    "Current tobacco nonuser",
    "Tobacco non user",
])
def test_clinical_history_is_not_a_purchased_item(history):
    result = analyze_text("Synthetic Clinic\nOffice visit\n" + history + "\nAmount paid $25.00")
    assert result["matched_ineligible"] == []
    assert result["status"] == "eligible"


@pytest.mark.parametrize("purchase", [
    "Tobacco $8.00",
    "Tobacco use kit $8.00",
    "Alcohol $8.00",
    "SHAMPOO $8.00",
])
def test_clinical_history_does_not_hide_separate_purchases(purchase):
    result = analyze_text(
        "Synthetic Pharmacy\nIbuprofen $5.00\nTobacco use: Never\n"
        "Current tobacco non-user\n" + purchase
    )
    assert result["matched_ineligible"]
    assert result["status"] == "needs_review"


@pytest.mark.parametrize(("text", "expected"), [
    ("Amount paid $25.00\nRemaining balance $75.00", 25.00),
    ("Payment amount:\n$25.00\nBalance due:\n$75.00", 25.00),
    ("Total $100.00\nPayment received $25.00\nBalance $75.00", 25.00),
    ("Amount paid $25.00 Balance due $75.00", 25.00),
    ("Previous balance $100.00\nPayment $25.00\nNew balance $75.00", 25.00),
    ("Insurance paid $90.00\nYou paid $10.00", 10.00),
    ("Previous payment $80.00\nPayment amount $25.00", 25.00),
    ("Total amount paid $25.00\nTotal amount $100.00", 25.00),
    ("Payment total $25.00\nBalance $75.00", 25.00),
    ("Amount paid $0.00\nBalance due $75.00", 0.00),
    ("Balance due $75.00", None),
    ("Amount due $75.00\nSubtotal $70.00\nTax $5.00", None),
    ("Payment pending $25.00\nBalance due $75.00", None),
    ("Scheduled payment $25.00", None),
    ("Payment reversed $25.00", None),
    ("Refund $25.00\nBalance due $75.00", None),
    ("Amount paid -$25.00", None),
    ("Payment amount $20.00\nPayment amount $25.00", None),
    ("Total $10.00\nTotal $20.00", None),
    ("$20.00\n$35.00", None),
    ("Subtotal $10.00\nTax $0.80\nGrand total $10.80", 10.80),
    ("Total $25.00\nCash tendered $50.00\nChange $25.00", 25.00),
    ("Office visit $25.00", 25.00),
    ("2026-01-15 Total $25.00", 25.00),
    ("Grand total $100.00\nBalance due $100.00", None),
    ("Total $100.00\nUnpaid $100.00", None),
    ("Unpaid $25.00", None),
    ("Due $75.00", None),
    ("Outstanding $75.00", None),
    ("Total $25.00\nBalance due $0.00", None),
    ("Payment amount $25.00\nStatus: pending", None),
    ("Payment amount $25.00\nUnpaid", None),
    ("Unpaid payment amount $25.00", None),
    ("Plan paid $90.00\nYou paid $10.00", 10.00),
    ("Payment amount $25.00\nUnpaid balance $75.00", 25.00),
    ("Payment amount $25.00 Unpaid balance $75.00", 25.00),
])
def test_amount_hint_distinguishes_payments_totals_and_balances(text, expected):
    assert analyze_text(text)["amount"] == expected


def test_receipt_hints_do_not_change_financial_records(client, monkeypatch):
    from backend import ocr

    monkeypatch.setattr(ocr, "extract_text", lambda path: (
        "Synthetic Clinic\nOffice visit\nCurrent tobacco non-user\n"
        "Amount paid $25.00\nBalance due $75.00", "pdf-text"
    ))
    expense_response = client.post("/api/v1/expenses", json={
        "merchant": "Synthetic Clinic", "date": "2026-01-15",
        "amount": 42.50, "category": "Medical", "notes": "Verified original expense",
    })
    assert expense_response.status_code == 201
    expense_id = expense_response.json()["id"]
    pull_response = client.post("/api/v1/reimbursements", json={
        "date": "2026-02-01", "total_amount": 15.00,
        "line_items": [{"expense_id": expense_id, "covered_amount": 15.00}],
    })
    assert pull_response.status_code == 201
    pull_id = pull_response.json()["id"]
    before = client.get(f"/api/v1/expenses/{expense_id}").json()
    pull_before = client.get(f"/api/v1/reimbursements/{pull_id}").json()
    assert pull_before["line_items"][0]["receipt_count"] == 0
    expected_pull = {**pull_before, "line_items": [
        {**pull_before["line_items"][0], "receipt_count": 1},
    ]}
    content = b"%PDF-1.4 synthetic evidence\n%%EOF\n"
    uploaded = client.post(f"/api/v1/expenses/{expense_id}/receipts", files={
        "file": ("synthetic.pdf", content, "application/pdf"),
    })
    assert uploaded.status_code == 201
    receipt = uploaded.json()
    for _ in range(2):
        analyzed = client.post(f"/api/v1/expenses/{expense_id}/analyze")
        assert analyzed.status_code == 200
        after = analyzed.json()
        for field in ["merchant", "date", "amount", "category", "notes", "reimbursed",
                      "reimbursed_date", "covered_amount", "remaining_amount", "pull_count"]:
            assert after[field] == before[field]
        assert client.get(f"/api/v1/reimbursements/{pull_id}").json() == expected_pull
        assert after["receipts"] == [receipt]
        download = client.get(f"/api/v1/receipts/{receipt['id']}/file")
        assert download.status_code == 200
        assert download.content == content
        assert after["auto_review"]["matched_ineligible"] == []
        assert after["auto_review"]["amount"] is None
        assert after["auto_review_status"] == "needs_review"
        assert "saved expense" in after["auto_review_notes"]


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
