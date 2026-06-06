# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for GPredict-Improved.

Bundles:
  - src/main.py (entry point)
  - src/web/static/  (FastAPI static files)
  - locale/          (i18n .mo files)
  - src/data/community_transmitters.json

Hamlib note:
  Linux  : collected from /opt/hamlib/4.7 or system path.
  macOS  : collected from Homebrew (brew install hamlib).
  Windows: DLLs pre-downloaded by CI into hamlib-win64/; Python binding
           copied to site-packages before this spec runs.
"""

import os
import sys
from pathlib import Path

block_cipher = None

# Repository root (one level up from scripts/)
ROOT = Path(SPECPATH).parent  # noqa: F821  (SPECPATH is injected by PyInstaller)
SRC = ROOT / "src"

# --------------------------------------------------------------------------- #
# Platform-specific Hamlib binary collection
# --------------------------------------------------------------------------- #
hamlib_binaries: list[tuple[str, str]] = []

if sys.platform == "win32":
    # CI places hamlib DLLs in hamlib-win64/bin/ relative to repo root
    hamlib_bin_dir = ROOT / "hamlib-win64" / "bin"
    if hamlib_bin_dir.exists():
        for dll in hamlib_bin_dir.glob("*.dll"):
            hamlib_binaries.append((str(dll), "."))

elif sys.platform == "darwin":
    # Homebrew hamlib dylibs
    brew_lib = Path("/opt/homebrew/lib")
    if not brew_lib.exists():
        brew_lib = Path("/usr/local/lib")
    for dylib in brew_lib.glob("libhamlib*.dylib"):
        hamlib_binaries.append((str(dylib), "."))

# Linux: Hamlib .so files come from LD_LIBRARY_PATH / rpath — no explicit copy needed.

# --------------------------------------------------------------------------- #
# Data files
# --------------------------------------------------------------------------- #
datas = [
    # Web UI static files
    (str(SRC / "web" / "static"), "web/static"),
    # i18n compiled catalogs
    (str(ROOT / "locale"), "locale"),
    # Community frequency database
    (str(SRC / "data" / "community_transmitters.json"), "data"),
]

# --------------------------------------------------------------------------- #
# Hidden imports (dynamic loaders that PyInstaller cannot auto-detect)
# --------------------------------------------------------------------------- #
hidden_imports = [
    # uvicorn workers / reload
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    # FastAPI / Starlette
    "starlette.routing",
    "starlette.staticfiles",
    "starlette.middleware",
    "starlette.middleware.cors",
    # Skyfield
    "skyfield",
    "skyfield.api",
    "skyfield.data",
    "skyfield.iokit",
    # Pydantic v2 core
    "pydantic",
    "pydantic.deprecated",
    "pydantic_core",
    # APScheduler
    "apscheduler",
    "apscheduler.schedulers.background",
    "apscheduler.triggers.interval",
    # zeroconf
    "zeroconf",
    "zeroconf._services",
    # qrcode
    "qrcode",
    "qrcode.image.pil",
    # Shapely
    "shapely",
    "shapely.geometry",
    # BeautifulSoup
    "bs4",
    # PySide6 extras used at runtime
    "PySide6.QtCharts",
    "PySide6.QtSvg",
    "PySide6.QtPrintSupport",
    # Alembic (DB migrations)
    "alembic",
    "alembic.config",
    "alembic.runtime",
    "alembic.runtime.migration",
]

# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
a = Analysis(
    [str(SRC / "main.py")],
    pathex=[str(SRC)],
    binaries=hamlib_binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "IPython",
        "jupyter",
        "notebook",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)  # noqa: F821

# --------------------------------------------------------------------------- #
# Executable
# --------------------------------------------------------------------------- #
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="gpredict-improved",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,   # GUI app — no terminal window on Windows/macOS
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="gpredict-improved",
)

# macOS: also build an .app bundle
if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name="GPredict-Improved.app",
        icon=None,
        bundle_identifier="org.gpredict.improved",
        info_plist={
            "NSPrincipalClass": "NSApplication",
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": "0.1.0",
        },
    )
