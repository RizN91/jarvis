# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec - Jarvis.

Build a portable, WINDOWED, ONE-FOLDER release:
    package.cmd
    python -m PyInstaller --noconfirm --clean installer/jarvis.spec

Result:
    dist/Jarvis/Jarvis.exe      (console=False -> no console window)
    dist/Jarvis/_internal/...       (Python runtime + dependencies)
Ship the whole dist/Jarvis folder; the exe needs _internal beside it.

---------------------------------------------------------------------------
BUNDLED ASSET PATH - DO NOT CHANGE ONE SIDE WITHOUT THE OTHER
---------------------------------------------------------------------------
The settings UI files (index.html, styles.css, app.js) live in the source
tree at jarvis/ui/web/ and are declared below as a data file with the
destination folder "jarvis/ui/web".

jarvis.ui.window.web_root() resolves that folder at runtime, and when it
is running frozen it looks under sys._MEIPASS, in this order:

    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        candidate = Path(bundled) / "jarvis" / "ui" / "web"   # <- matches
        if candidate.exists():
            return candidate
        candidate = Path(bundled) / "ui" / "web"                   # fallback

So the destination folder below MUST stay "jarvis/ui/web". If you move
the data files here, add the new location to web_root() as well, or the app
will start with a "settings UI assets are missing" error.
"""

from pathlib import Path

# SPECPATH is provided by PyInstaller and points at installer/ (this file).
ROOT = Path(SPECPATH).resolve().parent          # installer/ -> project root
APP = ROOT / "jarvis"

# ---------------------------------------------------------------- data files
# The WebView2 settings UI. Destination must match web_root() (see above).
datas = [
    (str(APP / "ui" / "web"), "jarvis/ui/web"),
]

# ------------------------------------------------------------ hidden imports
# Modules imported dynamically / only via COM / only through another package,
# which PyInstaller's static analysis can miss.
hiddenimports = [
    # pywebview + its Windows (edgechromium/WebView2) backend
    "webview",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "clr",                      # pythonnet
    "clr_loader",
    # pystray picks its backend at runtime
    "pystray._win32",
    # pywin32 submodules resolved lazily
    "win32timezone",
    "win32api",
    "win32con",
    "win32gui",
    "win32clipboard",
    # comtypes generates modules at runtime
    "comtypes",
    "comtypes.stream",
    # audio + wake word
    "sounddevice",
    "sherpa_onnx",
    "sentencepiece",
    # websockets subpackage used by the Live engine
    "websockets.asyncio.client",
    "websockets.legacy",
]

# ------------------------------------------------------------------- excludes
# Keep the bundle small: none of these are used by the app.
excludes = [
    "tkinter",
    "matplotlib",
    "pandas",
    "scipy",
    "sklearn",
    "PyQt5", "PyQt6", "PySide2", "PySide6",
    "pytest",
    "IPython",
    "tests",
]

a = Analysis(  # noqa: F821  (Analysis/EXE/COLLECT/PYZ are injected by PyInstaller)
    [str(APP / "__main__.py")],     # entry point for "python -m jarvis"
    pathex=[str(ROOT)],             # so "import jarvis" resolves
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,          # one-folder build
    name="Jarvis",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                  # windowed: never opens a console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,                        # <- jarvis/ui/web lands in the bundle
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Jarvis",
)
