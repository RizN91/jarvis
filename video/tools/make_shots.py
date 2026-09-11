"""Record real footage of Jarvis dictating into a real application.

This does not simulate anything. It launches Notepad, focuses it, calls the
shipped `jarvis.win.insert.insert_text()` path to type the dictation, and shows
the shipped `jarvis.win.overlay.Overlay` window on screen at the same time —
then grabs the actual screen, frame by frame, while the text lands. So what the
video shows is the product, not an illustration of it.

Typing the sentence in small pieces to get a typewriter effect means calling the
insert path many times in a row, which is not what it is built for, and it is
occasionally flaky on Notepad's WinUI edit control — a character can be dropped
or doubled. So every take is VERIFIED: the document is read back through the
clipboard and compared against what was dictated, and a bad take is discarded
and retried. A misleading promo frame is worse than a slower script.

The read-back uses a clipboard sentinel, because "the document is empty" and
"Ctrl+C never reached the app, so I just read the old clipboard" are otherwise
indistinguishable — and the second one fabricates a pass out of nothing.

Run:  ./.venv/Scripts/python.exe video/tools/make_shots.py
Out:  video/public/live/NNN_*.png
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="jarvis-live-"))
os.environ["BV_DATA_DIR"] = str(_TMP)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import win32clipboard  # noqa: E402
from PIL import ImageGrab  # noqa: E402

from jarvis.win import insert as ins, target as tgt  # noqa: E402
from jarvis.win.overlay import Overlay, PillState, enable_dpi_awareness  # noqa: E402

OUT = ROOT / "video" / "public" / "live"

DICTATED = ("Hi Sarah — following up on the invoice from Tuesday. "
            "Could you confirm the PO number so I can get this processed today?")

CW, CH = 1500, 820
NOTEPAD_W, NOTEPAD_H = 1080, 540

VK_CTRL, VK_A, VK_C, VK_DELETE, VK_ESC = 0x11, 0x41, 0x43, 0x2E, 0x1B
PROBE = "\u0000JARVIS-PROBE\u0000"

MAX_TAKES = 3
CHUNK = 4            # characters per insert call
CHUNK_PAUSE = 0.22   # seconds between calls


# ------------------------------------------------------------------ input

def send_combo(*vks: int) -> None:
    inputs = [ins._key(VK_CTRL)]
    for vk in vks:
        inputs.append(ins._key(vk))
    for vk in reversed(vks):
        inputs.append(ins._key(vk, up=True))
    inputs.append(ins._key(VK_CTRL, up=True))
    ins._send(inputs)
    time.sleep(0.12)


def tap(vk: int) -> None:
    ins._send([ins._key(vk), ins._key(vk, up=True)])
    time.sleep(0.08)


def click_at(x: int, y: int) -> None:
    """Put the caret somewhere by clicking; focus alone is not enough."""
    u = ctypes.windll.user32
    u.SetCursorPos(int(x), int(y))
    time.sleep(0.1)
    u.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.04)
    u.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.22)


def clipboard_text() -> str:
    try:
        win32clipboard.OpenClipboard()
        try:
            return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT) or ""
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return ""


def set_clipboard(text: str) -> None:
    try:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        pass


def document_text() -> str | None:
    """The document's text, or None if the keystrokes never reached the app."""
    set_clipboard(PROBE)
    send_combo(VK_A)
    send_combo(VK_C)
    time.sleep(0.32)
    got = clipboard_text()
    return None if got == PROBE else got


# ------------------------------------------------------------------ windows

def screen_size() -> tuple[int, int]:
    u = ctypes.windll.user32
    return u.GetSystemMetrics(0), u.GetSystemMetrics(1)


def top_windows() -> set[int]:
    out: set[int] = set()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _l):
        out.add(hwnd)
        return True

    ctypes.windll.user32.EnumWindows(cb, 0)
    return out


def find_notepad(before: set[int], timeout: float = 15.0) -> int:
    u = ctypes.windll.user32
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        found: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def cb(hwnd, _l):
            if hwnd in before or not u.IsWindowVisible(hwnd):
                return True
            n = u.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            cls = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(hwnd, cls, 256)
            if "Notepad" in buf.value or cls.value.startswith("Notepad"):
                found.append(hwnd)
            return True

        u.EnumWindows(cb, 0)
        if found:
            return found[0]
        time.sleep(0.25)
    return 0


def copy_published_shots() -> None:
    """Stage the repo's real screenshots for the video's gallery scene."""
    dest = ROOT / "video" / "public" / "shots"
    dest.mkdir(parents=True, exist_ok=True)
    src = ROOT / "docs" / "images"

    wanted = [
        src / "settings-dark.webp",
        src / "settings-light.webp",
        src / "settings-compact.webp",
        src / "i18n-ar-settings.webp",
        src / "i18n-ar-wizard.webp",
    ]
    wanted += sorted((src / "settings").glob("*.webp"))

    n = 0
    for p in wanted:
        if not p.exists():
            continue
        name = p.name if p.parent.name != "settings" else f"s-{p.name}"
        shutil.copy2(p, dest / name)
        n += 1
    print(f"  staged {n} published screenshots -> {dest}")


# ------------------------------------------------------------------ the take

def main() -> None:
    enable_dpi_awareness()
    copy_published_shots()
    OUT.mkdir(parents=True, exist_ok=True)
    # Clear the previous take first: a shorter new take would otherwise leave
    # higher-numbered files behind, and the video would happily play them.
    for stale in OUT.glob("*.png"):
        stale.unlink()

    sw, _sh = screen_size()
    x = max(0, (sw - NOTEPAD_W) // 2)
    y = 200

    before = top_windows()
    proc = subprocess.Popen(["notepad.exe"])
    hwnd = find_notepad(before)
    if not hwnd:
        print("  !! could not find the Notepad window; aborting")
        proc.terminate()
        return
    print(f"  notepad hwnd={hwnd}")

    ctypes.windll.user32.SetWindowPos(hwnd, 0, x, y, NOTEPAD_W, NOTEPAD_H, 0x0040)
    time.sleep(1.5)
    tap(VK_ESC)                       # dismiss any Windows prompt that appeared
    time.sleep(0.4)
    tgt.force_foreground(hwnd)
    time.sleep(0.6)

    clip = ins.snapshot_clipboard()

    ov = Overlay()
    if not ov.create():
        print("  !! overlay.create() failed; aborting")
        _cleanup(None, proc)
        return
    ov.start()

    grab_x = max(0, (sw - CW) // 2)
    bbox = (grab_x, 0, grab_x + CW, CH)

    ok = False
    for attempt in range(1, MAX_TAKES + 1):
        for stale in OUT.glob("*.png"):
            stale.unlink()
        if _take(attempt, hwnd, ov, bbox, x, y):
            ok = True
            break
        print(f"  take {attempt} rejected; retrying")

    ins._restore_clipboard(clip)
    _cleanup(ov, proc)
    n = len(list(OUT.glob("*.png")))
    print(f"  {'KEPT' if ok else 'NO USABLE TAKE'} — {n} frames in {OUT}")
    if not ok:
        sys.exit(1)


def _take(attempt: int, hwnd: int, ov: Overlay, bbox, x: int, y: int) -> bool:
    """One attempt: empty the document, type it, verify it. True if accepted."""
    # ---- empty the document (Notepad restores its last session, so a fresh
    #      window is not reliably blank) ----
    for _ in range(4):
        click_at(x + NOTEPAD_W // 2, y + NOTEPAD_H // 2)
        send_combo(VK_A)
        tap(VK_DELETE)
        time.sleep(0.35)
        current = document_text()
        if current is None:
            continue
        if not current.strip():
            break
    else:
        print(f"  take {attempt}: could not empty the document")
        return False

    ov.show(PillState(state="listening", hint="Say “Hey Jarvis”"))
    time.sleep(1.0)

    frames = 0

    def snap(tag: str) -> None:
        nonlocal frames
        ImageGrab.grab(bbox=bbox, all_screens=True).save(OUT / f"{frames:03d}_{tag}.png")
        frames += 1

    snap("listening")
    ov.set_state("dictating")
    time.sleep(0.6)

    shown = ""
    refused = False
    for i in range(0, len(DICTATED), CHUNK):
        chunk = DICTATED[i:i + CHUNK]
        res = ins.insert_text(chunk, tgt.capture_target(hwnd), allow_clipboard=False)
        if not res.ok:
            print(f"  take {attempt}: insert refused at {i}: {res.detail}")
            refused = True
            break
        shown += chunk
        ov.set_state("dictating", transcript=shown.strip())
        ov.set_level(0.35)
        time.sleep(CHUNK_PAUSE)
        snap("typing")

    if refused:
        return False

    ov.set_state("dictating", detail="Typed at the cursor")
    time.sleep(0.8)
    snap("done")

    landed = document_text()
    if landed is None:
        print(f"  take {attempt}: could not read the document back")
        return False
    if landed.strip() != DICTATED.strip():
        print(f"  take {attempt}: MISMATCH after {len(landed)} chars")
        print(f"     got: {landed[:130]!r}")
        return False

    print(f"  take {attempt}: VERIFIED — the document holds exactly the "
          f"dictated sentence ({frames} frames)")
    return True


def _cleanup(ov: Overlay | None, proc: subprocess.Popen) -> None:
    if ov is not None:
        try:
            ov.hide()
            time.sleep(0.3)
            ov.stop()
        except Exception:
            pass
    time.sleep(0.2)
    subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"],
                   capture_output=True, text=True)
    proc.terminate()
    print("  cleaned up: overlay stopped, notepad closed")


if __name__ == "__main__":
    main()
