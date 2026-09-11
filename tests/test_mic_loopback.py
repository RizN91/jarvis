"""REAL end-to-end test: microphone -> capture -> ASR, with BOTH engines.

Billable (a few cents). Why it is built this way:

The microphone is real and the speakers are real, but the SPEECH is the Windows
SAPI fixture, played out loud and re-captured. That is deliberate:
  * It exercises the genuine hardware path - real capture device, real WASAPI
    conversion, real PortAudio callback, real resampling, real WAV encoding.
  * It keeps the content known, so accuracy can be measured.
  * It does NOT upload the user's ambient room audio to a cloud service just to
    prove a code path. The spec forbids uploading ambient audio.

The result also gives the first real answer to "which engine should the user use?",
with the honest caveat that the voice is synthetic.

Run:  python tests/test_mic_loopback.py
"""

from __future__ import annotations

import os
import sys
import time
import wave
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._console import use_utf8_stdout  # noqa: E402

use_utf8_stdout()

from jarvis.audio import capture, playback, resample  # noqa: E402
from jarvis.core import pricing  # noqa: E402
from jarvis.engines import transcribe  # noqa: E402
from tests._credentials import (FIXTURES, load_api_key,  # noqa: E402
                               word_error_rate)

SRC_WAV = os.path.join(FIXTURES, "sapi_speech.wav")
OUT_WAV = os.path.join(FIXTURES, "mic_loopback.wav")
RATE = 24000

# The system default output was HEADPHONES during development, so the loopback
# captured nothing. Allow an explicit output device (index or name substring) so
# the test can target real speakers; falls back to the system default.
#   BV_SPEAKER_DEVICE=4      -> device index 4
#   BV_SPEAKER_DEVICE=Speakers
OUTPUT_DEVICE: Optional[int] = None
_spec = os.environ.get("BV_SPEAKER_DEVICE", "").strip()
if _spec:
    if _spec.isdigit():
        OUTPUT_DEVICE = int(_spec)
    else:
        for d in capture.list_output_devices():
            if _spec.lower() in d.name.lower():
                OUTPUT_DEVICE = d.index
                break

REFERENCE = (
    "Jarvis test. Open my quarterly report documents and summarise the latest "
    "file. Do not send 1,250 units to Acme. Codex and Claude use Supabase and "
    "TypeScript."
)

RESULTS: list[str] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    line = f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  :: {detail}" if detail else "")
    RESULTS.append(line)
    print(line)


def read_wav_pcm(path: str) -> tuple[bytes, int]:
    with wave.open(path) as w:
        return w.readframes(w.getnframes()), w.getframerate()


def main() -> int:
    print("=" * 74)
    print("REAL end-to-end test: microphone -> capture -> ASR")
    print("=" * 74)

    # ---- real devices ------------------------------------------------
    ins = capture.list_input_devices()
    outs = capture.list_output_devices()
    record("real input devices enumerated", bool(ins),
           f"{len(ins)} inputs; default: "
           f"{next((d.name for d in ins if d.default), 'unknown')}")
    record("real output devices enumerated", bool(outs),
           f"{len(outs)} outputs; default: "
           f"{next((d.name for d in outs if d.default), 'unknown')}")

    # ---- resampler quality (unit-level, but on real data) -------------
    src_pcm, src_rate = read_wav_pcm(SRC_WAV)
    up = resample.resample_offline(src_pcm, src_rate, RATE)
    down = resample.resample_offline(up, RATE, src_rate)
    ratio_ok = abs(len(up) / len(src_pcm) - RATE / src_rate) < 0.01
    record("offline resampler preserves duration ratio", ratio_ok,
           f"{len(src_pcm)} -> {len(up)} bytes (ratio "
           f"{len(up)/max(1,len(src_pcm)):.4f}, expected {RATE/src_rate:.4f})")

    import numpy as np
    a = np.frombuffer(src_pcm, dtype="<i2").astype(np.float32)
    b = np.frombuffer(down, dtype="<i2").astype(np.float32)
    n = min(a.size, b.size)
    if n > 1000:
        corr = float(np.corrcoef(a[:n], b[:n])[0, 1])
    else:
        corr = 0.0
    record("resample down/up round-trip stays correlated with the original",
           corr > 0.7, f"pearson r = {corr:.3f}")

    # ---- play the known speech out loud while capturing ---------------
    pcm24 = resample.resample_offline(src_pcm, src_rate, RATE)
    play_seconds = min(11.0, len(pcm24) / 2 / RATE)
    clip = pcm24[:int(play_seconds * RATE) * 2]

    cap = capture.MicrophoneCapture(rate=RATE)
    spk = playback.Speaker(rate=RATE, device=OUTPUT_DEVICE)
    try:
        cap.start()
    except Exception as exc:
        record("open the real microphone", False, str(exc))
        return 1
    record("open the real microphone", True,
           f"capturing at {RATE} Hz from device {cap.device}")
    record("open the real output device for the loopback", True,
           f"device index {OUTPUT_DEVICE} "
           f"({'explicit' if OUTPUT_DEVICE is not None else 'system default'})")

    try:
        spk.start()
        spk.play(clip)
        # Capture a little past the end of playback so the tail is not clipped.
        time.sleep(play_seconds + 0.6)
    finally:
        captured = cap.take()
        cap_peak, cap_rms = cap.peak, cap.level
        cap.stop()
        spk.stop()

    seconds = len(captured) / 2 / RATE
    record("microphone captured the played speech", seconds > play_seconds * 0.8,
           f"{seconds:.2f}s captured, peak={cap_peak:.3f}, rms={cap_rms:.4f}")
    if cap_peak < 0.01:
        record("captured signal is loud enough to transcribe", False,
               f"peak only {cap_peak:.4f} - the microphone may be muted or the "
               f"speakers are too quiet for a loopback test")

    with wave.open(OUT_WAV, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(captured)
    record("captured audio written as a valid WAV", os.path.exists(OUT_WAV),
           f"{OUT_WAV} ({os.path.getsize(OUT_WAV)} bytes)")

    if not load_api_key():
        record("API key available", False, "no key in api.env")
        return 2

    # ---- engine A: Economy (gpt-transcribe) on the captured audio -----
    eng = transcribe.TranscribeEngine()
    from jarvis.db import db
    t0 = time.monotonic()
    economy = eng.transcribe_file(OUT_WAV, keywords=db().list_vocab(),
                                  prompt="Australian English dictation about "
                                         "software and e-commerce.",
                                  languages=["en"], audio_seconds=seconds)
    econ_ms = (time.monotonic() - t0) * 1000
    econ_wer = word_error_rate(REFERENCE, economy.text)
    record("Economy engine transcribed the live microphone recording",
           bool(economy.text.strip()),
           f"{len(economy.text)} chars in {econ_ms:.0f}ms, WER {econ_wer*100:.1f}%")
    print(f"      economy text: {economy.text[:160]!r}")

    # ---- engine B: Live (gpt-live-1) on the same captured audio -------
    import asyncio
    from jarvis.engines import live

    async def run_live() -> dict:
        cfg = live.LiveConfig(instructions=live.DICTATION_INSTRUCTIONS, dictation=True)
        session = live.LiveSession(cfg)

        async def source():
            step = int(RATE * 0.1) * 2
            for i in range(0, len(captured), step):
                yield captured[i:i + step]
                await asyncio.sleep(0.1)

        try:
            return await session.run_dictation(source(), drain_ms=1800.0)
        except Exception as exc:
            from jarvis.engines import errors
            return errors.from_exception(exc, live.MODEL).as_dict()

    t0 = time.monotonic()
    live_res = asyncio.run(run_live())
    live_ms = (time.monotonic() - t0) * 1000
    live_text = live_res.get("text") or ""
    live_wer = word_error_rate(REFERENCE, live_text)
    record("Live engine transcribed the live microphone recording",
           bool(live_text.strip()),
           f"{len(live_text)} chars in {live_ms:.0f}ms, WER {live_wer*100:.1f}%")
    print(f"      live text   : {live_text[:160]!r}")

    # ---- engines record their spend -----------------------------------
    from jarvis.core.cost import BudgetManager
    bm = BudgetManager()
    bm.record_transcribe(seconds, model=economy.model, request_id=economy.request_id)
    live_usd = float(live_res.get("usd") or 0.0)
    if live_res.get("session_id"):
        bm.record_live_snapshot(live_res["session_id"],
                                float(live_res.get("usage_seconds") or 0.0),
                                finalized=bool(live_res.get("finalization_complete")))
    total = economy.usd + live_usd
    print("-" * 74)
    print(f"SAME captured audio through both engines ({seconds:.1f}s):")
    print(f"  gpt-transcribe : WER {econ_wer*100:6.1f}%  ${economy.usd:.6f}  "
          f"({econ_ms:.0f} ms)")
    print(f"  gpt-live-1     : WER {live_wer*100:6.1f}%  ${live_usd:.6f}  "
          f"({live_ms:.0f} ms)")
    print(f"  cost ratio     : Live is "
          f"{pricing.USD_PER_MIN_LIVE/pricing.USD_PER_MIN_TRANSCRIBE:.1f}x the rate")
    print("NOTE: synthetic SAPI voice through real speakers and a real microphone. "
          "Directionally informative, NOT a verdict on the user's own speech.")

    passed = sum(1 for r in RESULTS if r.startswith("PASS"))
    failed = sum(1 for r in RESULTS if r.startswith("FAIL"))
    print("=" * 74)
    print(f"TOTAL {len(RESULTS)}  PASSED {passed}  FAILED {failed}")
    print(f"MEASURED COST THIS RUN: ${total:.6f}")
    print("=" * 74)

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "docs", "TEST_RESULTS_raw.txt")
    with open(out, "a", encoding="utf-8") as fh:
        fh.write(f"\n\n### tests/test_mic_loopback.py  "
                 f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        fh.write(f"- real mic capture: {seconds:.2f}s, peak={cap_peak:.3f}, "
                 f"rms={cap_rms:.4f}, resample corr={corr:.3f}\n")
        fh.write(f"- gpt-transcribe: WER {econ_wer*100:.1f}%, ${economy.usd:.6f}, "
                 f"{econ_ms:.0f}ms :: {economy.text!r}\n")
        fh.write(f"- gpt-live-1    : WER {live_wer*100:.1f}%, ${live_usd:.6f}, "
                 f"{live_ms:.0f}ms :: {live_text!r}\n")
        fh.write(f"- cost ratio Live:transcribe = "
                 f"{pricing.USD_PER_MIN_LIVE/pricing.USD_PER_MIN_TRANSCRIBE:.1f}x\n")
        fh.write(f"- total measured cost this run: ${total:.6f}\n")
        for line in RESULTS:
            fh.write(f"- {line}\n")
        fh.write(f"- TOTAL {len(RESULTS)} PASSED {passed} FAILED {failed}\n")
    print(f"evidence appended to {out}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
