import json
import logging
import os
import subprocess
import sys
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import (
    DATA_DIR,
    DATABASE_PATH,
    RECEIPTS_DIR,
    ensure_private_dir,
    restrict_private_file,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/system", tags=["system"])

# The packaged app's webview runs in private mode, so localStorage is wiped
# on every restart. UI preferences are kept in a small JSON file instead.
SETTINGS_PATH = DATA_DIR / "settings.json"
THEMES = ("dark", "light", "sepia")
DEFAULT_THEME = "dark"


class ThemeIn(BaseModel):
    theme: Literal["dark", "light", "sepia"]


def _read_settings() -> dict:
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable settings file %s: %s", SETTINGS_PATH, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _write_settings(data: dict) -> None:
    ensure_private_dir(SETTINGS_PATH.parent)
    tmp = SETTINGS_PATH.with_name(SETTINGS_PATH.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, SETTINGS_PATH)
    restrict_private_file(SETTINGS_PATH)


@router.get("/theme")
def get_theme():
    theme = _read_settings().get("theme")
    return {"theme": theme if theme in THEMES else DEFAULT_THEME}


@router.put("/theme")
def set_theme(body: ThemeIn):
    data = _read_settings()
    data["theme"] = body.theme
    try:
        _write_settings(data)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save theme: {exc}")
    return {"theme": body.theme}


@router.get("/data-dir")
def get_data_dir():
    return {
        "data_dir": str(DATA_DIR),
        "database_path": str(DATABASE_PATH),
        "receipts_dir": str(RECEIPTS_DIR),
    }


@router.post("/reveal-data-dir")
def reveal_data_dir():
    ensure_private_dir(DATA_DIR)
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(DATA_DIR)])
        elif sys.platform == "win32":
            os.startfile(str(DATA_DIR))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(DATA_DIR)])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not open folder: {e}")
    return {"ok": True, "data_dir": str(DATA_DIR)}
