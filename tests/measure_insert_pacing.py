"""Measure how many injected characters actually land, per pacing setting.

REAL experiment, no fakes.

Some controls - WinUI/WinRT surfaces such as Windows 11 Notepad's RichEditD2DPT,
and Chromium/WebView2 editors - silently DROP synthesized keyboard input when
events arrive faster than the control processes them. A single bulk SendInput is
therefore not safe to assume.

This measures character-level fidelity against a real Win32 multiline EDIT
control (tests/harness_window.py) across several (chars-per-batch, delay)
combinations, then reports the fastest setting that loses nothing. The winner is
hard-coded in win/insert.py and cited in docs/TEST_RESULTS.md.

Run:  python tests/measure_insert_pacing.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._console import use_utf8_stdout  # noqa: E402

use_utf8_stdout()

from jarvis.win import insert as ins  # noqa: E402
from jarvis.win import session as sess  # noqa: E402
from jarvis.win import target as tgt  # noqa: E402
from tests.harness_window import EditHarness  # noqa: E402

SAMPLE = (
    "Jarvis pacing probe 0123456789 - supplier invoices, Codex, Claude, "
    "Supabase, TypeScript, npm, WooCommerce, Zoho, Kubernetes, Postgres."
)
UNICODE_SAMPLE = (
    "caf\u00e9 na\u00efve \u2014 12.5% \u20ac 你好 1234567890 "
    "Do not send 1,250; not 12500 and not 1.25."
)


def score(expected: str, got: str) -> float:
    """Character-level accuracy, so dropped characters are quantified."""
    if not expected:
        return 1.0
    matched = sum(1 for a, b in zip(expected, got) if a == b)
    return matched / len(expected)


def run_case(h: EditHarness, text: str, chunk: int, delay: float):
    """Insert `text` into a clean harness control and score what landed."""
    h.set_text("")
    time.sleep(0.15)
    h.focus_edit()
    tgt.force_foreground(h.hwnd)
    h.focus_edit()
    time.sleep(0.12)
    if h.read() != "":
        return None, "could not clear the test surface", 0.0

    info = tgt.capture_target(h.hwnd)
    old_chunk = ins.SENDINPUT_CHUNK
    ins.SENDINPUT_CHUNK = chunk
    try:
        started = time.monotonic()
        res = ins.insert_text(text, info)
        elapsed = (time.monotonic() - started) * 1000.0
    finally:
        ins.SENDINPUT_CHUNK = old_chunk
    time.sleep(0.45)
    got = h.read()
    return res, got, elapsed


def main() -> int:
    if sess.is_locked():
        print("BLOCKED: the workstation is locked; cannot inject input.")
        return 2

    h = EditHarness()
    if not h.start():
        print("FAIL: could not create the real Win32 test surface")
        return 1
    print(f"real Win32 surface: hwnd=0x{h.hwnd:X} edit=0x{h.edit:X} "
          f"class=EDIT multiline")
    try:
        if not h.focus_edit():
            print("WARN: the test surface could not take keyboard focus; "
                  "results may be meaningless")

        results = []
        print(f"\nsample length = {len(SAMPLE)} chars")
        for chunk, delay in [(64, 0.0), (32, 0.0), (16, 0.002), (8, 0.004),
                             (4, 0.004), (1, 0.003)]:
            res, got, elapsed = run_case(h, SAMPLE, chunk, delay)
            if res is None:
                print(f"chunk={chunk:>2} delay={delay*1000:.0f}ms -> "
                      f"SKIPPED ({got})")
                continue
            acc = score(SAMPLE, got)
            exact = got.strip() == SAMPLE
            results.append((chunk, delay, acc, exact, elapsed, len(got), res.method))
            print(f"chunk={chunk:>2} delay={delay*1000:.0f}ms -> "
                  f"accuracy={acc*100:5.1f}%  exact={exact}  "
                  f"landed={len(got)}/{len(SAMPLE)}  {elapsed:6.0f}ms  "
                  f"method={res.method}")
            if not exact:
                print(f"      got={got[:160]!r}")

        print(f"\nunicode sample length = {len(UNICODE_SAMPLE)} chars")
        uni_results = []
        for chunk, delay in [(64, 0.0), (8, 0.004), (1, 0.003)]:
            res, got, elapsed = run_case(h, UNICODE_SAMPLE, chunk, delay)
            if res is None:
                continue
            acc = score(UNICODE_SAMPLE, got)
            exact = got.strip() == UNICODE_SAMPLE
            uni_results.append((chunk, delay, acc, exact, elapsed))
            print(f"chunk={chunk:>2} delay={delay*1000:.0f}ms -> "
                  f"accuracy={acc*100:5.1f}%  exact={exact}  {elapsed:6.0f}ms")
            if not exact:
                print(f"      got={got[:160]!r}")

        perfect = [r for r in results if r[3]]
        print()
        if perfect:
            best = min(perfect, key=lambda r: r[4])
            print(f"BEST (fastest setting that lost NOTHING): chunk={best[0]} "
                  f"delay={best[1]*1000:.0f}ms  {best[4]:.0f}ms")
            if len(perfect) == len(results):
                print("NOTE: every setting was lossless on this control; the "
                      "target is not dropping bulk input.")
        else:
            print("WARNING: no setting was lossless on this control.")
            best = max(results, key=lambda r: r[2])
            print(f"BEST accuracy: chunk={best[0]} delay={best[1]*1000:.0f}ms "
                  f"= {best[2]*100:.1f}%")

        out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "docs", "TEST_RESULTS_raw.txt")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"\n\n### tests/measure_insert_pacing.py  "
                     f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            fh.write("- surface: real Win32 multiline EDIT control created in-process "
                     "(tests/harness_window.py)\n")
            fh.write(f"- ascii sample: {len(SAMPLE)} chars\n")
            for chunk, delay, acc, exact, elapsed, landed, method in results:
                fh.write(f"- ascii chunk={chunk:>2} delay={delay*1000:.0f}ms -> "
                         f"accuracy={acc*100:.1f}% exact={exact} "
                         f"landed={landed}/{len(SAMPLE)} elapsed={elapsed:.0f}ms "
                         f"method={method}\n")
            fh.write(f"- unicode sample: {len(UNICODE_SAMPLE)} chars\n")
            for chunk, delay, acc, exact, elapsed in uni_results:
                fh.write(f"- unicode chunk={chunk:>2} delay={delay*1000:.0f}ms -> "
                         f"accuracy={acc*100:.1f}% exact={exact} "
                         f"elapsed={elapsed:.0f}ms\n")
            fh.write(f"- CHOSEN: chunk={best[0]} delay={best[1]*1000:.0f}ms\n")
        print(f"evidence appended to {out}")
    finally:
        h.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
