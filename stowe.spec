# PyInstaller spec for Stowe macOS .app
# Build: /opt/homebrew/bin/python3.11 -m PyInstaller stowe.spec --noconfirm

import pathlib
import sys

try:
    import webview as _wv
except ImportError:
    sys.exit(
        "pywebview is not installed for the Python running PyInstaller. "
        "Install it in that environment (pip install pywebview) and rebuild."
    )

block_cipher = None

# Locate pywebview's bundled PyInstaller hooks from the installed package.
# Resolves for whatever interpreter runs PyInstaller (venv, python.org, Homebrew).
_webview_hooks = pathlib.Path(_wv.__file__).resolve().parent / "__pyinstaller"
if not _webview_hooks.is_dir():
    sys.exit(
        "pywebview is installed, but its PyInstaller hook directory was not "
        f"found at {_webview_hooks}. Reinstall pywebview in the Python that "
        "runs PyInstaller."
    )
_webview_hooks = str(_webview_hooks)

a = Analysis(
    ['run.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        # Whole frontend tree, including the vendored Chart.js build and its
        # license at frontend/vendor/chartjs/ (no CDN fetch at runtime).
        ('frontend', 'frontend'),
        ('assets',   'assets'),
    ],
    hiddenimports=[
        # uvicorn
        'uvicorn.logging',
        'uvicorn.loops.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.websockets_impl',
        'uvicorn.lifespan.on',
        'uvicorn.lifespan.off',
        # multipart
        'python_multipart',
        'multipart',
        # pywebview — platforms live at webview.platforms.*
        'webview',
        'webview.platforms.cocoa',
        'webview.platforms.edgechromium',
        'webview.platforms.gtk',
        'webview.platforms.qt',
        'webview.platforms.mshtml',
        'webview.platforms.win32',
        'webview.platforms.winforms',
        'webview.platforms.cef',
        # Apple Vision OCR for receipt auto-review (macOS only). pyobjc bindings
        # load submodules lazily, so PyInstaller needs them named explicitly.
        # macOS-only build — safe to list unconditionally here (this is stowe.spec).
        'objc',
        'Foundation',
        'CoreFoundation',
        'Quartz',
        'Vision',
    ],
    hookspath=[_webview_hooks],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'pandas', 'pytest'],
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Stowe',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    # Signing is performed post-build by scripts/build-macos.sh so we can
    # deep-sign every nested dylib/.so. Do NOT set these here — PyInstaller's
    # built-in signing only touches the top-level executable.
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Stowe',
)

app = BUNDLE(
    coll,
    name='Stowe.app',
    icon='assets/stowe.icns',
    bundle_identifier='com.stowe.app',
    info_plist={
        'CFBundleName': 'Stowe',
        'CFBundleDisplayName': 'Stowe',
        'CFBundleVersion': '0.7.0',
        'CFBundleShortVersionString': '0.7.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '11.0',
        'NSHumanReadableCopyright': 'Copyright (c) 2026 Connor Kay. MIT License.',
        # Allow WKWebView to load localhost
        'NSAppTransportSecurity': {
            'NSAllowsLocalNetworking': True,
        },
    },
)
