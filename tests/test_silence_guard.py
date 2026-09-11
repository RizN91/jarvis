"""NON-BILLABLE test: silence must never produce a fabricated transcript.

Background: during loopback testing the microphone captured 11 seconds of
near-silence (peak -40 dBFS). With the user's vocabulary configured as keyword
hints, `gpt-transcribe` returned "the user." - invented text from no speech.

This test proves the guard in engines/transcribe.transcribe_captured refuses to
upload silent audio, so no billed call happens and no invented text can reach a
text field. It makes NO network calls, so it costs nothing and can run offline.

Run:  python tests/test_silence_guard.py
"""

from __future__ import annotations

import os
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._console import use_utf8_stdout  # noqa: E402

use_utf8_stdout()

from jarvis.audio import vad  # noqa: E402
from jarvis.engines import transcribe  # noqa: E402
from tests._credentials import FIXTURES  # noqa: E402

RATE = 24000
NEAR_SILENT = os.path.join(FIXTURES, "mic_loopback.wav")
REAL_SPEECH = os.path.join(FIXTURES, "sapi_speech.wav")

RESULTS: list[str] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    line = f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  :: {detail}" if detail else "")
    RESULTS.append(line)
    print(line)


def read_pcm(path: str) -> tuple[bytes, int]:
    with wave.open(path) as w:
        return w.readframes(w.getnframes()), w.getframerate()


def digital_silence(seconds: float = 4.0, rate: int = RATE) -> bytes:
    return b"\x00\x00" * int(seconds * rate)


def low_noise(seconds: float = 4.0, rate: int = RATE, amp: int = 12) -> bytes:
    """A quiet room: tiny random noise, no speech."""
    import numpy as np
    rng = np.random.default_rng(7)
    return (rng.integers(-amp, amp, size=int(seconds * rate))).astype("<i2").tobytes()


def main() -> int:
    print("=" * 74)
    print("NON-BILLABLE: silence / noise must not produce invented dictation")
    print("=" * 74)

    # ---- 1. the exact buffer that caused the bug ----------------------
    if os.path.exists(NEAR_SILENT):
        pcm, rate = read_pcm(NEAR_SILENT)
        info = vad.analyze(pcm, rate)
        record("the real near-silent capture is classified as 'no speech'",
               not info["has_speech"],
               f"{info['duration']}s peak={info['peak_db']}dBFS "
               f"rms={info['rms_db']}dBFS speech_ratio={info['speech_ratio']}")

        eng = transcribe.TranscribeEngine()
        res = eng.transcribe_captured(pcm, rate, keywords=["the user", "Codex",
                                                          "Supabase"])
        record("Economy path REFUSES the silent capture (no upload, no charge)",
               (not res.ok) and res.kind == "no_speech"
               and (not res.uploaded) and res.usd == 0.0,
               f"kind={res.kind} uploaded={res.uploaded} usd={res.usd} "
               f"reason={res.reason[:70]}")
    else:
        record("near-silent fixture present", False, f"{NEAR_SILENT} missing")

    # ---- 2. pure digital silence --------------------------------------
    sil = digital_silence()
    record("digital silence is not speech", not vad.has_speech(sil, RATE),
           f"speech_ratio={vad.analyze(sil, RATE)['speech_ratio']}")

    # ---- 3. a quiet room (low-level noise) ----------------------------
    noise = low_noise()
    info = vad.analyze(noise, RATE)
    record("a quiet room is not speech", not info["has_speech"],
           f"peak={info['peak_db']}dBFS speech_ratio={info['speech_ratio']}")

    # ---- 4. real speech IS detected ----------------------------------
    if os.path.exists(REAL_SPEECH):
        sp, srate = read_pcm(REAL_SPEECH)
        sinfo = vad.analyze(sp, srate)
        record("real speech is detected", sinfo["has_speech"],
               f"{sinfo['duration']}s peak={sinfo['peak_db']}dBFS "
               f"speech_ratio={sinfo['speech_ratio']}")

        trimmed = vad.trim_silence(sp, srate)
        record("trim_silence shortens silence but keeps the speech",
               0 < len(trimmed) <= len(sp),
               f"{len(sp)} -> {len(trimmed)} bytes "
               f"({100*len(trimmed)/max(1,len(sp)):.0f}% kept)")

        # Padding both ends of real speech must not make it look like silence.
        padded = digital_silence(2.0) + sp + digital_silence(2.0)
        ptrim = vad.trim_silence(padded, srate)
        record("leading/trailing silence is removed from real speech",
               len(ptrim) < len(padded),
               f"{len(padded)} -> {len(ptrim)} bytes")
        record("padded real speech is still detected as speech",
               vad.has_speech(padded, srate),
               f"speech_ratio={vad.analyze(padded, srate)['speech_ratio']}")

    # ---- 5. the guard runs BEFORE any network client is created --------
    class Exploding(transcribe.TranscribeEngine):
        def _sdk(self):
            raise AssertionError("the SDK must not be created for silent audio")

    try:
        r = Exploding().transcribe_captured(digital_silence(), RATE)
        record("no API client is constructed for silent audio",
               (not r.ok) and r.kind == "no_speech",
               "the silence check short-circuits before any network setup")
    except AssertionError as exc:
        record("no API client is constructed for silent audio", False, str(exc))

    passed = sum(1 for r in RESULTS if r.startswith("PASS"))
    failed = sum(1 for r in RESULTS if r.startswith("FAIL"))
    print("=" * 74)
    print(f"TOTAL {len(RESULTS)}  PASSED {passed}  FAILED {failed}   (cost: $0.00)")
    print("=" * 74)

    import time
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "docs", "TEST_RESULTS_raw.txt")
    with open(out, "a", encoding="utf-8") as fh:
        fh.write(f"\n\n### tests/test_silence_guard.py  "
                 f"{time.strftime('%Y-%m-%d %H:%M:%S')} (non-billable)\n")
        for line in RESULTS:
            fh.write(f"- {line}\n")
        fh.write(f"- TOTAL {len(RESULTS)} PASSED {passed} FAILED {failed}\n")
    print(f"evidence appended to {out}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
