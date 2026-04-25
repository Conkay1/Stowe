from datetime import datetime
from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


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
    created_at   = Column(DateTime, default=datetime.utcnow)

    line_items = relationship(
        "ReimbursementLineItem",
        back_populates="reimbursement",
        cascade="all, delete-orphan",
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
