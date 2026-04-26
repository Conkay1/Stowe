import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.db import init_db
from backend.routers import expenses, reimbursements, categories


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure DB schema and any incremental migrations are applied on app boot.
    # run.py also calls init_db() before the server starts, but doing it here
    # makes the app self-bootstrapping under any ASGI runner. init_db() is idempotent.
    init_db()
    yield


app = FastAPI(title="Stowe", docs_url="/api/docs", lifespan=lifespan)

app.include_router(expenses.router)
app.include_router(reimbursements.router)
app.include_router(categories.router)

# In a PyInstaller bundle, bundled data files are extracted under sys._MEIPASS.
_RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
FRONTEND = _RESOURCE_ROOT / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    return FileResponse(str(FRONTEND / "index.html"))
