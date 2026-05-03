import os
import subprocess
import sys

from fastapi import APIRouter, HTTPException

from config import DATA_DIR, DATABASE_PATH, RECEIPTS_DIR

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/data-dir")
def get_data_dir():
    return {
        "data_dir": str(DATA_DIR),
        "database_path": str(DATABASE_PATH),
        "receipts_dir": str(RECEIPTS_DIR),
    }


@router.post("/reveal-data-dir")
def reveal_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
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
