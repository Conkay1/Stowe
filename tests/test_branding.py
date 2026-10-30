import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_window_title_is_wellslip_desktop():
    src = (ROOT / "run.py").read_text(encoding="utf-8")
    assert re.search(r'create_window\(\s*title="Wellslip Desktop"', src)


def test_ui_shows_wellslip_desktop():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "<title>Wellslip Desktop</title>" in html
    manifest = json.loads((ROOT / "frontend" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "Wellslip Desktop"
