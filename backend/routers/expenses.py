import csv
import io
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import HSAExpense, Receipt
from backend.schemas import ExpenseCreate, ExpenseOut, ExpenseUpdate, LedgerYear, ReceiptOut, VaultSummary
from config import HSA_CATEGORIES, RECEIPTS_DIR

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


# ── Categories ────────────────────────────────────────────────────────────────

@router.get("/categories")
def list_categories():
    return HSA_CATEGORIES


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
    return q.order_by(HSAExpense.date.desc()).all()


@router.post("/expenses", response_model=ExpenseOut, status_code=201)
def create_expense(body: ExpenseCreate, db: Session = Depends(get_db)):
    expense = HSAExpense(**body.model_dump())
    db.add(expense)
    db.commit()
    db.refresh(expense)
    return expense


@router.get("/expenses/{expense_id}", response_model=ExpenseOut)
def get_expense(expense_id: int, db: Session = Depends(get_db)):
    expense = db.get(HSAExpense, expense_id)
    if not expense:
        raise HTTPException(404, "Expense not found")
    return expense


@router.put("/expenses/{expense_id}", response_model=ExpenseOut)
def update_expense(expense_id: int, body: ExpenseUpdate, db: Session = Depends(get_db)):
    expense = db.get(HSAExpense, expense_id)
    if not expense:
        raise HTTPException(404, "Expense not found")

    data = body.model_dump(exclude_unset=True)

    if "reimbursed" in data:
        if data["reimbursed"] is True and "reimbursed_date" not in data:
            data["reimbursed_date"] = date.today()
        elif data["reimbursed"] is False:
            data.setdefault("reimbursed_date", None)  # clear date when un-reimbursing

    for k, v in data.items():
        setattr(expense, k, v)

    db.commit()
    db.refresh(expense)
    return expense


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

    unreimbursed = [e for e in expenses if not e.reimbursed]
    reimbursed   = [e for e in expenses if e.reimbursed]

    total_unreimbursed = sum(e.amount for e in unreimbursed)
    total_reimbursed   = sum(e.amount for e in reimbursed)

    unreimbursed_with_receipts = sum(1 for e in unreimbursed if e.receipts)
    receipt_pct = (
        round(unreimbursed_with_receipts / len(unreimbursed) * 100, 1)
        if unreimbursed else 0.0
    )

    return VaultSummary(
        total_unreimbursed=round(total_unreimbursed, 2),
        total_reimbursed=round(total_reimbursed, 2),
        count_unreimbursed=len(unreimbursed),
        count_reimbursed=len(reimbursed),
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


@router.get("/annual-ledger", response_model=list[LedgerYear])
def get_annual_ledger(db: Session = Depends(get_db)):
    expenses = db.query(HSAExpense).all()

    by_year: dict[int, list[HSAExpense]] = {}
    for e in expenses:
        yr = e.date.year
        by_year.setdefault(yr, []).append(e)

    result = []
    for year in sorted(by_year.keys(), reverse=True):
        items = by_year[year]
        total = sum(e.amount for e in items)
        reimb = sum(e.amount for e in items if e.reimbursed)
        unreimb = [e for e in items if not e.reimbursed]
        with_receipts = sum(1 for e in unreimb if e.receipts)
        pct = round(with_receipts / len(unreimb) * 100, 1) if unreimb else 100.0
        result.append(LedgerYear(
            year=year,
            count=len(items),
            total_amount=round(total, 2),
            total_reimbursed=round(reimb, 2),
            total_unreimbursed=round(total - reimb, 2),
            receipt_completeness_pct=pct,
        ))

    return result
