import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Redirect DB + receipts to a temp location so tests never touch real data.
    db_path = tmp_path / "test.db"
    receipts_dir = tmp_path / "receipts"
    receipts_dir.mkdir()

    import config
    monkeypatch.setattr(config, "DATABASE_PATH", db_path)
    monkeypatch.setattr(config, "RECEIPTS_DIR", receipts_dir)
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{db_path}")

    # Rebuild engine against the temp DB.
    from backend import db as db_module
    test_engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestSession)

    # Point the expenses router at the temp receipts dir too.
    from backend.routers import expenses as expenses_router
    monkeypatch.setattr(expenses_router, "RECEIPTS_DIR", receipts_dir)

    # Create schema.
    from backend.models import Base
    Base.metadata.create_all(bind=test_engine)

    # Override the get_db dependency so the app uses the temp session.
    from main import app

    def override_get_db():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[db_module.get_db] = override_get_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
