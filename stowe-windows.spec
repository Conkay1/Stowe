# PyInstaller spec for Stowe Windows .exe
# Prerequisites: pip install pyinstaller pywebview
# Build: python -m PyInstaller stowe-windows.spec --noconfirm
# Output: dist\Stowe\Stowe.exe
# Then run: iscc stowe.iss  (requires Inno Setup)

import pathlib, webview as _wv

block_cipher = None

# Locate pywebview's bundled PyInstaller hooks without hardcoding a path.
_webview_hooks = str(pathlib.Path(_wv.__file__).parent / '__pyinstaller')

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
        # pywebview — include all platform backends; PyInstaller will tree-shake
        'webview',
        'webview.platforms.edgechromium',
        'webview.platforms.mshtml',
        'webview.platforms.win32',
        'webview.platforms.winforms',
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
    # Requires assets/stowe.ico — convert from assets/stowe.icns before building.
    # ImageMagick: magick convert assets/stowe.icns assets/stowe.ico
    icon='assets/stowe.ico',
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
