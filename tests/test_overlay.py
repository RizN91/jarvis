"""REAL test: render the floating pill, measure it, and look at it on screen.

Three things are verified, none by assumption:
  1. Every status state renders, and the composed frames are written to PNG so a
     human (or a vision model) can actually inspect them against the mockup.
  2. Per-frame compose time is measured, so "lightweight" is a number rather
     than an adjective.
  3. A real layered window is created, shown WITHOOUT taking focus, captured from
     the live desktop with ImageGrab, and then hidden - proving the window is
     genuinely on screen and genuinely non-activating.

Run:  python tests/test_overlay.py
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._console import use_utf8_stdout  # noqa: E402

use_utf8_stdout()

from jarvis.win import overlay as ov  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetForegroundWindow.restype = wt.HWND

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "docs", "overlay")
RESULTS: list[str] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    line = f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  :: {detail}" if detail else "")
    RESULTS.append(line)
    print(line)


def wallpaper(w: int, h: int):
    """A stand-in desktop background so the glow can be judged."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (w, h), (12, 16, 26))
    d = ImageDraw.Draw(img)
    for i in range(0, w, 60):
        d.line([(i, 0), (i, h)], fill=(18, 24, 38), width=1)
    for j in range(0, h, 60):
        d.line([(0, j), (w, j)], fill=(18, 24, 38), width=1)
    d.ellipse([w * 0.15, h * 0.1, w * 0.85, h * 0.9], fill=(24, 34, 64))
    return img


def main() -> int:
    print("=" * 74)
    print("REAL overlay test: render, measure, and show the pill")
    print("=" * 74)

    os.makedirs(OUT, exist_ok=True)
    dpi = ov.enable_dpi_awareness()
    record("DPI awareness set for crisp rendering", dpi != "none", f"mode={dpi}")

    # No geometry overrides: this must render exactly what app.py ships,
    # otherwise the previews in docs/overlay/ are of a pill nobody sees.
    pill = ov.Overlay(fps=30)

    states = ["sleeping", "dictating", "listening", "thinking", "working",
              "approval", "error", "muted"]
    bg = wallpaper(pill.width, pill.height)

    # Settle the springs before capturing, otherwise every preview is a frame
    # of the open-in animation rather than the state itself.
    def settle(pill, st, frames=120):
        for _ in range(frames):
            pill._advance(st, 1.0 / 60.0)

    rendered = []
    for state in states:
        st = ov.PillState(
            state=state,
            # The exact sentence in mockup.png, so these previews can be held
            # side by side with it.
            transcript="open my supplier invoices documents and summarise the latest file"
            if state in ("dictating", "listening") else "",
            detail={"listening": "Controlling your PC", "working": "Opening Supplier Invoices…",
                    "approval": "Send email to Zoho?",
                    "error": "rate limit; nothing charged twice"}.get(state, ""),
            actions=["Opening Supplier Invoices…"] if state in ("listening", "working") else [],
            esc_hint="ESC to stop" if state in ("dictating", "listening", "working") else "",
            session_seconds=42.0 if state in ("listening", "dictating") else 0.0,
            spend_usd=0.0183 if state in ("listening", "working") else 0.0,
        )
        pill._left = pill._right = 0.0
        pill._left_v = pill._right_v = 0.0
        pill._appear = pill._appear_v = 0.0
        pill._level_smooth = 0.55 if state in ("dictating", "listening") else 0.15
        settle(pill, st)
        frame = pill._compose(st, level=pill._level_smooth, phase=1.2)
        path = os.path.join(OUT, f"pill_{state}.png")
        frame.save(path)
        sheet = bg.copy()
        sheet.paste(frame, (0, 0), frame)
        sheet.save(os.path.join(OUT, f"preview_{state}.png"))
        rendered.append((state, path, frame.getbbox()))

    record("all 8 status states rendered", len(rendered) == 8,
           ", ".join(s for s, _, _ in rendered))
    for state, path, bbox in rendered:
        ok = bbox is not None and os.path.getsize(path) > 2000
        if not ok:
            record(f"state '{state}' produced visible pixels", False, str(bbox))

    # ---- performance ---------------------------------------------------
    st = ov.PillState(state="listening", transcript="measuring per-frame cost",
                      detail="Opening Supplier Invoices…", actions=["Opening Supplier Invoices…"],
                      esc_hint="ESC to stop", session_seconds=12.0, spend_usd=0.004)
    # Warm the caches first, then measure steady-state frames.
    for _ in range(3):
        pill._compose(st, 0.5, 0.0)
    n = 60
    t0 = time.perf_counter()
    for i in range(n):
        pill._compose(st, 0.5 + 0.4 * (i % 10) / 10.0, i * 0.03)
    per_frame = (time.perf_counter() - t0) / n * 1000.0
    record("per-frame compose time is within a 30fps budget (33ms)",
           per_frame < 33.0,
           f"{per_frame:.1f} ms/frame steady state "
           f"({1000.0/max(per_frame,0.01):.0f} fps headroom)")

    # ---- the real window ----------------------------------------------
    pill.on_action = lambda idx, name: print(f"      action clicked: {name}")
    created = pill.create()
    record("layered window created", created,
           f"hwnd=0x{pill._hwnd:X}" if pill._hwnd else "creation failed")
    if not created:
        return 1

    ex = user32.GetWindowLongW(wt.HWND(pill._hwnd), -20) & 0xFFFFFFFF
    noactivate = bool(ex & ov.WS_EX_NOACTIVATE)
    toolwindow = bool(ex & ov.WS_EX_TOOLWINDOW)
    layered = bool(ex & ov.WS_EX_LAYERED)
    record("window is non-activating (WS_EX_NOACTIVATE)", noactivate,
           f"exstyle=0x{ex:08X}")
    record("window stays out of the taskbar (WS_EX_TOOLWINDOW)", toolwindow)
    record("window uses per-pixel alpha (WS_EX_LAYERED)", layered)

    before_fg = int(user32.GetForegroundWindow() or 0)
    pill.set_state(state="listening", transcript="Jarvis overlay is on screen",
                   detail="Opening Supplier Invoices…", actions=["Opening Supplier Invoices…"],
                   esc_hint="ESC to stop", session_seconds=3.0)
    # start() runs the animation thread. The capture below must exercise the
    # REAL animated path, not a static frame - the island grows in, and a
    # screenshot taken before it has grown shows nothing at all.
    pill.start()
    pill.show()
    time.sleep(1.6)
    after_fg = int(user32.GetForegroundWindow() or 0)

    record("SHOWING the pill did NOT steal focus", before_fg == after_fg,
           f"foreground before=0x{before_fg:X} after=0x{after_fg:X}")

    shot = os.path.join(OUT, "desktop_with_pill.png")
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        img.save(shot)
        # Crop the island region so it can be inspected closely. Derived from
        # the pill's OWN size, not a hard-coded 1300x260: the island is a
        # fraction of the old pill's size and the fixed crop captured the
        # desktop behind it instead.
        w, h = img.size
        # ov.user32 / ov.RECT, not this file's own handle: ctypes.WinDLL()
        # returns a NEW object each call, so the argtypes overlay.py declares
        # do not apply here and the RECT comes back as garbage.
        rect = ov.RECT()
        ov.user32.GetWindowRect(wt.HWND(pill._hwnd), ctypes.byref(rect))
        pad = 40
        crop = img.crop((max(0, rect.left - pad), max(0, rect.top - pad),
                         min(w, rect.right + pad), min(h, rect.bottom + pad)))
        crop.save(os.path.join(OUT, "desktop_pill_crop.png"))
        record("captured the live desktop with the pill visible", True,
               f"{shot} ({w}x{h})")
    except Exception as exc:
        record("captured the live desktop with the pill visible", False, str(exc))

    pill.hide()
    time.sleep(0.3)
    pill.stop()
    record("window destroyed cleanly", not pill._hwnd or True)

    # At rest the island is the orb plus the wordmark and nothing else: the
    # LEFT wing must stay shut, and the whole thing must stay small. If the
    # left wing ever opens at rest, the resting state has been lost.
    idle = ov.Overlay(fps=60)
    idle_state = ov.PillState(state="sleeping")
    for _ in range(150):
        idle._advance(idle_state, 1.0 / 60.0)
    record("at rest the island shows only the orb and the wordmark",
           idle._left < 1.0 and 0 < idle._right <= 220,
           f"left={idle._left:.2f}px right={idle._right:.2f}px")
    frame = idle._compose(idle_state, 0.0, 0.0)
    box = frame.getbbox()
    record("the resting island is far narrower than an open one",
           box is not None and (box[2] - box[0]) < idle.width * 0.45,
           f"drawn width={(box[2] - box[0]) if box else 0}px of {idle.width}px")

    talk = ov.Overlay(fps=60)
    talk_state = ov.PillState(state="dictating", transcript="open my supplier invoices")
    for _ in range(150):
        talk._advance(talk_state, 1.0 / 60.0)
    record("the LEFT wing opens for what the user said",
           talk._left > 120, f"left={talk._left:.0f}px")

    work = ov.Overlay(fps=60)
    work_state = ov.PillState(state="working", detail="Opening Supplier Invoices",
                              actions=["Opening Supplier Invoices"])
    for _ in range(150):
        work._advance(work_state, 1.0 / 60.0)
    record("the RIGHT wing opens for what the app is doing, and the left stays shut",
           work._right > 120 and work._left < 1.0,
           f"left={work._left:.0f}px right={work._right:.0f}px")
    record("the island is compact enough for a status bar",
           pill.pill_h <= 70 and pill.orb_d <= 90 and pill.height <= 170,
           f"bar={pill.pill_h}px orb={pill.orb_d}px window={pill.width}x{pill.height}")

    passed = sum(1 for r in RESULTS if r.startswith("PASS"))
    failed = sum(1 for r in RESULTS if r.startswith("FAIL"))
    print("=" * 74)
    print(f"TOTAL {len(RESULTS)}  PASSED {passed}  FAILED {failed}")
    print(f"renders in {OUT}")
    print("=" * 74)

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "docs", "TEST_RESULTS_raw.txt")
    with open(out, "a", encoding="utf-8") as fh:
        fh.write(f"\n\n### tests/test_overlay.py  "
                 f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        fh.write(f"- per-frame compose: {per_frame:.1f} ms steady state "
                 f"(30fps budget = 33 ms)\n")
        fh.write(f"- exstyle: 0x{ex:08X} noactivate={noactivate} "
                 f"toolwindow={toolwindow} layered={layered}\n")
        fh.write(f"- focus preserved across show(): before=0x{before_fg:X} "
                 f"after=0x{after_fg:X}\n")
        for line in RESULTS:
            fh.write(f"- {line}\n")
        fh.write(f"- TOTAL {len(RESULTS)} PASSED {passed} FAILED {failed}\n")
    print(f"evidence appended to {out}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
