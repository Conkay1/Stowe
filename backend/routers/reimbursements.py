"""Reimbursement Pull Events — record HSA distributions covering one or more expense slices."""
from typing import Optional

from sqlalchemy import func
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import HSAAccount, HSAExpense, Reimbursement, ReimbursementLineItem
from backend.schemas import (
    DistributionOut,
    PullLineItemOut,
    ReimbursementCreate,
    ReimbursementListItem,
    ReimbursementOut,
    ReimbursementUpdate,
)

router = APIRouter(prefix="/api/v1", tags=["reimbursements"])


# ── Helpers ──────────────────────────────────────────────────────────────────

def _existing_covered(db: Session, expense_id: int, exclude_pull_id: Optional[int] = None) -> float:
    """Sum of covered_amount across all existing line items for an expense."""
    q = db.query(func.coalesce(func.sum(ReimbursementLineItem.covered_amount), 0.0))\
        .filter(ReimbursementLineItem.expense_id == expense_id)
    if exclude_pull_id is not None:
        q = q.filter(ReimbursementLineItem.reimbursement_id != exclude_pull_id)
    return float(q.scalar() or 0.0)


def _recompute_expense_state(db: Session, expense_id: int, *, was_pull_backed: bool = False) -> None:
    """Set `reimbursed` / `reimbursed_date` based on this expense's line items.

    - If line items fully cover the expense (within 1¢), mark reimbursed and use the latest pull date.
    - If line items exist but partial, mark not-reimbursed.
    - If no line items remain and `was_pull_backed=True` (i.e. caller just removed coverage),
      clear the flags so the expense returns to the vault.
    - If no line items exist and the caller didn't just remove backing, leave existing values
      alone (preserves the manual "Mark as reimbursed" override).
    """
    expense = db.get(HSAExpense, expense_id)
    if not expense:
        return

    line_items = db.query(ReimbursementLineItem)\
        .filter(ReimbursementLineItem.expense_id == expense_id).all()

    if not line_items:
        if was_pull_backed:
            expense.reimbursed = False
            expense.reimbursed_date = None
        expense.reimbursement_id = None
        return

    total_covered = sum(li.covered_amount for li in line_items)
    if total_covered + 0.01 >= expense.amount:
        expense.reimbursed = True
        expense.reimbursed_date = max(li.reimbursement.date for li in line_items)
        most_recent = max(line_items, key=lambda li: li.reimbursement.date)
        expense.reimbursement_id = most_recent.reimbursement_id
    else:
        expense.reimbursed = False
        expense.reimbursed_date = None
        expense.reimbursement_id = None


def _line_item_out(li: ReimbursementLineItem) -> PullLineItemOut:
    e = li.expense
    return PullLineItemOut(
        id=li.id,
        expense_id=e.id,
        covered_amount=round(li.covered_amount, 2),
        expense_amount=round(e.amount, 2),
        merchant=e.merchant,
        date=e.date,
        category=e.category,
        receipt_count=len(e.receipts),
    )


def _to_list_item(pull: Reimbursement) -> ReimbursementListItem:
    return ReimbursementListItem(
        id=pull.id,
        date=pull.date,
        reference=pull.reference,
        notes=pull.notes,
        expense_count=len(pull.line_items),
        total_amount=round(sum(li.covered_amount for li in pull.line_items), 2),
        account_id=pull.account_id,
        account_name=pull.account.name if pull.account else None,
        created_at=pull.created_at,
    )


def _to_full(pull: Reimbursement) -> ReimbursementOut:
    base = _to_list_item(pull)
    return ReimbursementOut(
        **base.model_dump(),
        line_items=[
            _line_item_out(li)
            for li in sorted(pull.line_items, key=lambda li: li.expense.date, reverse=True)
        ],
        distribution=DistributionOut.model_validate(pull.distribution) if pull.distribution else None,
    )


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/reimbursements", response_model=ReimbursementOut, status_code=201)
def create_reimbursement(body: ReimbursementCreate, db: Session = Depends(get_db)):
    expense_ids = [li.expense_id for li in body.line_items]

    expenses = (
        db.query(HSAExpense)
        .filter(HSAExpense.id.in_(expense_ids))
        .all()
    )
    by_id = {e.id: e for e in expenses}

    missing = [eid for eid in expense_ids if eid not in by_id]
    if missing:
        raise HTTPException(400, f"Unknown expense ID(s): {missing}")

    # Per-expense overflow check: existing covered + new covered <= amount + 1¢.
    overflows: list[str] = []
    manually_reimbursed: list[int] = []
    for li in body.line_items:
        e = by_id[li.expense_id]
        existing = _existing_covered(db, e.id)
        if existing == 0 and e.reimbursed:
            # Manually marked reimbursed without any pull backing — block.
            manually_reimbursed.append(e.id)
            continue
        if existing + li.covered_amount > e.amount + 0.01:
            remaining = round(max(e.amount - existing, 0), 2)
            overflows.append(
                f"expense {e.id} ({e.merchant}): only ${remaining:.2f} remaining, "
                f"requested ${li.covered_amount:.2f}"
            )

    if manually_reimbursed:
        raise HTTPException(
            400,
            f"Expense(s) already manually marked reimbursed: {manually_reimbursed}. "
            "Unmark them first if you want to back them with a pull.",
        )
    if overflows:
        raise HTTPException(400, "Pull would over-cover: " + "; ".join(overflows))

    pull = Reimbursement(
        date=body.date,
        reference=body.reference,
        notes=body.notes,
        total_amount=round(body.total_amount, 2),
    )
    db.add(pull)
    db.flush()  # assign pull.id

    for li in body.line_items:
        db.add(ReimbursementLineItem(
            reimbursement_id=pull.id,
            expense_id=li.expense_id,
            covered_amount=round(li.covered_amount, 2),
        ))
    db.flush()

    for eid in expense_ids:
        _recompute_expense_state(db, eid)

    db.commit()
    db.refresh(pull)
    return _to_full(pull)


@router.get("/reimbursements", response_model=list[ReimbursementListItem])
def list_reimbursements(db: Session = Depends(get_db)):
    pulls = (
        db.query(Reimbursement)
        .order_by(Reimbursement.date.desc(), Reimbursement.id.desc())
        .all()
    )
    return [_to_list_item(p) for p in pulls]


@router.get("/reimbursements/{pull_id}", response_model=ReimbursementOut)
def get_reimbursement(pull_id: int, db: Session = Depends(get_db)):
    pull = db.get(Reimbursement, pull_id)
    if not pull:
        raise HTTPException(404, "Pull not found")
    return _to_full(pull)


@router.put("/reimbursements/{pull_id}", response_model=ReimbursementOut)
def update_reimbursement(pull_id: int, body: ReimbursementUpdate, db: Session = Depends(get_db)):
    """Partial update of a Pull. Currently only `account_id` is editable."""
    pull = db.get(Reimbursement, pull_id)
    if not pull:
        raise HTTPException(404, "Pull not found")

    data = body.model_dump(exclude_unset=True)

    if "account_id" in data:
        new_account_id = data["account_id"]
        if new_account_id is not None:
            account = db.get(HSAAccount, new_account_id)
            if not account:
                raise HTTPException(400, f"Unknown account_id: {new_account_id}")
        pull.account_id = new_account_id

    db.commit()
    db.refresh(pull)
    return _to_full(pull)


@router.delete("/reimbursements/{pull_id}", status_code=204)
def delete_reimbursement(pull_id: int, db: Session = Depends(get_db)):
    pull = db.get(Reimbursement, pull_id)
    if not pull:
        raise HTTPException(404, "Pull not found")

    affected_expense_ids = [li.expense_id for li in pull.line_items]

    db.delete(pull)  # cascades to line items
    db.flush()

    for eid in affected_expense_ids:
        _recompute_expense_state(db, eid, was_pull_backed=True)

    db.commit()
