import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.routers import expenses

app = FastAPI(title="Stowe", docs_url="/api/docs")

app.include_router(expenses.router)

# In a PyInstaller bundle, bundled data files are extracted under sys._MEIPASS.
_RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
FRONTEND = _RESOURCE_ROOT / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    return FileResponse(str(FRONTEND / "index.html"))
