"""Settings / setup-wizard window host.

ARCHITECTURE DECISION: THE SETTINGS WINDOW IS A SEPARATE PROCESS
-----------------------------------------------------------------
The obvious design is one process that owns both the tray/hooks/overlay and the
WebView2 settings UI. That was rejected for three reasons:

1. RESOURCES. WebView2 brings a tree of helper processes (~100 MB+). The tray
   process must stay small because it runs all day, and the build spec explicitly
   says to "avoid unnecessary resident runtimes" and to measure the whole process
   tree rather than just the main one. Keeping WebView2 in a child that exits
   when the window closes means the resident app never pays for it.

2. FOCUS. pywebview must own the main thread of whatever process runs it. The
   tray app's main thread is better spent not blocking on a GUI loop.

3. NO IPC SURFACE. The spec requires that localhost/IPC endpoints be
   authenticated and origin-restricted. The simplest way to satisfy that is to
   have NO endpoint: config is a JSON file and state is a SQLite database, both
   already designed for concurrent access, so the settings process reads and
   writes them directly. The tray process watches the config file's mtime and
   reloads. There is nothing to authenticate because nothing is listening.

`web_root()` resolves the web assets both from source and from a PyInstaller
one-folder bundle.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional


def web_root() -> Path:
    """Directory containing index.html / styles.css / app.js.

    PyInstaller one-folder builds place bundled data under sys._MEIPASS; the
    installer spec bundles `jarvis/ui/web` and must keep this path matching.
    """
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        candidate = Path(bundled) / "jarvis" / "ui" / "web"
        if candidate.exists():
            return candidate
        candidate = Path(bundled) / "ui" / "web"
        if candidate.exists():
            return candidate
    return Path(__file__).resolve().parent / "web"


def index_path() -> str:
    p = web_root() / "index.html"
    if not p.exists():
        raise FileNotFoundError(
            f"the settings UI assets are missing: {p}. If this is a packaged "
            f"build, the bundling path in installer/jarvis.spec must match "
            f"jarvis.ui.window.web_root()."
        )
    return str(p)


def _pywebview():
    try:
        import webview
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "pywebview is required for the settings window. Install it with: "
            "pip install pywebview pythonnet"
        ) from exc
    return webview


class SettingsHost:
    """Hosts the settings window and pumps its GUI loop.

    `start()` MUST be called on the main thread. `push()` and `open_window()`
    may be called from any thread.
    """

    TITLE = "Jarvis — Settings"

    def __init__(self, api=None, width: int = 1120, height: int = 780,
                 on_closed=None):
        self.api = api
        self.width = width
        self.height = height
        self.on_closed = on_closed
        self._window = None
        self._closed = False

    # ---------------------------------------------------------------- open
    def create(self):
        webview = _pywebview()
        if self._window is None:
            self._window = webview.create_window(
                self.TITLE, url=index_path(), js_api=self.api,
                width=self.width, height=self.height, min_size=(900, 640),
                background_color="#0a0e1a",
                text_select=True,
            )
            self._window.events.closed += self._handle_closed
        return self._window

    def _handle_closed(self) -> None:
        self._closed = True
        if self.on_closed:
            try:
                self.on_closed()
            except Exception:
                pass

    def start(self, func=None) -> None:
        """Run the GUI loop on the calling (main) thread."""
        webview = _pywebview()
        self.create()
        webview.start(func, self._window, gui="edgechromium")

    # --------------------------------------------------------------- push
    def push(self, event: str, payload: dict) -> bool:
        """Send an event to the page's bvBus handler."""
        if self._window is None:
            return False
        import json
        try:
            script = (f"window.bvBus && window.bvBus({json.dumps(event)}, "
                      f"{json.dumps(payload)})")
            self._window.evaluate_js(script)
            return True
        except Exception:
            return False

    def focus(self) -> None:
        if self._window is not None:
            try:
                self._window.show()
                self._window.restore()
            except Exception:
                pass

    @property
    def alive(self) -> bool:
        return self._window is not None and not self._closed


def run_settings_standalone(argv: Optional[list] = None) -> int:
    """Entry point for `python -m jarvis.ui.window` (the settings process)."""
    from .bridge import SettingsAPI

    api = SettingsAPI()
    # `--setup` is accepted for compatibility; which screen appears is decided by
    # the UI from setup_complete, so no start step is forced here.
    argv = list(argv if argv is not None else sys.argv[1:])
    host = SettingsHost(api=api)
    api.host = host
    host.start()
    return 0


if __name__ == "__main__":
    sys.exit(run_settings_standalone())
