import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest


def expense(client, amount=100):
    response = client.post("/api/v1/expenses", json={
        "merchant": "Synthetic Clinic", "date": "2025-01-10", "amount": amount,
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def pull(client, expense_id, amount):
    return client.post("/api/v1/reimbursements", json={
        "date": "2025-02-10", "total_amount": amount,
        "line_items": [{"expense_id": expense_id, "covered_amount": amount}],
    })


def proposal(client, expense_id, amount):
    body = {"amount": amount, "reason": "Later insurer adjustment; synthetic evidence"}
    response = client.post(f"/api/v1/expenses/{expense_id}/corrections/preview", json=body)
    assert response.status_code == 200, response.text
    return {**body, "expected_revision": response.json()["revision"], "operation_id": str(uuid4())}


def apply(client, expense_id, body):
    return client.post(f"/api/v1/expenses/{expense_id}/corrections", json=body)


def current(client, expense_id):
    return client.get(f"/api/v1/expenses/{expense_id}").json()


@pytest.mark.parametrize("history", ["partial", "full", "manual"])
def test_plain_amount_edit_cannot_rewrite_reimbursed_cost(client, history):
    eid = expense(client)
    if history == "manual":
        client.put(f"/api/v1/expenses/{eid}", json={"reimbursed": True})
    else:
        assert pull(client, eid, 40 if history == "partial" else 100).status_code == 201
    before = current(client, eid)
    response = client.put(f"/api/v1/expenses/{eid}", json={"amount": 70, "notes": "must not persist"})
    assert response.status_code == 409
    assert current(client, eid) == before
    assert client.put(f"/api/v1/expenses/{eid}", json={"amount": 100, "notes": "Metadata only"}).status_code == 200


def test_unmark_and_amount_edit_cannot_bypass_manual_guard(client):
    eid = expense(client)
    client.put(f"/api/v1/expenses/{eid}", json={"reimbursed": True})
    assert client.put(f"/api/v1/expenses/{eid}", json={"amount": 70, "reimbursed": False}).status_code == 409
    assert current(client, eid)["amount"] == 100


def test_unreimbursed_amount_edits_still_work(client):
    eid = expense(client)
    assert client.put(f"/api/v1/expenses/{eid}", json={"amount": 70}).status_code == 200


@pytest.mark.parametrize("covered,new_amount,remaining,excess,reimbursed", [
    (100, 70, 0, 30, True), (40, 70, 30, 0, False),
    (40, 40, 0, 0, True), (100, 120, 20, 0, False),
    (100, 0, 0, 100, True), (40, 40.01, 0.01, 0, False),
    (40, 39.99, 0, 0.01, True),
])
def test_correction_preserves_cash_and_receipts_and_audits_cost(client, covered, new_amount, remaining, excess, reimbursed):
    eid = expense(client)
    p = pull(client, eid, covered).json()
    account = client.post("/api/v1/accounts", json={"name": "Synthetic HSA", "custodian": "Test"}).json()
    aid = account["id"]
    imported = client.post(f"/api/v1/accounts/{aid}/imports/commit", json={
        "column_map": {"date": "date", "amount": "amount"},
        "rows": [{"date": "2025-02-10", "amount": covered, "custodian_ref": "SYNTHETIC-001"}],
    })
    assert imported.status_code == 200
    distributions = client.get(f"/api/v1/accounts/{aid}/distributions").json()
    assert distributions[0]["reimbursement_id"] == p["id"]
    original = b"%PDF-1.4 synthetic correction evidence"
    receipt = client.post(f"/api/v1/expenses/{eid}/receipts", files={"file": ("synthetic.pdf", original, "application/pdf")}).json()
    before = current(client, eid)
    request = proposal(client, eid, new_amount)
    assert current(client, eid) == before
    response = apply(client, eid, request)
    assert response.status_code == 201, response.text
    audit = response.json()
    assert audit["before"]["expense"]["amount"] == 100
    assert audit["after"]["expense"]["amount"] == new_amount
    assert audit["reason"] == request["reason"]
    for field in ("line_items", "pulls", "distributions", "receipts"):
        assert audit["before"][field] == audit["after"][field]
    after = current(client, eid)
    assert (after["amount"], after["covered_amount"], after["remaining_amount"], after["overcovered_amount"]) == (new_amount, covered, remaining, excess)
    assert after["reimbursed"] is reimbursed
    assert after["reconciliation_required"] is (excess > 0)
    assert after["receipts"] == before["receipts"]
    downloaded = client.get(f"/api/v1/receipts/{receipt['id']}/file").content
    assert hashlib.sha256(downloaded).digest() == hashlib.sha256(original).digest()
    assert client.get(f"/api/v1/accounts/{aid}/distributions").json() == distributions
    assert client.get(f"/api/v1/reimbursements/{p['id']}").json()["total_amount"] == covered
    summary = client.get("/api/v1/summary").json()
    assert summary["total_unreimbursed"] == remaining
    assert summary["total_overcovered"] == excess
    matched = client.get(f"/api/v1/accounts/{aid}/reconciliation").json()["matched"][0]
    assert matched["amount_difference"] == 0
    assert matched["reconciliation_required"] is (excess > 0)
    assert matched["overcovered_expense_ids"] == ([eid] if excess else [])
    if remaining == 0:
        assert pull(client, eid, 0.01).status_code == 400
    assert client.delete(f"/api/v1/reimbursements/{p['id']}").status_code == 409
    assert client.delete(f"/api/v1/expenses/{eid}").status_code == 409
    assert client.get(f"/api/v1/expenses/{eid}/corrections").json() == [audit]


def test_idempotent_retry_and_conflicting_operation_id(client):
    eid = expense(client)
    pull(client, eid, 100)
    request = proposal(client, eid, 70)
    first = apply(client, eid, request)
    assert first.status_code == 201
    repeat = apply(client, eid, request)
    assert repeat.status_code == 200
    assert repeat.json() == first.json()
    assert apply(client, eid, {**request, "amount": 60}).status_code == 409
    assert len(client.get(f"/api/v1/expenses/{eid}/corrections").json()) == 1


@pytest.mark.parametrize("change", ["metadata", "pull", "receipt", "correction"])
def test_stale_preview_cannot_overwrite_new_state(client, change):
    eid = expense(client)
    pull(client, eid, 40)
    request = proposal(client, eid, 70)
    if change == "metadata":
        client.put(f"/api/v1/expenses/{eid}", json={"notes": "Changed after preview"})
    elif change == "pull":
        pull(client, eid, 10)
    elif change == "receipt":
        client.post(f"/api/v1/expenses/{eid}/receipts", files={"file": ("new.pdf", b"%PDF-fake", "application/pdf")})
    else:
        assert apply(client, eid, proposal(client, eid, 80)).status_code == 201
    before = current(client, eid)
    assert apply(client, eid, request).status_code == 409
    assert current(client, eid) == before


@pytest.mark.parametrize("amount", [-1, "NaN", "Infinity", "1.001", None])
def test_invalid_cost_rejected_without_writes(client, amount):
    eid = expense(client)
    response = client.post(f"/api/v1/expenses/{eid}/corrections/preview", json={"amount": amount, "reason": "Synthetic adjustment"})
    assert response.status_code == 422
    assert current(client, eid)["amount"] == 100


def test_manual_reimbursement_needs_verified_coverage(client):
    eid = expense(client)
    client.put(f"/api/v1/expenses/{eid}", json={"reimbursed": True})
    response = client.post(f"/api/v1/expenses/{eid}/corrections/preview", json={"amount": 70, "reason": "Synthetic adjustment"})
    assert response.status_code == 409
    assert current(client, eid)["remaining_amount"] == 0


def test_correction_and_audit_roll_back_together(client):
    from sqlalchemy import event
    from backend.models import ExpenseCorrection

    eid = expense(client)
    pull(client, eid, 100)
    request = proposal(client, eid, 70)
    before = current(client, eid)

    def fail_audit(mapper, connection, target):
        raise RuntimeError("Synthetic audit storage failure")

    event.listen(ExpenseCorrection, "before_insert", fail_audit)
    try:
        with pytest.raises(RuntimeError, match="Synthetic audit storage failure"):
            apply(client, eid, request)
    finally:
        event.remove(ExpenseCorrection, "before_insert", fail_audit)
    assert current(client, eid) == before
    assert client.get(f"/api/v1/expenses/{eid}/corrections").json() == []


def test_correction_serializes_against_new_pull(client, monkeypatch):
    from backend import corrections

    eid = expense(client)
    pull(client, eid, 50)
    request = proposal(client, eid, 40)
    entered, release, pull_started = threading.Event(), threading.Event(), threading.Event()
    snapshot = corrections.snapshot

    def paused_snapshot(*args):
        result = snapshot(*args)
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return result

    def concurrent_pull():
        pull_started.set()
        return pull(client, eid, 20)

    monkeypatch.setattr(corrections, "snapshot", paused_snapshot)
    with ThreadPoolExecutor(max_workers=2) as pool:
        correction = pool.submit(apply, client, eid, request)
        try:
            assert entered.wait(2)
            competing = pool.submit(concurrent_pull)
            assert pull_started.wait(2)
            assert not competing.done()
        finally:
            release.set()
        assert correction.result(timeout=5).status_code == 201
        assert competing.result(timeout=5).status_code == 400
    assert current(client, eid)["covered_amount"] == 50


@pytest.mark.parametrize("difference", [0, 0.01, -0.01, 5])
def test_linked_cash_difference_is_explicit(client, difference):
    eid = expense(client)
    p = pull(client, eid, 100).json()
    aid = client.post("/api/v1/accounts", json={"name": "Synthetic HSA", "custodian": "Test"}).json()["id"]
    client.post(f"/api/v1/accounts/{aid}/imports/commit", json={
        "column_map": {"date": "date", "amount": "amount"},
        "rows": [{"date": "2025-02-10", "amount": 100 + difference}],
    })
    dist = client.get(f"/api/v1/accounts/{aid}/distributions").json()[0]
    client.post(f"/api/v1/distributions/{dist['id']}/match", json={"reimbursement_id": p["id"]})
    matched = client.get(f"/api/v1/accounts/{aid}/reconciliation").json()["matched"][0]
    assert matched["amount_difference"] == difference
    assert matched["reconciliation_required"] is (difference != 0)


def test_two_corrections_from_one_preview_cannot_both_apply(client):
    eid = expense(client)
    pull(client, eid, 100)
    first = proposal(client, eid, 70)
    second = {**first, "operation_id": str(uuid4()), "amount": 80}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda request: apply(client, eid, request), [first, second]))
    assert sorted(result.status_code for result in results) == [201, 409]
    audits = client.get(f"/api/v1/expenses/{eid}/corrections").json()
    assert len(audits) == 1
    assert current(client, eid)["amount"] == audits[0]["after"]["expense"]["amount"]


def test_schema_upgrade_preserves_existing_financial_records(client):
    from backend import db
    from backend.models import ExpenseCorrection

    eid = expense(client)
    p = pull(client, eid, 100).json()
    before = current(client, eid)
    ExpenseCorrection.__table__.drop(db.engine)
    db.init_db()
    db.init_db()
    assert current(client, eid) == before
    assert client.get(f"/api/v1/reimbursements/{p['id']}").json() == p
    assert apply(client, eid, proposal(client, eid, 70)).status_code == 201


def test_subsequent_corrections_remain_audited(client):
    eid = expense(client)
    first = apply(client, eid, proposal(client, eid, 70))
    assert first.status_code == 201
    assert client.put(f"/api/v1/expenses/{eid}", json={"amount": 60}).status_code == 409
    second = apply(client, eid, proposal(client, eid, 60))
    assert second.status_code == 201
    assert second.json()["before"]["expense"]["amount"] == 70
    assert client.get(f"/api/v1/expenses/{eid}/corrections").json() == [first.json(), second.json()]


@pytest.mark.parametrize("amount", [0.001, "NaN", "Infinity"])
def test_new_pull_cannot_create_zero_or_nonfinite_coverage(client, amount):
    eid = expense(client)
    assert pull(client, eid, amount).status_code == 422
    assert current(client, eid)["covered_amount"] == 0


def test_new_pull_total_must_equal_allocation_to_the_cent(client):
    eid = expense(client)
    response = client.post("/api/v1/reimbursements", json={
        "date": "2025-02-10", "total_amount": 40.01,
        "line_items": [{"expense_id": eid, "covered_amount": 40}],
    })
    assert response.status_code == 422


def test_preview_requires_a_reason_and_rejects_other_financial_writes(client):
    eid = expense(client)
    for body in ({"amount": 70, "reason": "  "}, {"amount": 70}, {"amount": 70, "reason": "Synthetic", "reimbursed": False}):
        assert client.post(f"/api/v1/expenses/{eid}/corrections/preview", json=body).status_code == 422


def test_reverting_a_correction_still_invalidates_old_preview(client):
    eid = expense(client)
    pull(client, eid, 100)
    old = proposal(client, eid, 60)
    assert apply(client, eid, proposal(client, eid, 70)).status_code == 201
    assert apply(client, eid, proposal(client, eid, 100)).status_code == 201
    assert apply(client, eid, old).status_code == 409


def test_duplicate_allocations_are_rejected_before_creating_a_pull(client):
    eid = expense(client)
    response = client.post("/api/v1/reimbursements", json={
        "date": "2025-02-10", "total_amount": 120,
        "line_items": [{"expense_id": eid, "covered_amount": 60}] * 2,
    })
    assert response.status_code == 422
    assert "duplicate expense_id" in response.text
    assert client.get("/api/v1/reimbursements").json() == []
    assert current(client, eid)["covered_amount"] == 0
