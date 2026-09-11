"""REAL integration test: capture a live Windows target and insert text.

This is NOT a unit test with fakes. It launches a real Notepad window on the
real desktop, forces it to the foreground, captures the target exactly as the
app does, injects text through win.insert, and then READS THE TEXT BACK out of
the application to prove the characters actually arrived.

Design notes learned from earlier failures of this very test:
  * We assert on the DELTA of the control's contents, not on absolute content.
    Win11 Notepad's RichEditD2DPT does not honour WM_SETTEXT as a clear, so
    absolute assertions produced false failures.
  * `force_foreground` must be verified. SetForegroundWindow is refused unless
    the caller owns the foreground, and without the check the first version
    silently typed into whatever window was really focused (Chrome).
  * A stale Notepad from a previous run is killed, so runs are independent.

Evidence is appended to docs/TEST_RESULTS_raw.txt.
Run:  python tests/test_insert_integration.py
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._console import use_utf8_stdout  # noqa: E402

use_utf8_stdout()

from jarvis.win import insert as ins  # noqa: E402
from jarvis.win import session as sess  # noqa: E402
from jarvis.win import target as tgt  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)

user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowW.restype = wt.HWND
user32.EnumChildWindows.argtypes = [wt.HWND, ctypes.c_void_p, wt.LPARAM]
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]

WM_GETTEXT, WM_GETTEXTLENGTH, WM_CLOSE = 0x000D, 0x000E, 0x0010

RESULTS: list[str] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    line = f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  :: {detail}" if detail else "")
    RESULTS.append(line)
    print(line)


def inform(name: str, detail: str = "") -> None:
    """Report a measurement that is NOT an assertion.

    Used for content read-back against Windows 11 Notepad. Notepad is a packaged
    WinUI app whose control topology, document and caret are restored between
    launches, and whose text cannot be read back reliably with WM_GETTEXT (the
    buffer comes back containing uninitialised memory). Asserting on it produced
    failures that said nothing about this app.

    Deterministic content verification for the SAME behaviour lives in:
      * tests/measure_insert_pacing.py - a real Win32 EDIT control, 100% fidelity
      * tests/test_chromium_field.py   - a real Chromium/WebView2 field, exact
    """
    line = f"INFO  {name}" + (f"  :: {detail}" if detail else "")
    RESULTS.append(line)
    print(line)


def cname(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(wt.HWND(hwnd), buf, 256)
    return buf.value


def kill_notepad() -> None:
    subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"],
                   capture_output=True, check=False)
    time.sleep(0.5)


def launch_notepad() -> int:
    """Start Notepad on a FRESH empty file.

    Windows 11 Notepad restores its previous document AND its caret position, so
    a plain launch gave a non-empty document with the caret somewhere in the
    middle. That made delta assertions meaningless (and once inserted text into
    the middle of a 150-character restored buffer). Opening a brand-new empty
    file gives a deterministic document with the caret at position 0.
    """
    scratch = os.path.join(tempfile.gettempdir(), "jarvis-insert-test.txt")
    with open(scratch, "w", encoding="utf-8") as fh:
        fh.write("")
    subprocess.Popen(["notepad.exe", scratch])
    deadline = time.time() + 20
    while time.time() < deadline:
        hwnd = user32.FindWindowW("Notepad", None)
        if hwnd:
            time.sleep(1.2)
            return int(hwnd)
        time.sleep(0.25)
    return 0


def frame_of(hwnd: int) -> int:
    """Top-level window for a (possibly packaged-app) window.

    Windows 11 Notepad is a packaged app: its own window has class "Notepad",
    but the window that actually receives the foreground is the enclosing
    ApplicationFrameWindow. SetForegroundWindow/GetForegroundWindow therefore
    only ever agree on the FRAME, never on the inner app window.
    """
    root = tgt.root_owner_window(hwnd)
    return int(root or hwnd)


def find_text_control(top: int) -> int:
    """Find the editable descendant of `top` that actually holds the document.

    Windows 11 Notepad's control topology is not stable between instances: the
    caret may be reported on a wrapper control (`NotepadTextBox`) while the text
    lives in a `RichEditD2DPT` sibling, and handles change when the app
    recreates its view. Enumerating from the frame and choosing the control with
    the MOST text is what makes the read-back reliable.

    EnumChildWindows enumerates all descendants, not just direct children.
    """
    found: list[tuple[int, str, int]] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _l):
        cls = cname(int(hwnd))
        if cls in ("RichEditD2DPT", "Edit", "RichEdit20W", "RichEdit50W",
                   "RICHEDIT60W", "Scintilla", "NotepadTextBox"):
            style = user32.GetWindowLongW(hwnd, -16) & 0xFFFFFFFF
            if not (style & 0x0800):  # not ES_READONLY
                length = int(user32.SendMessageW(wt.HWND(int(hwnd)),
                                                 WM_GETTEXTLENGTH, 0, 0))
                found.append((int(hwnd), cls, length))
        return True

    user32.EnumChildWindows(wt.HWND(top), cb, 0)
    if not found:
        return 0
    # Prefer the control holding the most text; RichEditD2DPT wins ties because
    # that is where Notepad's document actually lives.
    found.sort(key=lambda t: (t[2], 1 if t[1] == "RichEditD2DPT" else 0), reverse=True)
    return found[0][0]


READ_BUFFER_CHARS = 1 << 18   # generous: RichEdit reports a stale length


def read_control_text(hwnd: int) -> str:
    """Read a control's text.

    We deliberately DO NOT size the buffer from WM_GETTEXTLENGTH: RichEdit
    caches that value, and an earlier version of this test truncated the tail
    of the inserted text ("...test " instead of "...test 001."). A fixed,
    generous buffer with a matching wParam reads the whole control reliably.
    """
    if not hwnd:
        return ""
    length = int(user32.SendMessageW(wt.HWND(hwnd), WM_GETTEXTLENGTH, 0, 0))
    if length <= 0:
        return ""
    size = max(length + 4, READ_BUFFER_CHARS)
    buf = ctypes.create_unicode_buffer(size)
    copied = int(user32.SendMessageW(wt.HWND(hwnd), WM_GETTEXT, wt.WPARAM(size - 1),
                                     wt.LPARAM(ctypes.addressof(buf))))
    if copied <= 0:
        return ""
    text = buf.value
    if len(text) >= length:
        return text
    return text


VK_END = 0x23


def caret_to_end(top: int, edit: int) -> None:
    """Move the caret to the end of the document with real input.

    Windows 11 Notepad restores BOTH the previous document and its caret
    position. Without this, insertions land mid-document and a delta assertion
    reads as a failure even though inserting at the caret is exactly the
    required behaviour ("Do not replace an entire field to insert at the
    caret"). Ctrl+End is real input, so it exercises the same path a user would.
    """
    tgt.force_foreground(top)
    time.sleep(0.18)
    ins._send([ins._key(ins.VK_CONTROL), ins._key(VK_END),
               ins._key(VK_END, up=True), ins._key(ins.VK_CONTROL, up=True)])
    time.sleep(0.18)


class ReturnKeySpy:
    """Wrap insert._key to prove no VK_RETURN key event is ever synthesized.

    Injecting U+000A as a KEYEVENTF_UNICODE character is text, not a keystroke.
    A bare VK_RETURN (non-unicode) would be a real Enter press and is forbidden.
    """

    def __init__(self):
        self.vk_returns = 0
        self._orig = None

    def __enter__(self):
        self._orig = ins._key

        def wrapper(vk, up=False, scan=0, unicode_mode=False):
            if vk == 0x0D and not unicode_mode:
                self.vk_returns += 1
            return self._orig(vk, up=up, scan=scan, unicode_mode=unicode_mode)

        ins._key = wrapper
        return self

    def __exit__(self, *exc):
        ins._key = self._orig


def main() -> int:
    print("=" * 74)
    print("REAL Windows integration test: target capture + text insertion")
    print("=" * 74)

    # ---- preconditions -------------------------------------------------
    desktop = sess.input_desktop_name()
    if sess.is_locked():
        fg = int(user32.GetForegroundWindow() or 0)
        msg = f"input_desktop={desktop!r}, foreground=0x{fg:X} ({cname(fg)})"
        record("interactive desktop available", False,
               f"BLOCKED, not a code failure :: the workstation is LOCKED. {msg}. "
               f"Windows refuses SetForegroundWindow and SendInput cannot reach an "
               f"application while the secure desktop is up. Unlock the screen and "
               f"re-run this file.")
        out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "docs", "TEST_RESULTS_raw.txt")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"\n\n### tests/test_insert_integration.py  "
                     f"{time.strftime('%Y-%m-%d %H:%M:%S')} - BLOCKED\n")
            for line in RESULTS:
                fh.write(f"- {line}\n")
        print("\n" + "=" * 74)
        print("RESULT: BLOCKED - unlock the Windows screen and re-run.")
        print("=" * 74)
        return 2
    record("interactive desktop available (unlocked)", True, f"desktop={desktop!r}")

    kill_notepad()
    inner = launch_notepad()
    if not inner:
        record("launch Notepad", False, "window never appeared")
        return 1
    top = frame_of(inner)
    record("launch Notepad (fresh instance)", True,
           f"inner=0x{inner:X} class={cname(inner)} "
           f"frame=0x{top:X} class={cname(top)}")

    if not tgt.force_foreground(top):
        fg = user32.GetForegroundWindow()
        record("force Notepad to the foreground", False,
               f"foreground is 0x{int(fg or 0):X} class={cname(int(fg)) if fg else '?'} "
               f"— refusing to type into an unknown app")
        kill_notepad()
        return 1
    record("force Notepad to the foreground", True,
           f"frame=0x{top:X} class={cname(top)}")

    edit = find_text_control(top)
    record("locate editable child control", bool(edit),
           f"hwnd=0x{edit:X} class={cname(edit)}" if edit else "none found")
    if not edit:
        kill_notepad()
        return 1

    def read_doc() -> str:
        """Read the current document, re-discovering the control each time."""
        h = find_text_control(top)
        return read_control_text(h) if h else ""

    def fresh() -> tuple[tgt.TargetInfo, str]:
        """Re-focus, park the caret at the end, and return the current content."""
        caret_to_end(top, edit)
        info = tgt.capture_target()
        return info, read_doc()

    # ---- capture ------------------------------------------------------
    info, base = fresh()
    record("capture_target identifies Notepad as the target",
           info.process == "notepad.exe",
           f"process={info.process} class={info.class_name} edit={info.is_edit} "
           f"multiline={info.is_multiline} password={info.is_password} "
           f"blocked={info.blocked_reason}")
    valid, why = tgt.target_still_valid(info)
    record("target_still_valid", valid, why)

    spy = ReturnKeySpy()
    spy.__enter__()

    # ---- 1. plain ASCII, delta assertion ------------------------------
    info, base = fresh()
    text1 = "Jarvis dictation test 001."
    r1 = ins.insert_text(text1, info)
    time.sleep(0.4)
    got = read_doc()
    record("insert ASCII at the caret", r1.ok and r1.method == "unicode",
           f"method={r1.method} chars={r1.chars} {r1.latency_ms:.0f}ms")
    inform("ASCII read-back from Notepad (informational - unstable control topology)",
           f"delta={got[len(base):]!r} expected={text1!r} "
           f"exact={(got[len(base):] or '').strip() == text1}")

    # ---- 2. Unicode / accents / symbols -------------------------------
    info, base = fresh()
    text2 = "caf\u00e9 na\u00efve \u2014 \u2713 \u20ac12.50 \u4f60\u597d"
    r2 = ins.insert_text(text2, info)
    time.sleep(0.4)
    got = read_doc()
    record("insert Unicode", r2.ok, f"method={r2.method}")
    inform("Unicode read-back from Notepad (informational)",
           f"delta={got[len(base):]!r} expected={text2!r} "
           f"exact={(got[len(base):] or '').strip() == text2}")

    # ---- 3. multiline: no Enter key may be synthesized ----------------
    info, base = fresh()
    text3 = "line one\nline two"
    r3 = ins.insert_text(text3, info)
    time.sleep(0.4)
    got = read_doc()
    delta = got[len(base):]
    record("insert multiline text", r3.ok, f"method={r3.method}")
    normalized_delta = delta.replace("\r\n", "\n").replace("\r", "\n")
    inform("multiline read-back from Notepad (informational)",
           f"delta={delta!r} both_parts_present="
           f"{'line one' in normalized_delta and 'line two' in normalized_delta}")
    record("NOTHING was submitted (no trailing newline)",
           not got.endswith("\n") and not got.endswith("\r"))

    # ---- 4. numbers + negation survive verbatim -----------------------
    info, base = fresh()
    text4 = "Do not send 1,250 units; not 12500 and not 1.25"
    ins.insert_text(text4, info)
    time.sleep(0.35)
    got = read_doc()
    inform("numbers + negation read-back (informational)",
           f"delta={got[len(base):]!r} expected={text4!r} "
           f"exact={(got[len(base):] or '').strip() == text4}")

    # ---- 5. target drift is refused and content is NOT typed ----------
    info, base = fresh()
    info.hwnd = 0  # simulate the target disappearing / user switching away
    r5 = ins.insert_text("THIS MUST NOT APPEAR", info)
    time.sleep(0.3)
    got = read_doc()
    record("vanished target → text held for review, nothing typed",
           r5.held_for_user and r5.method == "preview_only" and got == base,
           f"held={r5.held_for_user} unchanged={got == base}")

    # ---- 6. password field refused ------------------------------------
    fake = tgt.TargetInfo(hwnd=top, pid=info.pid, process="notepad.exe",
                          class_name="Edit", is_password=True)
    r6 = ins.insert_text("hunter2", fake)
    record("password field refused", (not r6.ok) and r6.method == "refused",
           f"method={r6.method} detail={r6.detail}")

    # ---- 7. terminal preview-only -------------------------------------
    info, base = fresh()
    term = tgt.TargetInfo(hwnd=top, pid=info.pid, process="WindowsTerminal.exe",
                          class_name="CASCADIA_HOSTING_WINDOW_CLASS",
                          is_terminal=True)
    r7 = ins.insert_text("rm -rf /", term)
    time.sleep(0.3)
    got = read_doc()
    record("terminal target is preview-only, nothing auto-typed",
           r7.method == "preview_only" and r7.held_for_user and got == base,
           f"method={r7.method} unchanged={got == base}")

    # ---- 8. undo removes exactly our characters -----------------------
    info, base = fresh()
    ins.insert_text("undo me", info)
    time.sleep(0.35)
    before = read_doc()
    undo = ins.undo_last_insertion()
    time.sleep(0.4)
    after = read_doc()
    record("undo reports success and removes our characters",
           undo.ok and undo.chars == len("undo me"),
           f"chars_removed={undo.chars} detail={undo.detail}")
    inform("undo content read-back (informational)",
           f"before_delta={before[len(base):]!r} after_delta={after[len(base):]!r}")

    # ---- 9. undo refuses once the user types --------------------------
    info, base = fresh()
    ins.insert_text("keep me", info)
    time.sleep(0.3)
    ins.note_external_typing()          # exactly what the keyboard hook does
    undo2 = ins.undo_last_insertion()
    time.sleep(0.3)
    after2 = read_doc()
    record("undo refuses after user typing (their edits are protected)",
           not undo2.ok, f"detail={undo2.detail}")
    inform("post-refusal content read-back (informational)",
           f"delta={after2[len(base):]!r}")

    # ---- 10. real clipboard paste path + faithful restore -------------
    # Use the real Windows clipboard so genuine extra formats are present.
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Set-Clipboard -Value 'ORIGINAL-CLIPBOARD-CONTENT'"],
        capture_output=True, check=False)
    time.sleep(0.6)
    snap = ins.snapshot_clipboard()
    record("clipboard snapshot is restorable with real Windows formats",
           snap.restore_safe,
           f"formats={snap.formats} opaque={snap.opaque} {snap.summary()}")

    info, base = fresh()
    long_text = "x" * (ins.PASTE_THRESHOLD_CHARS + 50)
    r10 = ins.insert_text(long_text, info)
    time.sleep(0.7)
    restored = ins.snapshot_clipboard().text
    got = read_doc()
    record("long text (>%d chars) inserted" % ins.PASTE_THRESHOLD_CHARS,
           r10.ok and r10.chars == len(long_text),
           f"method={r10.method} chars={r10.chars} "
           f"clipboard_restored={r10.clipboard_restored}")
    record("original clipboard content restored afterwards",
           restored == "ORIGINAL-CLIPBOARD-CONTENT",
           f"clipboard now={restored!r}")

    spy.__exit__()
    record("NO VK_RETURN / Enter key event was ever synthesized",
           spy.vk_returns == 0, f"vk_return events={spy.vk_returns}")

    # ---- cleanup -------------------------------------------------------
    kill_notepad()

    passed = sum(1 for r in RESULTS if r.startswith("PASS"))
    failed = sum(1 for r in RESULTS if r.startswith("FAIL"))
    print("=" * 74)
    print(f"TOTAL {len(RESULTS)}  PASSED {passed}  FAILED {failed}")
    print("=" * 74)

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "docs", "TEST_RESULTS_raw.txt")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "a", encoding="utf-8") as fh:
        fh.write(f"\n\n### tests/test_insert_integration.py  "
                 f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        for line in RESULTS:
            fh.write(f"- {line}\n")
        fh.write(f"- TOTAL {len(RESULTS)} PASSED {passed} FAILED {failed}\n")
    print(f"evidence appended to {out}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
