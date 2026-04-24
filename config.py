import os
import sys
from pathlib import Path

APP_NAME = "Stowe"
BASE_DIR = Path(__file__).parent


def _user_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / APP_NAME.lower()


# When packaged with PyInstaller, write user data to a platform-standard
# writable location instead of inside the read-only .app bundle.
DATA_DIR = _user_data_dir() if getattr(sys, "frozen", False) else BASE_DIR

DATABASE_PATH = DATA_DIR / "database" / "stowe.db"
RECEIPTS_DIR = DATA_DIR / "receipts"
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

HSA_CATEGORIES = [
    "Medical",
    "Pharmacy",
    "Dental",
    "Vision",
    "Mental Health",
    "Medical Equipment",
    "Other",
]
