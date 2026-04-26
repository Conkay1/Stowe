import datetime as _dt
import re
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, field_validator, model_validator

from config import HSA_CATEGORIES

# Type alias — avoids Pydantic v2 field-name shadowing when a field is also called "date".
DateType = _dt.date


class CategoryIn(BaseModel):
    name: str

class CategoryOut(BaseModel):
    id: int
    name: str
    is_default: bool

    model_config = {"from_attributes": True}


class ExpenseCreate(BaseModel):
    merchant: str
    date: DateType
    amount: float
    category: str = "Other"
    notes: Optional[str] = None

    @field_validator("amount")
    @classmethod
    def amount_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Amount must be greater than 0")
        return round(v, 2)


class ExpenseUpdate(BaseModel):
    merchant: Optional[str] = None
    date: Optional[DateType] = None
    amount: Optional[float] = None
    category: Optional[str] = None
    notes: Optional[str] = None
    reimbursed: Optional[bool] = None
    reimbursed_date: Optional[DateType] = None

    @field_validator("amount")
    @classmethod
    def amount_must_be_positive(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("Amount must be greater than 0")
        return round(v, 2) if v is not None else v


class ReceiptOut(BaseModel):
    id: int
    expense_id: int
    original_filename: Optional[str]
    file_type: Optional[str]
    file_size_bytes: Optional[int]
    uploaded_at: datetime

    model_config = {"from_attributes": True}


class ExpenseOut(BaseModel):
    id: int
    merchant: str
    date: DateType
    amount: float
    category: str
    notes: Optional[str]
    reimbursed: bool
    reimbursed_date: Optional[DateType]
    covered_amount: float = 0.0     # sum of all line items pointing at this expense
    remaining_amount: float = 0.0   # max(0, amount - covered_amount); 0 means fully covered
    pull_count: int = 0             # how many distinct pulls back this expense
    created_at: datetime
    receipts: list[ReceiptOut] = []

    model_config = {"from_attributes": True}


class VaultSummary(BaseModel):
    total_unreimbursed: float
    total_reimbursed: float
    count_unreimbursed: int
    count_reimbursed: int
    receipt_completeness_pct: float


class LedgerYear(BaseModel):
    year: int
    count: int
    total_amount: float
    total_reimbursed: float
    total_unreimbursed: float
    receipt_completeness_pct: float


# ── Reimbursement Pull Events ────────────────────────────────────────────────

class PullLineItemIn(BaseModel):
    expense_id: int
    covered_amount: float

    @field_validator("covered_amount")
    @classmethod
    def positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("covered_amount must be greater than 0")
        return round(v, 2)


class ReimbursementCreate(BaseModel):
    date: DateType
    reference: Optional[str] = None
    notes: Optional[str] = None
    total_amount: float
    line_items: list[PullLineItemIn]

    @field_validator("total_amount")
    @classmethod
    def total_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("total_amount must be greater than 0")
        return round(v, 2)

    @field_validator("line_items")
    @classmethod
    def non_empty_no_dupes(cls, v: list[PullLineItemIn]) -> list[PullLineItemIn]:
        if not v:
            raise ValueError("line_items must contain at least one entry")
        ids = [li.expense_id for li in v]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate expense_id in line items")
        return v

    @field_validator("date")
    @classmethod
    def date_not_in_future(cls, v: DateType) -> DateType:
        if v > _dt.date.today():
            raise ValueError("Pull date cannot be in the future")
        return v

    @model_validator(mode="after")
    def sums_match(self) -> "ReimbursementCreate":
        s = round(sum(li.covered_amount for li in self.line_items), 2)
        if abs(s - round(self.total_amount, 2)) > 0.01:
            raise ValueError(
                f"Line items sum to ${s:.2f} but total_amount is ${self.total_amount:.2f}"
            )
        return self


class PullLineItemOut(BaseModel):
    id: int
    expense_id: int
    covered_amount: float
    expense_amount: float
    merchant: str
    date: DateType
    category: str
    receipt_count: int


class ReimbursementListItem(BaseModel):
    id: int
    date: DateType
    reference: Optional[str]
    notes: Optional[str]
    expense_count: int
    total_amount: float
    account_id: Optional[int] = None
    account_name: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReimbursementOut(ReimbursementListItem):
    line_items: list[PullLineItemOut] = []
    # Matched custodian distribution if reconciled. Forward ref — DistributionOut defined below.
    distribution: Optional["DistributionOut"] = None


# ── HSA Accounts ─────────────────────────────────────────────────────────────


def _validate_account_mask(v: Optional[str]) -> Optional[str]:
    """Reject anything that looks like a full account number.

    Rules:
      - empty / None passes through unchanged.
      - max 12 chars (last-4, last-6, short nicknames all fit).
      - reject all-digit strings of 8+ digits (anything that looks like an account number).
    """
    if v is None:
        return v
    v = v.strip()
    if not v:
        return None
    if len(v) > 12:
        raise ValueError("account_mask must be ≤ 12 characters — use a last-4 or nickname, never the full number")
    if re.fullmatch(r"\d{8,}", v):
        raise ValueError("account_mask looks like a full account number — use a last-4 or nickname instead")
    return v


class AccountCreate(BaseModel):
    name: str
    custodian: str
    account_mask: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("name", "custodian")
    @classmethod
    def non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v

    @field_validator("account_mask")
    @classmethod
    def mask_safe(cls, v: Optional[str]) -> Optional[str]:
        return _validate_account_mask(v)


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    custodian: Optional[str] = None
    account_mask: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("name", "custodian")
    @classmethod
    def non_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v

    @field_validator("account_mask")
    @classmethod
    def mask_safe(cls, v: Optional[str]) -> Optional[str]:
        return _validate_account_mask(v)


class AccountOut(BaseModel):
    id: int
    name: str
    custodian: str
    account_mask: Optional[str] = None
    is_active: bool
    notes: Optional[str] = None
    created_at: datetime

    # Derived (not stored).
    pull_count: int = 0
    distribution_count: int = 0
    unmatched_distribution_count: int = 0
    cash_balance: float = 0.0
    invested_balance: float = 0.0
    last_snapshot_date: Optional[DateType] = None
    has_csv_column_map: bool = False

    model_config = {"from_attributes": True}


# ── Custodian Distributions ──────────────────────────────────────────────────


class DistributionOut(BaseModel):
    id: int
    account_id: int
    date: DateType
    amount: float
    description: Optional[str] = None
    custodian_ref: Optional[str] = None
    reimbursement_id: Optional[int] = None
    matched_at: Optional[datetime] = None
    match_method: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class DistributionMatchIn(BaseModel):
    reimbursement_id: int


# Resolve the forward reference now that DistributionOut exists.
ReimbursementOut.model_rebuild()


# ── CSV Import ───────────────────────────────────────────────────────────────


class CSVParseResult(BaseModel):
    headers: list[str]
    data_rows: list[list[str]]      # all parsed data rows (file is in memory, capped at 10 MB upload)
    suggested_map: dict[str, str]   # header -> canonical field
    encoding: str
    delimiter: str


class CSVImportRow(BaseModel):
    """One row that the client wants to commit. Date stored as ISO yyyy-mm-dd."""
    date: DateType
    amount: float
    description: Optional[str] = None
    custodian_ref: Optional[str] = None

    @field_validator("amount")
    @classmethod
    def round_amount(cls, v: float) -> float:
        return round(v, 2)


class CSVCommitRequest(BaseModel):
    column_map: dict[str, str]      # original header -> canonical field, persisted to account
    rows: list[CSVImportRow]


class CSVCommitResult(BaseModel):
    inserted: int
    skipped_dupes: int
    skipped_invalid: int
    auto_matched: int


# ── Reconciliation ───────────────────────────────────────────────────────────


class ReconcMatchedItem(BaseModel):
    distribution: DistributionOut
    pull_id: int
    pull_date: DateType
    pull_total: float
    pull_reference: Optional[str] = None


class ReconcUnmatchedPull(BaseModel):
    id: int
    date: DateType
    total_amount: float
    reference: Optional[str] = None


class ReconciliationResult(BaseModel):
    matched: list[ReconcMatchedItem]
    unmatched_distributions: list[DistributionOut]
    unmatched_pulls: list[ReconcUnmatchedPull]


class ReconcileAutoResult(BaseModel):
    matched_count: int


# ── Balance Snapshots ────────────────────────────────────────────────────────


class SnapshotIn(BaseModel):
    as_of_date: DateType
    cash_balance: float = 0.0
    invested_balance: float = 0.0
    source: Optional[str] = "manual"

    @field_validator("cash_balance", "invested_balance")
    @classmethod
    def non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("balance cannot be negative")
        return round(v, 2)

    @field_validator("as_of_date")
    @classmethod
    def not_in_future(cls, v: DateType) -> DateType:
        if v > _dt.date.today():
            raise ValueError("snapshot date cannot be in the future")
        return v


class SnapshotUpdate(BaseModel):
    as_of_date: Optional[DateType] = None
    cash_balance: Optional[float] = None
    invested_balance: Optional[float] = None
    source: Optional[str] = None

    @field_validator("cash_balance", "invested_balance")
    @classmethod
    def non_negative(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        if v < 0:
            raise ValueError("balance cannot be negative")
        return round(v, 2)


class SnapshotOut(BaseModel):
    id: int
    account_id: int
    as_of_date: DateType
    cash_balance: float
    invested_balance: float
    source: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Reimbursement update (account tagging) ───────────────────────────────────


class ReimbursementUpdate(BaseModel):
    """Partial-update for a Pull. Currently only `account_id` is editable."""
    account_id: Optional[int] = None  # explicit None = clear; field-not-set = no change

    model_config = {"extra": "forbid"}
