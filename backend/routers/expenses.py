import csv
import io
import json
import logging
import os
import tempfile
import unicodedata
import uuid
import zipfile
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from backend import corrections, db as db_module, ocr
from backend.db import begin_financial_write, get_db
from backend.eligibility import analyze_text
from backend.models import HSAExpense, Receipt, ReimbursementLineItem
from backend.money import cents
from backend.schemas import (
    ExpenseCreate, ExpenseOut, ExpenseUpdate, LedgerYear,
    ReceiptAnalysisOut, ReceiptOut, VaultSummary,
)
from config import DATABASE_PATH, HSA_CATEGORIES, RECEIPTS_DIR

logger = logging.getLogger("stowe.review")

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
# Spreadsheet apps treat a leading character in this set as a formula.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _display_filename(name: str) -> str:
    """Receipt label stored for display and export.

    Control characters and path separators are removed. The extension allowlist
    is applied to the raw upload name before this runs, so cleanup cannot change
    which final suffix is accepted.
    """
    cleaned = "".join(
        ch for ch in name
        if ch not in "/\\" and unicodedata.category(ch) != "Cc"
    )
    return cleaned or "receipt"


def _csv_safe(value) -> str:
    """Prefix formula-like cells so spreadsheet apps import them as text."""
    text = "" if value is None else str(value)
    if text.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + text
    return text


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
    remaining = 0.0 if e.reimbursed and not pull_count else max(cents(e.amount) - cents(covered), 0) / 100
    overcovered = max(cents(covered) - cents(e.amount), 0) / 100
    # Parse the stored auto-review JSON into a typed object. Guarded so a stale or
    # malformed blob never 500s a list call.
    auto_review = None
    if e.auto_review_metadata:
        try:
            auto_review = ReceiptAnalysisOut(**json.loads(e.auto_review_metadata))
        except Exception:
            auto_review = None
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
        overcovered_amount=overcovered,
        reconciliation_required=overcovered > 0,
        correction_count=len(e.corrections),
        auto_review_status=e.auto_review_status or "not_analyzed",
        auto_review_notes=e.auto_review_notes,
        auto_review=auto_review,
        created_at=e.created_at,
        receipts=[ReceiptOut.model_validate(r) for r in e.receipts],
    )


def _read_validated_upload(file: UploadFile, content: bytes) -> tuple[bytes, str, str]:
    """Validate an upload's name / content-type / size.

    Returns (content, ext, display_name). The extension comes from the raw
    filename's final suffix. The returned name is the stored display label.
    Raises the same HTTPExceptions as the receipt upload path. `content` is passed
    in so the caller owns the (async) read — lets this be shared by both the
    persisting upload endpoint and the ephemeral analyze endpoint.
    """
    raw_name = file.filename or "receipt"
    ext = Path(raw_name).suffix.lower()
    if ext not in ALLOWED_RECEIPT_EXTS:
        raise HTTPException(400, f"Unsupported file type. Allowed: {', '.join(sorted(ALLOWED_RECEIPT_EXTS))}")
    if file.content_type and file.content_type not in ALLOWED_RECEIPT_MIMES:
        raise HTTPException(400, f"Unsupported content type: {file.content_type}")
    if len(content) > MAX_RECEIPT_BYTES:
        raise HTTPException(413, f"Receipt exceeds {MAX_RECEIPT_BYTES // (1024 * 1024)} MB limit")
    if len(content) == 0:
        raise HTTPException(400, "Empty file")
    return content, ext, _display_filename(raw_name)


def _review_expense(db: Session, expense: HSAExpense) -> None:
    """OCR every receipt on `expense`, classify, and persist the auto-review columns.

    Best-effort and defensive: any OCR/Vision/JSON failure is swallowed and logged
    so a flaky analysis never breaks the request that triggered it.
    """
    try:
        expense_id = expense.id
        receipts = [(r.id, r.filename) for r in expense.receipts]
        previous_amount = expense.amount
        previous_date = expense.date
        previous_status = expense.auto_review_status
        previous_notes = expense.auto_review_notes
        previous_metadata = expense.auto_review_metadata
        db.rollback()
        texts: list[str] = []
        method = "none"
        image_notice = None
        for _receipt_id, filename in receipts:
            f = RECEIPTS_DIR / filename
            if not f.exists():
                continue
            text, m = ocr.extract_text(f)
            if text.strip():
                texts.append(text)
            elif f.suffix.lower() in ocr.IMAGE_EXTS:
                image_notice = ocr.image_ocr_notice() or (
                    "Image recognition did not return readable text. Try Analyze again, "
                    "or verify this receipt manually."
                )
            if m != "none":
                method = m
        result = analyze_text("\n\n".join(texts))   # handles "" → not_analyzed
        if image_notice:
            result["status"] = "needs_review" if texts else "not_analyzed"
            result["confidence"] = "low"
            result["notes"] = image_notice
        result["method"] = method
        conflicts = []
        if result["amount"] is not None and cents(result["amount"]) != cents(previous_amount):
            conflicts.append("amount")
        if result["date"] is not None and result["date"] != previous_date.isoformat():
            conflicts.append("date")
        if conflicts:
            for field in conflicts:
                result[field] = None
            result["status"] = "needs_review"
            result["confidence"] = "low"
            result["notes"] = (
                f"Review suggested: extracted {' and '.join(conflicts)} do not match the "
                "saved expense; conflicting hints were omitted. " + result["notes"]
            )
        receipt_count = select(func.count(Receipt.id)).where(
            Receipt.expense_id == expense_id,
        ).scalar_subquery()
        matching_count = select(func.count(Receipt.id)).where(
            Receipt.expense_id == expense_id, tuple_(Receipt.id, Receipt.filename).in_(receipts),
        ).scalar_subquery()
        db.query(HSAExpense).filter(
            HSAExpense.id == expense_id,
            HSAExpense.amount == previous_amount,
            HSAExpense.date == previous_date,
            HSAExpense.auto_review_status == previous_status,
            HSAExpense.auto_review_notes == previous_notes,
            HSAExpense.auto_review_metadata == previous_metadata,
            receipt_count == len(receipts), matching_count == len(receipts),
        ).update({
            HSAExpense.auto_review_status: result["status"],
            HSAExpense.auto_review_notes: result["notes"],
            HSAExpense.auto_review_metadata: json.dumps(result),
        }, synchronize_session=False)
        db.commit()
    except Exception:
        logger.exception("auto-review failed for expense %s", getattr(expense, "id", "?"))
        db.rollback()



def _review_expense_after_upload(expense_id: int) -> None:
    try:
        with db_module.SessionLocal() as db:
            expense = db.get(HSAExpense, expense_id)
            if expense is not None:
                _review_expense(db, expense)
    except Exception:
        logger.exception("background auto-review failed for expense %s", expense_id)


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
    begin_financial_write(db)
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
    if "amount" in data:
        if data["amount"] is None:
            raise HTTPException(422, "Amount cannot be null.")
        if cents(data["amount"]) != cents(expense.amount) and (
            has_coverage or expense.reimbursed or expense.reimbursed_date
            or expense.reimbursement_id or corrections.has_corrections(db, expense_id)
        ):
            raise HTTPException(409, "Use an audited expense correction to change a cost with reimbursement history.")
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
    begin_financial_write(db)
    expense = db.get(HSAExpense, expense_id)
    if not expense:
        raise HTTPException(404, "Expense not found")

    if corrections.has_corrections(db, expense_id) or expense.line_items or expense.reimbursed:
        raise HTTPException(409, "This expense has financial history. Preserve it and use an audited correction.")

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
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    expense = db.get(HSAExpense, expense_id)
    if not expense:
        raise HTTPException(404, "Expense not found")

    content, ext, original = _read_validated_upload(file, await file.read())

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
    background_tasks.add_task(_review_expense_after_upload, expense_id)
    return receipt


@router.post("/receipts/analyze", response_model=ReceiptAnalysisOut)
def analyze_receipt(file: UploadFile = File(...)):
    """OCR + classify an uploaded file WITHOUT persisting anything.

    Used by the Add-expense page to pre-fill the form before the expense is saved.
    The bytes are written to a private OS temp file (local, cleaned up immediately)
    because both pypdf and Vision need a real filesystem path.
    """
    content, ext, _original = _read_validated_upload(file, file.file.read())

    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    try:
        tmp.write(content)
        tmp.flush()
        tmp.close()
        text, method = ocr.extract_text(Path(tmp.name))
        result = analyze_text(text)
        if not text.strip() and ext in ocr.IMAGE_EXTS:
            result["notes"] = ocr.image_ocr_notice() or (
                "Image recognition did not return readable text. Try Analyze again, "
                "or verify this receipt manually."
            )
        result["method"] = method
        return ReceiptAnalysisOut(**result)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


@router.post("/expenses/{expense_id}/analyze", response_model=ExpenseOut)
def reanalyze_expense(expense_id: int, db: Session = Depends(get_db)):
    """Re-run the auto-review over an expense's existing receipts and persist it."""
    expense = db.get(HSAExpense, expense_id)
    if not expense:
        raise HTTPException(404, "Expense not found")
    _review_expense(db, expense)
    db.refresh(expense)
    return _to_expense_out(expense, _coverage_map(db, [expense.id]))


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
def get_receipt_file(receipt_id: int, preview: bool = False, db: Session = Depends(get_db)):
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
        content_disposition_type=(
            "inline" if preview and receipt.file_type in ALLOWED_RECEIPT_MIMES else "attachment"
        ),
        headers={"X-Content-Type-Options": "nosniff"},
    )


# ── Summary & Ledger ──────────────────────────────────────────────────────────

@router.get("/summary", response_model=VaultSummary)
def get_summary(db: Session = Depends(get_db)):
    expenses = db.query(HSAExpense).all()
    coverage = _coverage_map(db, [e.id for e in expenses])

    total_unreimbursed = 0.0
    total_reimbursed   = 0.0
    total_overcovered = 0.0
    count_unreimbursed = 0
    count_reimbursed   = 0
    unreimb_with_receipts = 0

    for e in expenses:
        covered, _ = coverage.get(e.id, (0.0, 0))
        total_overcovered += max(cents(covered) - cents(e.amount), 0) / 100
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
        total_overcovered=round(total_overcovered, 2),
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
        filenames = "; ".join(
            _csv_safe(r.original_filename or r.filename) for r in e.receipts
        )
        writer.writerow([
            _csv_safe(e.date.isoformat()),
            _csv_safe(e.merchant),
            _csv_safe(e.category),
            _csv_safe(f"{e.amount:.2f}"),
            _csv_safe("Yes" if e.reimbursed else "No"),
            _csv_safe(e.reimbursed_date.isoformat() if e.reimbursed_date else ""),
            _csv_safe(len(e.receipts)),
            _csv_safe(filenames),
            _csv_safe((e.notes or "").replace("\n", " ").strip()),
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
