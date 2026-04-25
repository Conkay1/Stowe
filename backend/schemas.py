from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, field_validator

from config import HSA_CATEGORIES


class ExpenseCreate(BaseModel):
    merchant: str
    date: date
    amount: float
    category: str = "Other"
    notes: Optional[str] = None

    @field_validator("amount")
    @classmethod
    def amount_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Amount must be greater than 0")
        return round(v, 2)

    @field_validator("category")
    @classmethod
    def category_must_be_valid(cls, v: str) -> str:
        if v not in HSA_CATEGORIES:
            raise ValueError(f"Category must be one of: {', '.join(HSA_CATEGORIES)}")
        return v


class ExpenseUpdate(BaseModel):
    merchant: Optional[str] = None
    date: Optional[date] = None
    amount: Optional[float] = None
    category: Optional[str] = None
    notes: Optional[str] = None
    reimbursed: Optional[bool] = None
    reimbursed_date: Optional[date] = None

    @field_validator("amount")
    @classmethod
    def amount_must_be_positive(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("Amount must be greater than 0")
        return round(v, 2) if v is not None else v

    @field_validator("category")
    @classmethod
    def category_must_be_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in HSA_CATEGORIES:
            raise ValueError(f"Category must be one of: {', '.join(HSA_CATEGORIES)}")
        return v


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
    date: date
    amount: float
    category: str
    notes: Optional[str]
    reimbursed: bool
    reimbursed_date: Optional[date]
    reimbursement_id: Optional[int] = None
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

class ReimbursementCreate(BaseModel):
    date: date
    reference: Optional[str] = None
    notes: Optional[str] = None
    expense_ids: list[int]

    @field_validator("expense_ids")
    @classmethod
    def expense_ids_must_be_non_empty(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("expense_ids must contain at least one expense")
        # Dedupe while preserving order
        seen: set[int] = set()
        unique: list[int] = []
        for eid in v:
            if eid not in seen:
                seen.add(eid)
                unique.append(eid)
        return unique

    @field_validator("date")
    @classmethod
    def date_not_in_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("Pull date cannot be in the future")
        return v


class ReimbursementListItem(BaseModel):
    id: int
    date: date
    reference: Optional[str]
    notes: Optional[str]
    expense_count: int
    total_amount: float
    created_at: datetime

    model_config = {"from_attributes": True}


class ReimbursementOut(ReimbursementListItem):
    expenses: list[ExpenseOut] = []
