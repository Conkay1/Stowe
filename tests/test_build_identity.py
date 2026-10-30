import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_macos_bundle_identity():
    spec = (ROOT / "stowe.spec").read_text(encoding="utf-8")
    assert "bundle_identifier='com.wellslip.desktop'" in spec
    assert "bundle_identifier='com.stowe.app'" not in spec
    assert "name='Wellslip Desktop.app'" in spec
    assert "'CFBundleDisplayName': 'Wellslip Desktop'" in spec


def test_windows_appid_unchanged():
    iss = (ROOT / "stowe.iss").read_text(encoding="utf-8")
    assert re.search(r"^AppId=\{\{A3F2C1D0-7E4B-4F9A-B8C5-2D6E1F0A3B7C\}$", iss, re.M)
    assert '#define AppName      "Wellslip Desktop"' in iss


def test_build_script_matches_spec():
    script = (ROOT / "scripts" / "build-macos.sh").read_text(encoding="utf-8")
    assert 'APP="dist/Wellslip Desktop.app"' in script
    assert '"${APP}/Contents/MacOS/Wellslip Desktop"' in script
