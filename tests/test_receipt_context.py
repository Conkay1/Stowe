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
        assert after["auto_review"]["amount"] == 25.00
