"""REAL test: insert dictated text into a Chromium (WebView2) text field.

WHY THIS TEST EXISTS
--------------------
The build spec requires dictation to work in "representative browser text
fields, including ChatGPT". Browsers do not expose a Win32 EDIT control: the
caret lives inside the render process, and Chromium is known to drop synthesized
keyboard input when events arrive too fast. A Win32 EDIT control passing
(tests/measure_insert_pacing.py) does NOT prove a browser field works.

WebView2 is the same Chromium engine Chrome and Electron apps embed, so this
exercises the real pipeline: SendInput -> Chromium input handling -> textarea.

It also proves the Enter policy in the one place it really matters: a newline is
injected as TEXT, and a submit handler listening on the form must NOT fire.

Run:  python tests/test_chromium_field.py
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._console import use_utf8_stdout  # noqa: E402

use_utf8_stdout()

import webview  # noqa: E402

from jarvis.win import insert as ins  # noqa: E402
from jarvis.win import session as sess  # noqa: E402
from jarvis.win import target as tgt  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.EnumWindows.argtypes = [ctypes.c_void_p, wt.LPARAM]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetForegroundWindow.restype = wt.HWND

TITLE = "Jarvis Chromium Field Test"

HTML = """
<!doctype html><html><head><meta charset="utf-8"><title>t</title>
<style>
  body{font-family:Segoe UI,sans-serif;background:#0a0e1a;color:#e9eefb;margin:0;padding:18px}
  textarea{width:96%;height:190px;background:#111726;color:#e9eefb;border:1px solid #232c44;
           border-radius:10px;padding:12px;font:15px/1.5 'Segoe UI',sans-serif}
  #log{margin-top:12px;color:#f87171;font-weight:600}
</style></head><body>
<h3>Type here (Chromium textarea)</h3>
<textarea id="t" autofocus></textarea>
<div id="log">submit fired: 0</div>
<script>
  window.__submits = 0;
  document.addEventListener('keydown', function(e){
    if (e.key === 'Enter' && !e.shiftKey) { window.__submits++; }
  });
  window.__focusBox = function(){ document.getElementById('t').focus(); };
  window.__value = function(){ return document.getElementById('t').value; };
  window.__clear = function(){ document.getElementById('t').value='';
    document.getElementById('t').focus(); };
  window.__sel = function(){
    var t=document.getElementById('t');
    return document.activeElement === t;
  };
  window.__submits = function(){ return window.__submits; };
</script></body></html>
"""

RESULTS: list[str] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    line = f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  :: {detail}" if detail else "")
    RESULTS.append(line)
    print(line)


def find_window_by_title(title: str, timeout: float = 20.0) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def cb(hwnd, _l):
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buf, 512)
            if buf.value == title:
                found.append(int(hwnd))
                return False
            return True

        user32.EnumWindows(cb, 0)
        if found:
            return found[0]
        time.sleep(0.25)
    return 0


def cname(h: int) -> str:
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(wt.HWND(h), b, 256)
    return b.value


def run_tests(window) -> int:
    """Runs on a WORKER thread while the main thread pumps the GUI loop.

    pywebview requires the main thread for `start()`, but `evaluate_js` must not
    be called from a thread that is blocking the message loop or it deadlocks.
    So: main thread owns the loop, this worker drives the test.
    """
    print("=" * 74)
    print("REAL Chromium (WebView2) text-field insertion test")
    print("=" * 74)

    if sess.is_locked():
        record("interactive desktop available", False,
               "BLOCKED :: the workstation is locked; SendInput cannot reach a "
               "Chromium field. Unlock and re-run.")
        return 2
    record("interactive desktop available (unlocked)", True,
           f"desktop={sess.input_desktop_name()!r}")

    hwnd = find_window_by_title(TITLE)
    if not hwnd:
        record("locate the Chromium window", False, "window never appeared")
        return 1
    record("locate the Chromium window", True, f"hwnd=0x{hwnd:X} class={cname(hwnd)}")
    time.sleep(1.5)

    if not tgt.force_foreground(hwnd):
        record("focus the Chromium window", False, "could not take the foreground")
        return 1
    record("focus the Chromium window", True)

    window.evaluate_js("window.__focusBox()")
    time.sleep(0.4)

    info = tgt.capture_target()
    record("capture_target sees the browser surface", info.hwnd != 0,
           f"process={info.process} class={info.class_name} "
           f"control={info.control_class!r} is_edit={info.is_edit} "
           f"is_browser={info.is_browser} blocked={info.blocked_reason}")

    # ---- 1. ASCII into a Chromium textarea -----------------------------
    window.evaluate_js("window.__clear()")
    time.sleep(0.3)
    text1 = "Jarvis browser field test 001."
    r1 = ins.insert_text(text1, info)
    time.sleep(0.6)
    got1 = window.evaluate_js("window.__value()")
    record("insert ASCII into a Chromium textarea",
           r1.ok and (got1 or "") == text1,
           f"method={r1.method} expected={text1!r} got={got1!r}")

    # ---- 2. Unicode, numbers, negation ---------------------------------
    window.evaluate_js("window.__clear()")
    time.sleep(0.3)
    text2 = "caf\u00e9 na\u00efve \u2014 1,250 not 12500 \u20ac12.50"
    info = tgt.capture_target()
    r2 = ins.insert_text(text2, info)
    time.sleep(0.6)
    got2 = window.evaluate_js("window.__value()")
    record("insert Unicode + numbers + negation into Chromium",
           (got2 or "") == text2,
           f"method={r2.method} expected={text2!r} got={got2!r}")

    # ---- 3. technical vocabulary survives verbatim ---------------------
    window.evaluate_js("window.__clear()")
    time.sleep(0.3)
    text3 = ("the user uses Codex, Claude, Supabase, TypeScript, npm, "
             "WooCommerce, Zoho, Kubernetes and Postgres; FastAPI versus GraphQL.")
    info = tgt.capture_target()
    r3 = ins.insert_text(text3, info)
    time.sleep(0.7)
    got3 = window.evaluate_js("window.__value()")
    record("technical vocabulary lands verbatim in Chromium",
           (got3 or "") == text3,
           f"method={r3.method} landed={len(got3 or '')}/{len(text3)} "
           f"{'exact' if (got3 or '') == text3 else 'MISMATCH'}")
    if (got3 or "") != text3:
        print(f"      got={got3!r}")

    # ---- 4. long text via the paste path -------------------------------
    window.evaluate_js("window.__clear()")
    time.sleep(0.3)
    text4 = "y" * (ins.PASTE_THRESHOLD_CHARS + 120)
    info = tgt.capture_target()
    r4 = ins.insert_text(text4, info)
    time.sleep(1.0)
    got4 = window.evaluate_js("window.__value()")
    record("long text into Chromium textarea",
           (got4 or "") == text4,
           f"method={r4.method} landed={len(got4 or '')}/{len(text4)}")

    # ---- 5. Enter policy: a newline must NOT submit ---------------------
    window.evaluate_js("window.__clear()")
    time.sleep(0.3)
    before_submits = int(window.evaluate_js("window.__submits()") or 0)
    info = tgt.capture_target()
    multiline = "first line\nsecond line"
    r5 = ins.insert_text(multiline, info)
    time.sleep(0.6)
    got5 = window.evaluate_js("window.__value()") or ""
    after_submits = int(window.evaluate_js("window.__submits()") or 0)
    record("multiline dictation reaches the field",
           "first line" in got5 and "second line" in got5,
           f"method={r5.method} got={got5!r}")
    record("NO Enter/submit was triggered in the browser",
           after_submits == before_submits,
           f"enter-keydowns before={before_submits} after={after_submits}")

    # ---- 6. a password field is refused even in Chromium ---------------
    pw = tgt.TargetInfo(hwnd=hwnd, pid=info.pid, process=info.process,
                        class_name=info.class_name, is_password=True)
    r6 = ins.insert_text("hunter2", pw)
    record("password field refused", (not r6.ok) and r6.method == "refused",
           f"method={r6.method} detail={r6.detail}")

    passed = sum(1 for r in RESULTS if r.startswith("PASS"))
    failed = sum(1 for r in RESULTS if r.startswith("FAIL"))
    print("=" * 74)
    print(f"TOTAL {len(RESULTS)}  PASSED {passed}  FAILED {failed}")
    print("=" * 74)

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "docs", "TEST_RESULTS_raw.txt")
    with open(out, "a", encoding="utf-8") as fh:
        fh.write(f"\n\n### tests/test_chromium_field.py  "
                 f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        for line in RESULTS:
            fh.write(f"- {line}\n")
        fh.write(f"- TOTAL {len(RESULTS)} PASSED {passed} FAILED {failed}\n")
    print(f"evidence appended to {out}")
    return 0 if failed == 0 else 1


def main() -> int:
    window = webview.create_window(TITLE, html=HTML, width=760, height=460)

    def _run_and_exit(win) -> None:
        # os._exit is the only way out: webview.start() owns the main thread and
        # will not return while the window lives. But os._exit skips interpreter
        # shutdown, so ANY buffered stdout is discarded - and stdout is block
        # buffered whenever it is a pipe rather than a console. That silently
        # threw away every PASS/FAIL line of this file under `> log.txt` or any
        # captured run (measured: 0 bytes written, exit code 1, no clue why).
        # Flush both streams first, unconditionally.
        rc = run_tests(win)
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        os._exit(rc)

    def after_start(win):
        # Main thread keeps pumping; the test runs on its own worker.
        threading.Thread(target=lambda: _run_and_exit(win),
                         name="chromium-test", daemon=True).start()

    webview.start(after_start, window, gui="edgechromium")
    return 0


if __name__ == "__main__":
    sys.exit(main())
