import hashlib

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend import corrections
from backend.db import begin_financial_write, get_db
from backend.models import ExpenseCorrection, HSAExpense
from backend.money import cents
from backend.routers.reimbursements import _recompute_expense_state
from backend.schemas import ExpenseCorrectionIn, ExpenseCorrectionOut, ExpenseCorrectionPreviewIn, ExpenseCorrectionPreviewOut

router = APIRouter(prefix="/api/v1", tags=["expense corrections"])


def _expense(db, expense_id):
    expense = db.get(HSAExpense, expense_id)
    if expense is None:
        raise HTTPException(404, "Expense not found")
    return expense


@router.post("/expenses/{expense_id}/corrections/preview", response_model=ExpenseCorrectionPreviewOut)
def preview_correction(expense_id: int, body: ExpenseCorrectionPreviewIn, db: Session = Depends(get_db)):
    db.execute(text("BEGIN"))
    expense = _expense(db, expense_id)
    state = corrections.snapshot(db, expense)
    corrections.validate_correction(state, body.amount)
    covered = sum(cents(item["covered_amount"]) for item in state["line_items"])
    amount = cents(body.amount)
    return ExpenseCorrectionPreviewOut(
        expense_id=expense_id, revision=corrections.revision(db, state),
        before_amount=expense.amount, after_amount=float(body.amount), covered_amount=covered / 100,
        remaining_amount=max(amount - covered, 0) / 100,
        overcovered_amount=max(covered - amount, 0) / 100,
        reconciliation_required=covered > amount,
    )


@router.post("/expenses/{expense_id}/corrections", response_model=ExpenseCorrectionOut, status_code=201)
def apply_correction(expense_id: int, body: ExpenseCorrectionIn, response: Response, db: Session = Depends(get_db)):
    begin_financial_write(db)
    request_hash = hashlib.sha256(corrections.canonical({
        "expense_id": expense_id, "amount_cents": cents(body.amount),
        "reason": body.reason, "expected_revision": body.expected_revision,
    }).encode()).hexdigest()
    previous = db.query(ExpenseCorrection).filter_by(operation_id=str(body.operation_id)).first()
    if previous:
        if previous.request_hash != request_hash:
            raise HTTPException(409, "This operation ID was already used for a different correction.")
        response.status_code = 200
        return corrections.correction_out(previous)
    expense = _expense(db, expense_id)
    before = corrections.snapshot(db, expense)
    if corrections.revision(db, before) != body.expected_revision:
        raise HTTPException(409, "Expense or reimbursement evidence changed. Preview the correction again.")
    corrections.validate_correction(before, body.amount)
    try:
        expense.amount = float(body.amount)
        _recompute_expense_state(db, expense_id)
        db.flush()
        after = corrections.snapshot(db, expense)
        audit = ExpenseCorrection(
            expense_id=expense_id, operation_id=str(body.operation_id), request_hash=request_hash,
            reason=body.reason, before_json=corrections.canonical(before), after_json=corrections.canonical(after),
        )
        db.add(audit)
        db.commit()
        db.refresh(audit)
        return corrections.correction_out(audit)
    except Exception:
        db.rollback()
        raise


@router.get("/expenses/{expense_id}/corrections", response_model=list[ExpenseCorrectionOut])
def list_corrections(expense_id: int, db: Session = Depends(get_db)):
    _expense(db, expense_id)
    rows = db.query(ExpenseCorrection).filter_by(expense_id=expense_id).order_by(ExpenseCorrection.id).all()
    return [corrections.correction_out(row) for row in rows]
