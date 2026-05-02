# PyInstaller spec for Stowe macOS .app
# Build: /opt/homebrew/bin/python3.11 -m PyInstaller stowe.spec --noconfirm

block_cipher = None

a = Analysis(
    ['run.py'],
    pathex=['.'],
    binaries=[],
    datas=[
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
    ],
    hookspath=['/opt/homebrew/lib/python3.11/site-packages/webview/__pyinstaller'],
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
        'CFBundleVersion': '0.6.0',
        'CFBundleShortVersionString': '0.6.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '11.0',
        'NSHumanReadableCopyright': 'Copyright (c) 2026 Connor Kay. MIT License.',
        # Allow WKWebView to load localhost
        'NSAppTransportSecurity': {
            'NSAllowsLocalNetworking': True,
        },
    },
)
