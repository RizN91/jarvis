"""Real WebView2 wizard navigation and screenshots, isolated profile, no billing."""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PROFILE = tempfile.TemporaryDirectory(prefix="jarvis_ui_")
os.environ["BV_DATA_DIR"] = PROFILE.name
from jarvis import secrets
secrets.TARGET_NAME = "Jarvis/__codex_ui_no_key__"
from jarvis.ui.bridge import SettingsAPI
from jarvis.ui.window import SettingsHost
from PIL import ImageGrab
from jarvis.win.overlay import enable_dpi_awareness
enable_dpi_awareness()

OUT = ROOT / "docs" / "codex"
OUT.mkdir(exist_ok=True)
checks = []
api = SettingsAPI()
host = SettingsHost(api=api, width=1280, height=900)
api.host = host


def check(name, ok):
    checks.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name, flush=True)


def capture(window, name):
    hwnd = window.native.Handle.ToInt64()
    rect = wt.RECT()
    user32 = ctypes.WinDLL("user32")
    user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
    try:
        user32.SetWindowPos(hwnd, wt.HWND(-1), 0, 0, 0, 0, 0x0013)
        time.sleep(.3)
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom),
                       all_screens=True).save(OUT / name)
    finally:
        user32.SetWindowPos(hwnd, wt.HWND(-2), 0, 0, 0, 0, 0x0013)


def run(window):
    try:
        for _ in range(100):
            if window.evaluate_js("!!document.querySelector('#app:not([hidden])')"):
                break
            time.sleep(.15)
        check("real bridge boots the wizard", window.evaluate_js(
            "document.querySelector('.hero h2').textContent.includes('Jarvis')"))
        window.move(30, 30)
        time.sleep(.8)
        capture(window, "setup-dark.png")
        check("12 setup steps", window.evaluate_js(
            "document.querySelectorAll('#navList .nav-item').length === 12"))
        for step in range(1, 13):
            check(f"step {step} renders", window.evaluate_js(
                f"document.querySelector('.wiz-count').textContent === 'Step {step} of 12'"))
            check(f"step {step} has no horizontal overflow", window.evaluate_js(
                "document.querySelector('#view').scrollWidth <= document.querySelector('#view').clientWidth + 1"))
            if step < 12:
                window.evaluate_js("document.querySelector('[data-act=wiz-next]').click()")
                time.sleep(.12)
        # Do not complete setup, run paid tests, or enable autostart/wake.
        window.evaluate_js("document.querySelector('[data-act=wizard-restart]').click()")
        time.sleep(.2)
        window.evaluate_js("document.querySelector('[data-act=theme]').click()")
        time.sleep(.3)
        capture(window, "setup-light.png")
        check("light theme applies", window.evaluate_js(
            "document.documentElement.dataset.theme === 'light'"))
    except Exception as exc:
        check(f"UI exception: {exc}", False)
    finally:
        window.destroy()


if __name__ == "__main__":
    host.start(run)
    print(f"TOTAL {len(checks)} PASSED {sum(checks)} FAILED {len(checks)-sum(checks)}", flush=True)
    from jarvis.db import db
    db().close()
    import logging
    logging.shutdown()
    PROFILE.cleanup()
    sys.exit(0 if checks and all(checks) else 1)
