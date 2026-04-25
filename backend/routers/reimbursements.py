"""Reimbursement Pull Events — record HSA distributions covering one or more expenses."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import HSAExpense, Reimbursement
from backend.schemas import (
    ReimbursementCreate,
    ReimbursementListItem,
    ReimbursementOut,
)

router = APIRouter(prefix="/api/v1", tags=["reimbursements"])


def _to_list_item(pull: Reimbursement) -> ReimbursementListItem:
    return ReimbursementListItem(
        id=pull.id,
        date=pull.date,
        reference=pull.reference,
        notes=pull.notes,
        expense_count=len(pull.expenses),
        total_amount=round(sum(e.amount for e in pull.expenses), 2),
        created_at=pull.created_at,
    )


def _to_full(pull: Reimbursement) -> ReimbursementOut:
    base = _to_list_item(pull)
    return ReimbursementOut(
        **base.model_dump(),
        expenses=sorted(pull.expenses, key=lambda e: e.date, reverse=True),
    )


@router.post("/reimbursements", response_model=ReimbursementOut, status_code=201)
def create_reimbursement(body: ReimbursementCreate, db: Session = Depends(get_db)):
    expenses = (
        db.query(HSAExpense)
        .filter(HSAExpense.id.in_(body.expense_ids))
        .all()
    )

    found_ids = {e.id for e in expenses}
    missing = [eid for eid in body.expense_ids if eid not in found_ids]
    if missing:
        raise HTTPException(400, f"Unknown expense ID(s): {missing}")

    already_reimbursed = [e.id for e in expenses if e.reimbursed]
    if already_reimbursed:
        raise HTTPException(
            400,
            f"Expense(s) already reimbursed: {already_reimbursed}",
        )

    pull = Reimbursement(
        date=body.date,
        reference=body.reference,
        notes=body.notes,
    )
    db.add(pull)
    db.flush()  # assign pull.id

    for e in expenses:
        e.reimbursed = True
        e.reimbursed_date = body.date
        e.reimbursement_id = pull.id

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


@router.delete("/reimbursements/{pull_id}", status_code=204)
def delete_reimbursement(pull_id: int, db: Session = Depends(get_db)):
    pull = db.get(Reimbursement, pull_id)
    if not pull:
        raise HTTPException(404, "Pull not found")

    # Unmark all linked expenses before deleting the pull row.
    for e in list(pull.expenses):
        e.reimbursed = False
        e.reimbursed_date = None
        e.reimbursement_id = None

    db.delete(pull)
    db.commit()
