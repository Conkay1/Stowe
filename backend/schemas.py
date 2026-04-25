from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, field_validator, model_validator

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
    date: date
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
    def date_not_in_future(cls, v: date) -> date:
        if v > date.today():
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
    date: date
    category: str
    receipt_count: int


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
    line_items: list[PullLineItemOut] = []
