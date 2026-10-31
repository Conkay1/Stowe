import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config

config.DATA_DIR = Path(sys.argv[1]).resolve()
config.DATABASE_PATH = config.DATA_DIR / "database" / "wellslip.db"
config.DATABASE_URL = f"sqlite:///{config.DATABASE_PATH}"
config.RECEIPTS_DIR = config.DATA_DIR / "receipts"

import uvicorn
from backend import ocr

ocr.start_image_warmup = lambda: False
ocr.stop_image_warmup = lambda: None
from main import app

uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[2]), log_level="error")
