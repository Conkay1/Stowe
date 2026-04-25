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
    reimbursed       = Column(Boolean, nullable=False, default=False)
    reimbursed_date  = Column(Date)
    reimbursement_id = Column(
        Integer,
        ForeignKey("reimbursements.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at       = Column(DateTime, default=datetime.utcnow)

    receipts      = relationship("Receipt", back_populates="expense", cascade="all, delete-orphan")
    reimbursement = relationship("Reimbursement", back_populates="expenses")


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
    """A single HSA distribution event covering one or more expenses."""
    __tablename__ = "reimbursements"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    date       = Column(Date, nullable=False)
    reference  = Column(String)   # HSA portal reference / check number
    notes      = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    expenses = relationship("HSAExpense", back_populates="reimbursement")
