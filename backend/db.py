from sqlalchemy import create_engine
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


def init_db():
    from backend.models import Base
    RECEIPTS_DIR.mkdir(exist_ok=True)
    Base.metadata.create_all(bind=engine)
