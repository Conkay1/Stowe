import os
import subprocess
import sys

from fastapi import APIRouter, HTTPException

import config
from config import DATA_DIR, DATABASE_PATH, RECEIPTS_DIR, ensure_private_dir

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/data-dir")
def get_data_dir():
    return {
        "data_dir": str(DATA_DIR),
        "database_path": str(DATABASE_PATH),
        "receipts_dir": str(RECEIPTS_DIR),
        # Set only when the Stowe data copy failed this launch.
        "migration_notice": config.MIGRATION_NOTICE,
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
