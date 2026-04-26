from datetime import datetime
from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Category(Base):
    __tablename__ = "categories"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    name       = Column(String, unique=True, nullable=False)
    is_default = Column(Boolean, nullable=False, default=False)


class HSAExpense(Base):
    __tablename__ = "hsa_expenses"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    merchant         = Column(String, nullable=False)
    date             = Column(Date, nullable=False)
    amount           = Column(Float, nullable=False)
    category         = Column(String, nullable=False, default="Other")
    notes            = Column(Text)
    # `reimbursed` / `reimbursed_date` are maintained:
    #   - flipped True when line items fully cover `amount`
    #   - flipped False when a covering pull is undone
    #   - free for manual override when no line items exist (legacy "Mark as reimbursed" path)
    reimbursed       = Column(Boolean, nullable=False, default=False)
    reimbursed_date  = Column(Date)
    # Legacy column from v0.3 — superseded by reimbursement_line_items.
    # Kept for backfill migration and SQLite-friendly column retention; not read by new code.
    reimbursement_id = Column(
        Integer,
        ForeignKey("reimbursements.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at       = Column(DateTime, default=datetime.utcnow)

    receipts   = relationship("Receipt", back_populates="expense", cascade="all, delete-orphan")
    line_items = relationship("ReimbursementLineItem", back_populates="expense")


class Receipt(Base):
    __tablename__ = "receipts"

    id                = Column(Integer, primary_key=True, autoincrement=True)
    expense_id        = Column(Integer, ForeignKey("hsa_expenses.id"), nullable=False)
    filename          = Column(String, nullable=False)
    original_filename = Column(String)
    file_type         = Column(String)
    file_size_bytes   = Column(Integer)
    uploaded_at       = Column(DateTime, default=datetime.utcnow)

    expense = relationship("HSAExpense", back_populates="receipts")


class Reimbursement(Base):
    """A single HSA distribution event covering one or more expense slices."""
    __tablename__ = "reimbursements"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    date         = Column(Date, nullable=False)
    reference    = Column(String)
    notes        = Column(Text)
    total_amount = Column(Float, nullable=False, default=0.0)
    account_id   = Column(
        Integer,
        ForeignKey("hsa_accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at   = Column(DateTime, default=datetime.utcnow)

    line_items = relationship(
        "ReimbursementLineItem",
        back_populates="reimbursement",
        cascade="all, delete-orphan",
    )
    account      = relationship("HSAAccount", back_populates="pulls")
    distribution = relationship(
        "CustodianDistribution",
        back_populates="reimbursement",
        uselist=False,
    )


class ReimbursementLineItem(Base):
    """One slice of a pull — covers `covered_amount` of a specific expense."""
    __tablename__ = "reimbursement_line_items"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    reimbursement_id = Column(Integer, ForeignKey("reimbursements.id", ondelete="CASCADE"), nullable=False)
    expense_id       = Column(Integer, ForeignKey("hsa_expenses.id"), nullable=False)
    covered_amount   = Column(Float, nullable=False)

    reimbursement = relationship("Reimbursement", back_populates="line_items")
    expense       = relationship("HSAExpense", back_populates="line_items")


# ── HSA Accounts (custodian linking) ────────────────────────────────────────

class HSAAccount(Base):
    """An HSA the user owns. Pulls can be tagged to one; CSVs import distributions."""
    __tablename__ = "hsa_accounts"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    name           = Column(String, nullable=False)
    custodian      = Column(String, nullable=False)
    account_mask   = Column(String)           # last-4 or nickname; never the full number
    is_active      = Column(Boolean, nullable=False, default=True)
    notes          = Column(Text)
    csv_column_map = Column(Text)             # JSON: last successful column mapping
    created_at     = Column(DateTime, default=datetime.utcnow)

    distributions = relationship(
        "CustodianDistribution",
        back_populates="account",
        cascade="all, delete-orphan",
    )
    snapshots = relationship(
        "BalanceSnapshot",
        back_populates="account",
        cascade="all, delete-orphan",
    )
    pulls = relationship("Reimbursement", back_populates="account")


class CustodianDistribution(Base):
    """One distribution row imported from a custodian CSV. May be linked to a Reimbursement."""
    __tablename__ = "custodian_distributions"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    account_id       = Column(
        Integer,
        ForeignKey("hsa_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date             = Column(Date, nullable=False, index=True)
    amount           = Column(Float, nullable=False)
    description      = Column(String)
    custodian_ref    = Column(String, index=True)   # transaction id, dedupe spine
    reimbursement_id = Column(
        Integer,
        ForeignKey("reimbursements.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    matched_at       = Column(DateTime)
    match_method     = Column(String)               # "auto" | "manual"
    created_at       = Column(DateTime, default=datetime.utcnow)

    account       = relationship("HSAAccount", back_populates="distributions")
    reimbursement = relationship("Reimbursement", back_populates="distribution")


class BalanceSnapshot(Base):
    """Point-in-time balance for an HSA. Latest = current balance."""
    __tablename__ = "balance_snapshots"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    account_id       = Column(
        Integer,
        ForeignKey("hsa_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    as_of_date       = Column(Date, nullable=False, index=True)
    cash_balance     = Column(Float, nullable=False, default=0.0)
    invested_balance = Column(Float, nullable=False, default=0.0)
    source           = Column(String)          # "manual" | "csv"
    created_at       = Column(DateTime, default=datetime.utcnow)

    account = relationship("HSAAccount", back_populates="snapshots")

    __table_args__ = (
        UniqueConstraint("account_id", "as_of_date", name="ux_snapshot_account_date"),
    )
