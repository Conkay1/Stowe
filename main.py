import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.db import init_db
from backend.routers import accounts, expenses, reimbursements, categories, system


def _allowed_hosts(*, include_testclient: bool | None = None) -> list[str]:
    """Hostnames the API will accept.

    uvicorn does not reject a forged Host header. A page on another origin can
    DNS-rebind to 127.0.0.1 and call unauthenticated routes such as
    GET /api/v1/export/zip. TrustedHostMiddleware compares the hostname after
    stripping the port, so ``127.0.0.1:8000`` and ``localhost:8000`` match.

    FastAPI's TestClient defaults to Host ``testserver``. That name is allowed
    only while pytest is running; ``python3 run.py`` (browser and webview)
    still talks to ``http://127.0.0.1:<port>`` and does not include it.
    """
    hosts = ["127.0.0.1", "localhost"]
    if include_testclient is None:
        include_testclient = "pytest" in sys.modules
    if include_testclient:
        hosts.append("testserver")
    return hosts


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure DB schema and any incremental migrations are applied on app boot.
    # run.py also calls init_db() before the server starts, but doing it here
    # makes the app self-bootstrapping under any ASGI runner. init_db() is idempotent.
    init_db()
    yield


# Swagger and ReDoc load scripts from public CDNs. Nothing in the app reads
# the schema, so those pages and /openapi.json stay unmounted.
app = FastAPI(
    title="Stowe",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts())

app.include_router(expenses.router)
app.include_router(reimbursements.router)
app.include_router(categories.router)
app.include_router(accounts.router)
app.include_router(system.router)

# In a PyInstaller bundle, bundled data files are extracted under sys._MEIPASS.
_RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
FRONTEND = _RESOURCE_ROOT / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")


@app.get("/api/docs", include_in_schema=False)
@app.get("/redoc", include_in_schema=False)
async def disabled_api_docs():
    # Registered ahead of the SPA catch-all so these paths are a real 404.
    raise HTTPException(status_code=404)


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    return FileResponse(str(FRONTEND / "index.html"))
