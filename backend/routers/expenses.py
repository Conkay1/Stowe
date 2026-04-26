import csv
import io
import re
import uuid
import zipfile
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import HSAExpense, Receipt, ReimbursementLineItem
from backend.schemas import ExpenseCreate, ExpenseOut, ExpenseUpdate, LedgerYear, ReceiptOut, VaultSummary
from config import DATABASE_PATH, HSA_CATEGORIES, RECEIPTS_DIR

router = APIRouter(prefix="/api/v1", tags=["expenses"])

MAX_RECEIPT_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_RECEIPT_MIMES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
    "application/pdf",
}
ALLOWED_RECEIPT_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".pdf"}


def _sanitize_filename(name: str) -> str:
    return re.sub(r"[^\w.\-]", "_", Path(name).name)[:200]


def _coverage_map(db: Session, expense_ids: list[int]) -> dict[int, tuple[float, int]]:
    """Return {expense_id: (covered_amount, pull_count)} for the given expenses."""
    if not expense_ids:
        return {}
    rows = (
        db.query(
            ReimbursementLineItem.expense_id,
            func.coalesce(func.sum(ReimbursementLineItem.covered_amount), 0.0),
            func.count(func.distinct(ReimbursementLineItem.reimbursement_id)),
        )
        .filter(ReimbursementLineItem.expense_id.in_(expense_ids))
        .group_by(ReimbursementLineItem.expense_id)
        .all()
    )
    return {eid: (float(total or 0.0), int(count or 0)) for eid, total, count in rows}


def _to_expense_out(e: HSAExpense, coverage: dict[int, tuple[float, int]]) -> ExpenseOut:
    covered, pull_count = coverage.get(e.id, (0.0, 0))
    remaining = max(round(e.amount - covered, 2), 0.0)
    return ExpenseOut(
        id=e.id,
        merchant=e.merchant,
        date=e.date,
        amount=e.amount,
        category=e.category,
        notes=e.notes,
        reimbursed=e.reimbursed,
        reimbursed_date=e.reimbursed_date,
        covered_amount=round(covered, 2),
        remaining_amount=remaining,
        pull_count=pull_count,
        created_at=e.created_at,
        receipts=[ReceiptOut.model_validate(r) for r in e.receipts],
    )




# ── Expenses ──────────────────────────────────────────────────────────────────

@router.get("/expenses", response_model=list[ExpenseOut])
def list_expenses(
    reimbursed: Optional[bool] = None,
    year: Optional[int] = None,
    db: Session = Depends(get_db),
):
    q = db.query(HSAExpense)
    if reimbursed is not None:
        q = q.filter(HSAExpense.reimbursed == reimbursed)
    if year is not None:
        q = q.filter(func.strftime("%Y", HSAExpense.date) == str(year))
    expenses = q.order_by(HSAExpense.date.desc()).all()
    coverage = _coverage_map(db, [e.id for e in expenses])
    return [_to_expense_out(e, coverage) for e in expenses]


@router.post("/expenses", response_model=ExpenseOut, status_code=201)
def create_expense(body: ExpenseCreate, db: Session = Depends(get_db)):
    expense = HSAExpense(**body.model_dump())
    db.add(expense)
    db.commit()
    db.refresh(expense)
    return _to_expense_out(expense, {})


@router.get("/expenses/{expense_id}", response_model=ExpenseOut)
def get_expense(expense_id: int, db: Session = Depends(get_db)):
    expense = db.get(HSAExpense, expense_id)
    if not expense:
        raise HTTPException(404, "Expense not found")
    return _to_expense_out(expense, _coverage_map(db, [expense.id]))


@router.put("/expenses/{expense_id}", response_model=ExpenseOut)
def update_expense(expense_id: int, body: ExpenseUpdate, db: Session = Depends(get_db)):
    expense = db.get(HSAExpense, expense_id)
    if not expense:
        raise HTTPException(404, "Expense not found")

    data = body.model_dump(exclude_unset=True)

    # Block manual reimbursed-toggle when pull-backed coverage exists.
    has_coverage = (
        db.query(ReimbursementLineItem)
        .filter(ReimbursementLineItem.expense_id == expense_id)
        .first()
        is not None
    )
    if "reimbursed" in data and has_coverage:
        raise HTTPException(
            400,
            "This expense is backed by one or more pulls. Adjust by editing or undoing those pulls.",
        )

    if "reimbursed" in data:
        if data["reimbursed"] is True and "reimbursed_date" not in data:
            data["reimbursed_date"] = date.today()
        elif data["reimbursed"] is False:
            data.setdefault("reimbursed_date", None)

    for k, v in data.items():
        setattr(expense, k, v)

    db.commit()
    db.refresh(expense)
    return _to_expense_out(expense, _coverage_map(db, [expense.id]))


@router.delete("/expenses/{expense_id}", status_code=204)
def delete_expense(expense_id: int, db: Session = Depends(get_db)):
    expense = db.get(HSAExpense, expense_id)
    if not expense:
        raise HTTPException(404, "Expense not found")

    for receipt in expense.receipts:
        f = RECEIPTS_DIR / receipt.filename
        if f.exists():
            f.unlink()

    db.delete(expense)
    db.commit()


# ── Receipts ──────────────────────────────────────────────────────────────────

@router.post("/expenses/{expense_id}/receipts", response_model=ReceiptOut, status_code=201)
async def upload_receipt(
    expense_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    expense = db.get(HSAExpense, expense_id)
    if not expense:
        raise HTTPException(404, "Expense not found")

    original = file.filename or "receipt"
    safe_name = _sanitize_filename(original)
    ext = Path(safe_name).suffix.lower()

    if ext not in ALLOWED_RECEIPT_EXTS:
        raise HTTPException(400, f"Unsupported file type. Allowed: {', '.join(sorted(ALLOWED_RECEIPT_EXTS))}")
    if file.content_type and file.content_type not in ALLOWED_RECEIPT_MIMES:
        raise HTTPException(400, f"Unsupported content type: {file.content_type}")

    content = await file.read()
    if len(content) > MAX_RECEIPT_BYTES:
        raise HTTPException(413, f"Receipt exceeds {MAX_RECEIPT_BYTES // (1024 * 1024)} MB limit")
    if len(content) == 0:
        raise HTTPException(400, "Empty file")

    unique_name = f"{uuid.uuid4().hex}{ext}"
    dest = RECEIPTS_DIR / unique_name
    dest.write_bytes(content)

    receipt = Receipt(
        expense_id=expense_id,
        filename=unique_name,
        original_filename=original,
        file_type=file.content_type,
        file_size_bytes=len(content),
    )
    db.add(receipt)
    db.commit()
    db.refresh(receipt)
    return receipt


@router.delete("/receipts/{receipt_id}", status_code=204)
def delete_receipt(receipt_id: int, db: Session = Depends(get_db)):
    receipt = db.get(Receipt, receipt_id)
    if not receipt:
        raise HTTPException(404, "Receipt not found")

    f = RECEIPTS_DIR / receipt.filename
    if f.exists():
        f.unlink()

    db.delete(receipt)
    db.commit()


@router.get("/receipts/{receipt_id}/file")
def get_receipt_file(receipt_id: int, db: Session = Depends(get_db)):
    receipt = db.get(Receipt, receipt_id)
    if not receipt:
        raise HTTPException(404, "Receipt not found")

    f = RECEIPTS_DIR / receipt.filename
    if not f.exists():
        raise HTTPException(404, "File not found on disk")

    return FileResponse(
        str(f),
        media_type=receipt.file_type or "application/octet-stream",
        filename=receipt.original_filename or receipt.filename,
    )


# ── Summary & Ledger ──────────────────────────────────────────────────────────

@router.get("/summary", response_model=VaultSummary)
def get_summary(db: Session = Depends(get_db)):
    expenses = db.query(HSAExpense).all()
    coverage = _coverage_map(db, [e.id for e in expenses])

    total_unreimbursed = 0.0
    total_reimbursed   = 0.0
    count_unreimbursed = 0
    count_reimbursed   = 0
    unreimb_with_receipts = 0

    for e in expenses:
        covered, _ = coverage.get(e.id, (0.0, 0))
        # Manual override: if flagged reimbursed without any coverage, treat as fully reimbursed.
        if e.reimbursed and covered == 0:
            total_reimbursed += e.amount
            count_reimbursed += 1
            continue

        remaining = max(e.amount - covered, 0.0)
        total_unreimbursed += remaining
        total_reimbursed += min(covered, e.amount)
        if remaining > 0.005:
            count_unreimbursed += 1
            if e.receipts:
                unreimb_with_receipts += 1
        else:
            count_reimbursed += 1

    receipt_pct = (
        round(unreimb_with_receipts / count_unreimbursed * 100, 1)
        if count_unreimbursed else 0.0
    )

    return VaultSummary(
        total_unreimbursed=round(total_unreimbursed, 2),
        total_reimbursed=round(total_reimbursed, 2),
        count_unreimbursed=count_unreimbursed,
        count_reimbursed=count_reimbursed,
        receipt_completeness_pct=receipt_pct,
    )


@router.get("/export/csv")
def export_csv(year: Optional[int] = None, db: Session = Depends(get_db)):
    q = db.query(HSAExpense)
    if year is not None:
        q = q.filter(func.strftime("%Y", HSAExpense.date) == str(year))
    expenses = q.order_by(HSAExpense.date.asc()).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Date", "Merchant", "Category", "Amount",
        "Reimbursed", "Reimbursed Date", "Receipt Count", "Receipt Filenames", "Notes",
    ])
    for e in expenses:
        writer.writerow([
            e.date.isoformat(),
            e.merchant,
            e.category,
            f"{e.amount:.2f}",
            "Yes" if e.reimbursed else "No",
            e.reimbursed_date.isoformat() if e.reimbursed_date else "",
            len(e.receipts),
            "; ".join(r.original_filename or r.filename for r in e.receipts),
            (e.notes or "").replace("\n", " ").strip(),
        ])

    buf.seek(0)
    filename = f"stowe-ledger{f'-{year}' if year else ''}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export/zip")
def export_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if DATABASE_PATH.exists():
            zf.write(DATABASE_PATH, "stowe.db")
        if RECEIPTS_DIR.exists():
            for f in RECEIPTS_DIR.iterdir():
                if f.is_file():
                    zf.write(f, f"receipts/{f.name}")
    buf.seek(0)
    filename = f"stowe-backup-{date.today()}.zip"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/annual-ledger", response_model=list[LedgerYear])
def get_annual_ledger(db: Session = Depends(get_db)):
    expenses = db.query(HSAExpense).all()
    coverage = _coverage_map(db, [e.id for e in expenses])

    by_year: dict[int, list[HSAExpense]] = {}
    for e in expenses:
        by_year.setdefault(e.date.year, []).append(e)

    result = []
    for year in sorted(by_year.keys(), reverse=True):
        items = by_year[year]
        total = sum(e.amount for e in items)

        reimb = 0.0
        unreimb_count = 0
        unreimb_with_receipts = 0
        for e in items:
            covered, _ = coverage.get(e.id, (0.0, 0))
            if e.reimbursed and covered == 0:
                reimb += e.amount
                continue
            reimb += min(covered, e.amount)
            remaining = max(e.amount - covered, 0.0)
            if remaining > 0.005:
                unreimb_count += 1
                if e.receipts:
                    unreimb_with_receipts += 1

        pct = round(unreimb_with_receipts / unreimb_count * 100, 1) if unreimb_count else 100.0
        result.append(LedgerYear(
            year=year,
            count=len(items),
            total_amount=round(total, 2),
            total_reimbursed=round(reimb, 2),
            total_unreimbursed=round(max(total - reimb, 0.0), 2),
            receipt_completeness_pct=pct,
        ))

    return result
