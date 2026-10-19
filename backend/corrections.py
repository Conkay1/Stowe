import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import func, inspect

from backend.models import CustodianDistribution, ExpenseCorrection, Reimbursement, ReimbursementLineItem
from backend.money import cents


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def record(row):
    result = {}
    for column in inspect(type(row)).columns:
        value = getattr(row, column.key)
        result[column.key] = value.isoformat() if hasattr(value, "isoformat") else value
    return result


def snapshot(db, expense):
    items = db.query(ReimbursementLineItem).filter_by(expense_id=expense.id).order_by(ReimbursementLineItem.id).all()
    pull_ids = sorted({item.reimbursement_id for item in items})
    pulls = db.query(Reimbursement).filter(Reimbursement.id.in_(pull_ids)).order_by(Reimbursement.id).all()
    distributions = db.query(CustodianDistribution).filter(
        CustodianDistribution.reimbursement_id.in_(pull_ids),
    ).order_by(CustodianDistribution.id).all()
    values = record(expense)
    for key in ("auto_review_status", "auto_review_notes", "auto_review_metadata"):
        values.pop(key)
    return {
        "expense": values,
        "line_items": [record(item) for item in items],
        "pulls": [record(pull) for pull in pulls],
        "distributions": [record(distribution) for distribution in distributions],
        "receipts": [record(receipt) for receipt in sorted(expense.receipts, key=lambda r: r.id)],
    }


def revision(db, state):
    last_id = db.query(func.max(ExpenseCorrection.id)).filter_by(expense_id=state["expense"]["id"]).scalar()
    return hashlib.sha256(canonical([state, last_id]).encode()).hexdigest()


def has_corrections(db, expense_id):
    return db.query(ExpenseCorrection.id).filter_by(expense_id=expense_id).first() is not None


def validate_correction(state, amount):
    expense = state["expense"]
    if any(cents(item["covered_amount"]) <= 0 for item in state["line_items"]):
        raise HTTPException(409, "Existing reimbursement coverage is invalid and needs reconciliation first.")
    if not state["line_items"] and (
        expense["reimbursed"] or expense["reimbursed_date"] or expense["reimbursement_id"]
    ):
        raise HTTPException(409, "Record verified reimbursement coverage before correcting a manually reimbursed expense.")
    if cents(expense["amount"]) == cents(amount):
        raise HTTPException(422, "The corrected amount must differ from the current amount.")


def correction_out(row):
    return {
        "id": row.id, "expense_id": row.expense_id, "operation_id": row.operation_id,
        "reason": row.reason, "before": json.loads(row.before_json),
        "after": json.loads(row.after_json), "created_at": row.created_at,
    }
