"""Render the REAL overlay pill into PNG sequences for the demo video.

This does not reimplement the pill. It imports `jarvis.win.overlay`, drives its
actual spring animation with `_advance()`, and calls its actual `_compose()` -
so every pixel in the video is what the shipped app draws, including the orb,
the glass bevel, the waveform meter and the wing slide. If the pill changes,
re-running this script is all it takes for the video to match.

Run:  ./.venv/Scripts/python.exe video/tools/make_pill.py
Out:  video/public/pill/<state>/NNN.png   (RGBA, 940 px wide)
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# Keep the render away from the user's real settings/history.
_TMP = Path(tempfile.mkdtemp(prefix="jarvis-video-"))
os.environ["BV_DATA_DIR"] = str(_TMP)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from jarvis.win.overlay import Overlay, PillState  # noqa: E402

FPS = 30
OUT = ROOT / "video" / "public" / "pill"

# A speech-like loudness envelope: syllables, not a sine wave. Without this the
# meter looks like a test pattern and the whole point of the animation is lost.
def speech_level(i: int, n: int, rng: np.random.Generator) -> float:
    t = i / max(n - 1, 1)
    # phrase shape: rise, hold, fall away at the end of the sentence
    phrase = 0.35 + 0.65 * np.sin(np.pi * min(1.0, t * 1.06)) ** 0.6
    syllable = 0.55 + 0.45 * np.sin(2 * np.pi * 4.1 * t + 0.5)
    jitter = 1.0 + rng.normal(0, 0.10)
    return float(np.clip(phrase * syllable * jitter, 0.02, 1.0))


def ambient_level(i: int, n: int, rng: np.random.Generator) -> float:
    """Quiet room tone while listening - a low, breathing meter."""
    t = i / max(n - 1, 1)
    return float(np.clip(0.10 + 0.10 * np.sin(2 * np.pi * 0.8 * t)
                         + rng.normal(0, 0.02), 0.02, 0.35))


# (dir name, PillState, frames, level fn)
SCENES: list[tuple[str, PillState, int, str]] = [
    ("idle", PillState(state="sleeping"), 30, "flat"),
    ("listening", PillState(state="listening", hint="Say “Hey Jarvis”"), 60, "ambient"),
    ("dictating", PillState(
        state="dictating",
        transcript="Hi Sarah — following up on the invoice from Tuesday, "
                   "could you confirm the PO number?",
    ), 150, "speech"),
    ("thinking", PillState(state="thinking",
                           transcript="What's the latest on the Vue 3.6 release?"), 45, "flat"),
    ("working", PillState(
        state="working",
        task="Searching the web",
        actions=["web_search"],
        progress=0.45,
        detail="two results so far",
    ), 90, "flat"),
    ("speaking", PillState(
        state="listening", speaking=True,
        transcript="Vue 3.6 lands in October, and it ships…",
    ), 120, "speech"),
    ("error", PillState(state="error", detail="Microphone is in use by another app"),
     45, "flat"),
]


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    out_root = OUT
    out_root.mkdir(parents=True, exist_ok=True)

    ov = Overlay()
    ov.reduced_motion = False
    ov._visible = True

    # The wings start closed; open them once so the springs have a sane origin
    # and the first frame of every sequence is not a half-drawn island.
    ov._appear = 1.0

    total = 0
    for name, state, frames, mode in SCENES:
        d = out_root / name
        d.mkdir(parents=True, exist_ok=True)
        rng = np.random.default_rng(hash(name) % 2**31)

        # Let the springs settle for this state before recording, so the
        # sequence begins fully open rather than mid-slide.
        ov._state = state
        for _ in range(180):
            ov._advance(state, 1.0 / 60)

        for i in range(frames):
            if mode == "speech":
                lvl = speech_level(i, frames, rng)
            elif mode == "ambient":
                lvl = ambient_level(i, frames, rng)
            else:
                lvl = 0.0
            ov._advance(state, 1.0 / FPS)
            # Two sub-steps of motion per frame keeps the spring smooth when the
            # source is 30 fps; a single step looks slightly steppy on the slide.
            ov._advance(state, 1.0 / FPS)
            img = ov._compose(state, lvl, i / FPS * 2.0)
            img.save(d / f"{i:03d}.png")
        total += frames
        print(f"  {name:<11} {frames:>4} frames  {ov.width}x{ov.height}")

    print(f"\n  {total} frames -> {out_root}")
    shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
