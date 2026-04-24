from datetime import datetime
from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class HSAExpense(Base):
    __tablename__ = "hsa_expenses"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    merchant        = Column(String, nullable=False)
    date            = Column(Date, nullable=False)
    amount          = Column(Float, nullable=False)
    category        = Column(String, nullable=False, default="Other")
    notes           = Column(Text)
    reimbursed      = Column(Boolean, nullable=False, default=False)
    reimbursed_date = Column(Date)
    created_at      = Column(DateTime, default=datetime.utcnow)

    receipts = relationship("Receipt", back_populates="expense", cascade="all, delete-orphan")


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
