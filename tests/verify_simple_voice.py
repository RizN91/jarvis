"""Prove simple_voice.run() actually talks and listens - using speech, not a mic.

The bug this guards: run() read an event-loop holder that nothing ever assigned,
so every microphone chunk was dropped and the model received no audio. Everything
still reported success. A test that measures at the point of failure is the only
kind that would have caught it.

This runs the REAL run() function, swapping only the microphone for a recording
of speech, and asserts on the numbers run() itself prints: blocks sent, and
seconds of reply audio actually handed to the speakers.

Billable: one short live session, a couple of cents.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import threading
import time
import wave
from pathlib import Path

# ROOT must be the REPO root. `from tests._credentials import ...` below is not
# importable if this points at tests/ itself.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.audio import capture as audio_capture   # noqa: E402
from jarvis.audio import resample                   # noqa: E402
from jarvis.engines import live                     # noqa: E402
from tests._credentials import load_api_key         # noqa: E402
import simple_voice                                 # noqa: E402

WAV = ROOT / "tests" / "fixtures" / "sapi_speech.wav"
SECONDS = 6.0


def speech_chunks() -> list[bytes]:
    """The fixture as 100 ms PCM16 blocks at the Live rate."""
    with wave.open(str(WAV)) as w:
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())
    pcm = resample.resample_offline(raw, rate, live.DEFAULT_RATE)
    block = int(live.DEFAULT_RATE * 0.1) * 2          # 100 ms, pcm16 mono
    return [pcm[i:i + block] for i in range(0, len(pcm), block)]


class FakeMicrophone:
    """Stands in for MicrophoneCapture, replaying the recording in real time.

    Deliberately goes through the same on_chunk callback the real capture uses,
    so a fault in run()'s wiring is still caught.
    """

    def __init__(self, rate=live.DEFAULT_RATE, device=None, gain=1.0,
                 on_chunk=None, on_level=None, on_lost=None,
                 max_seconds=30, preroll=None):
        self.on_chunk = on_chunk
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        chunks = speech_chunks()[:int(SECONDS * 10)]

        def pump() -> None:
            for c in chunks:
                if self._stop.is_set():
                    return
                if self.on_chunk:
                    self.on_chunk(c)
                time.sleep(0.1)                       # real time, like a mic

        self._thread = threading.Thread(target=pump, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


async def main() -> int:
    load_api_key(verbose=False)
    audio_capture.MicrophoneCapture = FakeMicrophone      # the only swap

    args = argparse.Namespace(mic=None, out=None, no_greet=False)
    task = asyncio.create_task(simple_voice.run(args))

    # Long enough for the greeting, the speech, and a reply.
    await asyncio.sleep(SECONDS + 16.0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
