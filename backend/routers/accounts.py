"""HSA Accounts — custodian linking, CSV imports, reconciliation, balance snapshots."""
from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import (
    BalanceSnapshot,
    CustodianDistribution,
    HSAAccount,
    Reimbursement,
)
from backend.schemas import (
    AccountCreate,
    AccountOut,
    AccountUpdate,
    CSVCommitRequest,
    CSVCommitResult,
    CSVParseResult,
    DistributionMatchIn,
    DistributionOut,
    ReconcMatchedItem,
    ReconcUnmatchedPull,
    ReconcileAutoResult,
    ReconciliationResult,
    SnapshotIn,
    SnapshotOut,
    SnapshotUpdate,
)

router = APIRouter(prefix="/api/v1", tags=["accounts"])

MAX_CSV_BYTES = 10 * 1024 * 1024  # 10 MB — mirrors MAX_RECEIPT_BYTES.

# Auto-match heuristic constants.
MATCH_AMOUNT_TOLERANCE = 0.01
MATCH_DATE_WINDOW_DAYS = 5

# Header alias dict for fuzzy column-mapping suggestion. Lowercase keys; values are canonical fields.
HEADER_ALIASES: dict[str, str] = {
    "date": "date",
    "transaction date": "date",
    "trans date": "date",
    "posting date": "date",
    "post date": "date",
    "settlement date": "date",
    "value date": "date",
    "process date": "date",

    "amount": "amount",
    "amt": "amount",
    "withdrawal": "amount",
    "withdrawal amount": "amount",
    "distribution": "amount",
    "distribution amount": "amount",
    "debit": "amount",
    "credit": "amount",
    "transaction amount": "amount",

    "description": "description",
    "memo": "description",
    "details": "description",
    "transaction description": "description",
    "narration": "description",
    "payee": "description",

    "transaction id": "custodian_ref",
    "transaction number": "custodian_ref",
    "txn id": "custodian_ref",
    "reference": "custodian_ref",
    "reference number": "custodian_ref",
    "ref": "custodian_ref",
    "confirmation number": "custodian_ref",
    "check number": "custodian_ref",
}

CANONICAL_FIELDS = ("date", "amount", "description", "custodian_ref")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _account_or_404(db: Session, account_id: int) -> HSAAccount:
    acc = db.get(HSAAccount, account_id)
    if not acc:
        raise HTTPException(404, "Account not found")
    return acc


def _to_account_out(db: Session, acc: HSAAccount) -> AccountOut:
    pull_count = (
        db.query(func.count(Reimbursement.id))
        .filter(Reimbursement.account_id == acc.id)
        .scalar()
    ) or 0

    distribution_count = (
        db.query(func.count(CustodianDistribution.id))
        .filter(CustodianDistribution.account_id == acc.id)
        .scalar()
    ) or 0

    unmatched = (
        db.query(func.count(CustodianDistribution.id))
        .filter(
            CustodianDistribution.account_id == acc.id,
            CustodianDistribution.reimbursement_id.is_(None),
        )
        .scalar()
    ) or 0

    latest = (
        db.query(BalanceSnapshot)
        .filter(BalanceSnapshot.account_id == acc.id)
        .order_by(BalanceSnapshot.as_of_date.desc(), BalanceSnapshot.id.desc())
        .first()
    )

    return AccountOut(
        id=acc.id,
        name=acc.name,
        custodian=acc.custodian,
        account_mask=acc.account_mask,
        is_active=acc.is_active,
        notes=acc.notes,
        created_at=acc.created_at,
        pull_count=int(pull_count),
        distribution_count=int(distribution_count),
        unmatched_distribution_count=int(unmatched),
        cash_balance=round(latest.cash_balance, 2) if latest else 0.0,
        invested_balance=round(latest.invested_balance, 2) if latest else 0.0,
        last_snapshot_date=latest.as_of_date if latest else None,
        has_csv_column_map=bool(acc.csv_column_map),
    )


# ── Account CRUD ─────────────────────────────────────────────────────────────


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_db)):
    accs = (
        db.query(HSAAccount)
        .order_by(HSAAccount.is_active.desc(), HSAAccount.name.asc())
        .all()
    )
    return [_to_account_out(db, a) for a in accs]


@router.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(body: AccountCreate, db: Session = Depends(get_db)):
    acc = HSAAccount(
        name=body.name,
        custodian=body.custodian,
        account_mask=body.account_mask,
        notes=body.notes,
        is_active=True,
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return _to_account_out(db, acc)


@router.get("/accounts/{account_id}", response_model=AccountOut)
def get_account(account_id: int, db: Session = Depends(get_db)):
    acc = _account_or_404(db, account_id)
    return _to_account_out(db, acc)


@router.put("/accounts/{account_id}", response_model=AccountOut)
def update_account(account_id: int, body: AccountUpdate, db: Session = Depends(get_db)):
    acc = _account_or_404(db, account_id)
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(acc, k, v)
    db.commit()
    db.refresh(acc)
    return _to_account_out(db, acc)


@router.put("/accounts/{account_id}/deactivate", response_model=AccountOut)
def deactivate_account(account_id: int, db: Session = Depends(get_db)):
    acc = _account_or_404(db, account_id)
    acc.is_active = False
    db.commit()
    db.refresh(acc)
    return _to_account_out(db, acc)


@router.delete("/accounts/{account_id}", status_code=204)
def delete_account(account_id: int, db: Session = Depends(get_db)):
    acc = _account_or_404(db, account_id)

    pull_count = (
        db.query(func.count(Reimbursement.id))
        .filter(Reimbursement.account_id == acc.id)
        .scalar()
    ) or 0
    distrib_count = (
        db.query(func.count(CustodianDistribution.id))
        .filter(CustodianDistribution.account_id == acc.id)
        .scalar()
    ) or 0

    if pull_count or distrib_count:
        raise HTTPException(
            400,
            f"Cannot delete '{acc.name}' — {pull_count} pull(s) and "
            f"{distrib_count} distribution(s) still reference it. Deactivate instead.",
        )

    db.delete(acc)
    db.commit()


# ── CSV import ───────────────────────────────────────────────────────────────


def _decode_csv(raw: bytes) -> tuple[str, str]:
    """Return (decoded text, encoding label). Tries utf-8-sig first, then latin-1."""
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    # latin-1 should never fail (it accepts all bytes), but guard anyway.
    raise HTTPException(400, "Could not decode CSV — unsupported encoding.")


def _suggest_column_map(headers: list[str], stored_map_json: Optional[str]) -> dict[str, str]:
    """Return {original_header: canonical_field} for headers we can guess.

    Prefer a stored map (the account's last successful import) if every header in it
    is still present in the new file. Otherwise fall back to the alias dict + simple
    similarity scoring.
    """
    if stored_map_json:
        try:
            stored = json.loads(stored_map_json)
        except (json.JSONDecodeError, TypeError):
            stored = None
        if isinstance(stored, dict):
            # If the stored map's headers are all still in the file, reuse it verbatim.
            if all(h in headers for h in stored.keys()):
                return {h: stored[h] for h in headers if h in stored}

    out: dict[str, str] = {}
    for h in headers:
        key = h.strip().lower()
        if key in HEADER_ALIASES:
            out[h] = HEADER_ALIASES[key]
            continue
        # Try fuzzy match against alias keys; keep the best if it's reasonably close.
        best_field, best_score = None, 0.0
        for alias, field in HEADER_ALIASES.items():
            score = SequenceMatcher(None, key, alias).ratio()
            if score > best_score:
                best_score = score
                best_field = field
        if best_score >= 0.78 and best_field:
            out[h] = best_field
    return out


@router.post("/accounts/{account_id}/imports/parse", response_model=CSVParseResult)
async def parse_csv_import(
    account_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    acc = _account_or_404(db, account_id)

    raw = await file.read()
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(413, f"CSV exceeds {MAX_CSV_BYTES // (1024 * 1024)} MB limit")
    if not raw:
        raise HTTPException(400, "Empty file")

    text, encoding = _decode_csv(raw)

    # Sniff delimiter on a sample of the file. Fallback to comma if Sniffer chokes.
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = list(reader)
    # Never persist `text` or `raw` past this scope — we do not write CSV to disk.

    if not rows:
        raise HTTPException(400, "CSV has no rows")

    headers = [h.strip() for h in rows[0]]
    if not any(headers):
        raise HTTPException(400, "CSV header row is empty")

    data_rows = rows[1:]
    suggested = _suggest_column_map(headers, acc.csv_column_map)

    return CSVParseResult(
        headers=headers,
        data_rows=data_rows,
        suggested_map=suggested,
        encoding=encoding,
        delimiter=delimiter,
    )


def _candidate_pulls_for_match(
    db: Session,
    account_id: int,
    dist_date: date,
    dist_amount: float,
    excluded_pull_ids: Optional[set[int]] = None,
) -> list[Reimbursement]:
    """Return candidate pulls within the auto-match window for a distribution.

    `excluded_pull_ids` lets the caller skip pulls already claimed earlier in the same
    auto-match pass — necessary because the session uses `autoflush=False` so
    in-progress assignments aren't visible to subsequent queries.
    """
    lo = dist_date - timedelta(days=MATCH_DATE_WINDOW_DAYS)
    hi = dist_date + timedelta(days=MATCH_DATE_WINDOW_DAYS)
    q = (
        db.query(Reimbursement)
        .outerjoin(CustodianDistribution, CustodianDistribution.reimbursement_id == Reimbursement.id)
        .filter(CustodianDistribution.id.is_(None))                       # no existing match
        .filter((Reimbursement.account_id == account_id) | (Reimbursement.account_id.is_(None)))
        .filter(Reimbursement.date >= lo, Reimbursement.date <= hi)
        .filter(func.abs(Reimbursement.total_amount - dist_amount) <= MATCH_AMOUNT_TOLERANCE)
    )
    if excluded_pull_ids:
        q = q.filter(~Reimbursement.id.in_(excluded_pull_ids))
    return q.all()


def _try_auto_link(
    db: Session,
    dist: CustodianDistribution,
    claimed_pull_ids: Optional[set[int]] = None,
) -> bool:
    """Try to auto-link a distribution to a unique candidate pull. Returns True if linked.

    `claimed_pull_ids` is mutated when a link succeeds — pass the same set across iterations
    in the same transaction to prevent two distributions from grabbing the same pull.
    """
    candidates = _candidate_pulls_for_match(
        db, dist.account_id, dist.date, dist.amount, excluded_pull_ids=claimed_pull_ids,
    )
    if len(candidates) != 1:
        return False
    pull = candidates[0]
    dist.reimbursement_id = pull.id
    dist.matched_at = datetime.utcnow()
    dist.match_method = "auto"
    if pull.account_id is None:
        pull.account_id = dist.account_id
    if claimed_pull_ids is not None:
        claimed_pull_ids.add(pull.id)
    return True


@router.post("/accounts/{account_id}/imports/commit", response_model=CSVCommitResult)
def commit_csv_import(
    account_id: int,
    body: CSVCommitRequest,
    db: Session = Depends(get_db),
):
    acc = _account_or_404(db, account_id)

    # Existing custodian_refs for this account — used to short-circuit obvious dupes
    # before SQLite's partial unique index would reject them.
    existing_refs: set[str] = {
        r for (r,) in db.query(CustodianDistribution.custodian_ref)
        .filter(CustodianDistribution.account_id == acc.id)
        .filter(CustodianDistribution.custodian_ref.isnot(None))
        .all()
    }

    inserted = 0
    skipped_dupes = 0
    skipped_invalid = 0
    auto_matched = 0

    new_distribs: list[CustodianDistribution] = []

    for row in body.rows:
        # Amount of zero is meaningless for a distribution — skip without surfacing as error.
        if abs(row.amount) < 0.005:
            skipped_invalid += 1
            continue
        ref = (row.custodian_ref or "").strip() or None
        if ref and ref in existing_refs:
            skipped_dupes += 1
            continue
        dist = CustodianDistribution(
            account_id=acc.id,
            date=row.date,
            amount=round(abs(row.amount), 2),  # store as positive — distribution magnitude
            description=(row.description or "").strip() or None,
            custodian_ref=ref,
        )
        db.add(dist)
        new_distribs.append(dist)
        if ref:
            existing_refs.add(ref)
        inserted += 1

    # Flush to assign IDs so we can attempt auto-match in the same transaction.
    if new_distribs:
        db.flush()
        claimed: set[int] = set()
        for dist in new_distribs:
            if _try_auto_link(db, dist, claimed_pull_ids=claimed):
                auto_matched += 1

    # Persist the column map for next import (only if we actually inserted something).
    if inserted > 0 and body.column_map:
        acc.csv_column_map = json.dumps(body.column_map)

    db.commit()

    return CSVCommitResult(
        inserted=inserted,
        skipped_dupes=skipped_dupes,
        skipped_invalid=skipped_invalid,
        auto_matched=auto_matched,
    )


# ── Distributions ────────────────────────────────────────────────────────────


@router.get("/accounts/{account_id}/distributions", response_model=list[DistributionOut])
def list_distributions(
    account_id: int,
    status: str = "all",
    db: Session = Depends(get_db),
):
    _account_or_404(db, account_id)
    q = db.query(CustodianDistribution).filter(CustodianDistribution.account_id == account_id)
    if status == "matched":
        q = q.filter(CustodianDistribution.reimbursement_id.isnot(None))
    elif status == "unmatched":
        q = q.filter(CustodianDistribution.reimbursement_id.is_(None))
    elif status != "all":
        raise HTTPException(400, "status must be one of: all, matched, unmatched")
    rows = q.order_by(CustodianDistribution.date.desc(), CustodianDistribution.id.desc()).all()
    return [DistributionOut.model_validate(r) for r in rows]


@router.post("/distributions/{distribution_id}/match", response_model=DistributionOut)
def match_distribution(
    distribution_id: int,
    body: DistributionMatchIn,
    db: Session = Depends(get_db),
):
    dist = db.get(CustodianDistribution, distribution_id)
    if not dist:
        raise HTTPException(404, "Distribution not found")
    pull = db.get(Reimbursement, body.reimbursement_id)
    if not pull:
        raise HTTPException(404, "Pull not found")

    # A pull can only back one distribution. If this pull is already linked, refuse.
    other = (
        db.query(CustodianDistribution)
        .filter(
            CustodianDistribution.reimbursement_id == pull.id,
            CustodianDistribution.id != dist.id,
        )
        .first()
    )
    if other:
        raise HTTPException(
            400,
            f"That pull is already matched to distribution #{other.id}. Unlink it first.",
        )

    dist.reimbursement_id = pull.id
    dist.matched_at = datetime.utcnow()
    dist.match_method = "manual"
    if pull.account_id is None:
        pull.account_id = dist.account_id

    db.commit()
    db.refresh(dist)
    return DistributionOut.model_validate(dist)


@router.delete("/distributions/{distribution_id}/match", response_model=DistributionOut)
def unmatch_distribution(distribution_id: int, db: Session = Depends(get_db)):
    dist = db.get(CustodianDistribution, distribution_id)
    if not dist:
        raise HTTPException(404, "Distribution not found")
    dist.reimbursement_id = None
    dist.matched_at = None
    dist.match_method = None
    db.commit()
    db.refresh(dist)
    return DistributionOut.model_validate(dist)


@router.delete("/distributions/{distribution_id}", status_code=204)
def delete_distribution(distribution_id: int, db: Session = Depends(get_db)):
    dist = db.get(CustodianDistribution, distribution_id)
    if not dist:
        raise HTTPException(404, "Distribution not found")
    db.delete(dist)
    db.commit()


# ── Reconciliation ───────────────────────────────────────────────────────────


@router.get("/accounts/{account_id}/reconciliation", response_model=ReconciliationResult)
def get_reconciliation(account_id: int, db: Session = Depends(get_db)):
    _account_or_404(db, account_id)

    distribs = (
        db.query(CustodianDistribution)
        .filter(CustodianDistribution.account_id == account_id)
        .order_by(CustodianDistribution.date.desc())
        .all()
    )

    matched: list[ReconcMatchedItem] = []
    unmatched_distribs: list[DistributionOut] = []
    for d in distribs:
        if d.reimbursement_id and d.reimbursement is not None:
            p = d.reimbursement
            matched.append(ReconcMatchedItem(
                distribution=DistributionOut.model_validate(d),
                pull_id=p.id,
                pull_date=p.date,
                pull_total=round(p.total_amount, 2),
                pull_reference=p.reference,
            ))
        else:
            unmatched_distribs.append(DistributionOut.model_validate(d))

    # Pulls that belong to this account but have no distribution.
    unmatched_pulls_q = (
        db.query(Reimbursement)
        .outerjoin(
            CustodianDistribution,
            CustodianDistribution.reimbursement_id == Reimbursement.id,
        )
        .filter(Reimbursement.account_id == account_id)
        .filter(CustodianDistribution.id.is_(None))
        .order_by(Reimbursement.date.desc())
        .all()
    )
    unmatched_pulls = [
        ReconcUnmatchedPull(
            id=p.id,
            date=p.date,
            total_amount=round(p.total_amount, 2),
            reference=p.reference,
        )
        for p in unmatched_pulls_q
    ]

    return ReconciliationResult(
        matched=matched,
        unmatched_distributions=unmatched_distribs,
        unmatched_pulls=unmatched_pulls,
    )


@router.post("/accounts/{account_id}/reconcile/auto", response_model=ReconcileAutoResult)
def auto_reconcile(account_id: int, db: Session = Depends(get_db)):
    _account_or_404(db, account_id)
    distribs = (
        db.query(CustodianDistribution)
        .filter(
            CustodianDistribution.account_id == account_id,
            CustodianDistribution.reimbursement_id.is_(None),
        )
        .all()
    )
    matched_count = 0
    claimed: set[int] = set()
    for d in distribs:
        if _try_auto_link(db, d, claimed_pull_ids=claimed):
            matched_count += 1
    db.commit()
    return ReconcileAutoResult(matched_count=matched_count)


# ── Snapshots ────────────────────────────────────────────────────────────────


@router.get("/accounts/{account_id}/snapshots", response_model=list[SnapshotOut])
def list_snapshots(account_id: int, db: Session = Depends(get_db)):
    _account_or_404(db, account_id)
    snaps = (
        db.query(BalanceSnapshot)
        .filter(BalanceSnapshot.account_id == account_id)
        .order_by(BalanceSnapshot.as_of_date.desc(), BalanceSnapshot.id.desc())
        .all()
    )
    return [SnapshotOut.model_validate(s) for s in snaps]


@router.post("/accounts/{account_id}/snapshots", response_model=SnapshotOut, status_code=201)
def create_snapshot(account_id: int, body: SnapshotIn, db: Session = Depends(get_db)):
    _account_or_404(db, account_id)
    existing = (
        db.query(BalanceSnapshot)
        .filter(
            BalanceSnapshot.account_id == account_id,
            BalanceSnapshot.as_of_date == body.as_of_date,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            400,
            f"A snapshot for {body.as_of_date.isoformat()} already exists. Edit it instead.",
        )
    snap = BalanceSnapshot(
        account_id=account_id,
        as_of_date=body.as_of_date,
        cash_balance=body.cash_balance,
        invested_balance=body.invested_balance,
        source=body.source or "manual",
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return SnapshotOut.model_validate(snap)


@router.put("/snapshots/{snapshot_id}", response_model=SnapshotOut)
def update_snapshot(snapshot_id: int, body: SnapshotUpdate, db: Session = Depends(get_db)):
    snap = db.get(BalanceSnapshot, snapshot_id)
    if not snap:
        raise HTTPException(404, "Snapshot not found")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(snap, k, v)
    db.commit()
    db.refresh(snap)
    return SnapshotOut.model_validate(snap)


@router.delete("/snapshots/{snapshot_id}", status_code=204)
def delete_snapshot(snapshot_id: int, db: Session = Depends(get_db)):
    snap = db.get(BalanceSnapshot, snapshot_id)
    if not snap:
        raise HTTPException(404, "Snapshot not found")
    db.delete(snap)
    db.commit()
