from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from config import DATABASE_PATH, DATABASE_URL, RECEIPTS_DIR

DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate(bind):
    """Apply incremental schema changes. Safe to run on every startup."""
    with bind.begin() as conn:
        # v0.3 — add reimbursement_id FK to existing hsa_expenses tables.
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(hsa_expenses)"))]
        if cols and "reimbursement_id" not in cols:
            conn.execute(text(
                "ALTER TABLE hsa_expenses ADD COLUMN reimbursement_id INTEGER "
                "REFERENCES reimbursements(id)"
            ))

        # v0.4 — add total_amount to reimbursements (existing rows get backfilled below).
        reimb_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(reimbursements)"))]
        if reimb_cols and "total_amount" not in reimb_cols:
            conn.execute(text(
                "ALTER TABLE reimbursements ADD COLUMN total_amount REAL NOT NULL DEFAULT 0"
            ))


def _bootstrap_line_items(bind):
    """Backfill `reimbursement_line_items` from the legacy 1:1 reimbursement_id FK.

    Idempotent: only runs when the line-items table is empty AND legacy links exist.
    Each legacy (expense, pull) link becomes one line item with covered_amount = expense.amount.
    Also backfills the new `reimbursements.total_amount` column.
    """
    with bind.begin() as conn:
        existing_count = conn.execute(text(
            "SELECT COUNT(*) FROM reimbursement_line_items"
        )).scalar()
        if existing_count and existing_count > 0:
            return

        legacy_links = conn.execute(text(
            "SELECT id, reimbursement_id, amount FROM hsa_expenses "
            "WHERE reimbursement_id IS NOT NULL"
        )).fetchall()
        if not legacy_links:
            return

        for expense_id, pull_id, amount in legacy_links:
            conn.execute(
                text(
                    "INSERT INTO reimbursement_line_items "
                    "(reimbursement_id, expense_id, covered_amount) "
                    "VALUES (:pid, :eid, :amt)"
                ),
                {"pid": pull_id, "eid": expense_id, "amt": amount},
            )

        # Backfill total_amount on each reimbursement = sum of its line items.
        conn.execute(text(
            "UPDATE reimbursements SET total_amount = COALESCE(("
            "  SELECT ROUND(SUM(covered_amount), 2) "
            "  FROM reimbursement_line_items "
            "  WHERE reimbursement_id = reimbursements.id"
            "), 0)"
        ))


def init_db():
    from backend.models import Base
    RECEIPTS_DIR.mkdir(exist_ok=True)
    Base.metadata.create_all(bind=engine)
    _migrate(engine)
    _bootstrap_line_items(engine)
