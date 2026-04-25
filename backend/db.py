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


def init_db():
    from backend.models import Base
    RECEIPTS_DIR.mkdir(exist_ok=True)
    Base.metadata.create_all(bind=engine)
    _migrate(engine)
