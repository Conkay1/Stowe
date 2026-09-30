"""Icon paths referenced by the SPA must be served from /static."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "frontend" / "index.html"
MANIFEST = ROOT / "frontend" / "manifest.json"

_ICON_HREF = re.compile(r"""(?:href|src)=["'](/static/icons/[^"']+)["']""")


def referenced_icon_paths():
    html = INDEX.read_text(encoding="utf-8")
    paths = set(_ICON_HREF.findall(html))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for icon in manifest.get("icons", []):
        src = icon.get("src") or ""
        if src.startswith("/static/icons/"):
            paths.add(src)
    return sorted(paths)


def test_referenced_icons_return_200(client):
    paths = referenced_icon_paths()
    assert paths, "index.html and manifest.json should reference /static/icons/"
    for path in paths:
        res = client.get(path)
        assert res.status_code == 200, path
        assert res.headers["content-type"].startswith("image/png"), path
        assert res.content.startswith(b"\x89PNG\r\n\x1a\n"), path
