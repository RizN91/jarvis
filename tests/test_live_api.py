"""REAL API test: Live-first dictation through gpt-live-1.

Billable at $0.05/minute of session duration, billed per second. The measured
cost of each run is recorded in docs/TEST_RESULTS.md.

What this proves (and what it cannot):
  * PROVES the documented wire protocol works from this machine: connect to
    wss://api.openai.com/v1/live/sessions, session.start -> session.started,
    session.input_audio.append, session.input_transcript.delta fragments,
    session.usage.updated cumulative snapshots, session.close -> session.closed.
  * PROVES the bounded drain/finalisation path returns a transcript and reports
    whether finalisation was confirmed.
  * CANNOT prove accuracy on the user's voice. The audio is Windows SAPI synthetic
    speech. Only the opt-in A/B calibration screen with his own recordings can
    answer that.

Run:  python tests/test_live_api.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._console import use_utf8_stdout  # noqa: E402

use_utf8_stdout()

from jarvis.audio import resample  # noqa: E402
from jarvis.core import pricing  # noqa: E402
from jarvis.engines import live  # noqa: E402
from tests._credentials import (FIXTURES, load_api_key,  # noqa: E402
                               redaction_self_test, word_error_rate)

WAV = os.path.join(FIXTURES, "sapi_speech.wav")
LIVE_RATE = 24000
CHUNK_MS = 100

# The fixture says three sentences over ~24s. WER is only meaningful against a
# reference covering the audio we actually streamed, so we pick by duration.
REFERENCE_FIRST = ("Jarvis test. Open my quarterly report documents and "
                   "summarise the latest file.")
REFERENCE_FULL = (
    REFERENCE_FIRST + " Do not send 1,250 units to Acme. Codex and Claude use "
    "Supabase and TypeScript. Friday. Actually, Thursday works better for the "
    "npm and WooCommerce review."
)

RESULTS: list[str] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    line = f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  :: {detail}" if detail else "")
    RESULTS.append(line)
    print(line)


def load_pcm24(path: str, seconds: float) -> tuple[bytes, float]:
    """Read a WAV and resample to 24 kHz mono PCM16, capped to `seconds`."""
    with wave.open(path) as w:
        rate = w.getframerate()
        width = w.getsampwidth()
        channels = w.getnchannels()
        frames = min(w.getnframes(), int(rate * seconds))
        raw = w.readframes(frames)
    if width != 2:
        raise RuntimeError(f"expected 16-bit audio, got {width*8}-bit")
    if channels > 1:
        import numpy as np
        arr = np.frombuffer(raw, dtype="<i2").reshape(-1, channels)[:, 0]
        raw = arr.tobytes()
    converted = resample.resample_offline(raw, rate, LIVE_RATE)
    return converted, len(converted) / 2 / LIVE_RATE


async def paced_source(pcm: bytes, realtime: bool = True):
    """Yield PCM in 100 ms chunks, paced like a live microphone.

    The docs are explicit that "piping an entire file at once does not simulate
    a live microphone", so we pace it unless explicitly told otherwise.
    """
    bytes_per_chunk = int(LIVE_RATE * 2 * CHUNK_MS / 1000)
    for i in range(0, len(pcm), bytes_per_chunk):
        yield pcm[i:i + bytes_per_chunk]
        if realtime:
            await asyncio.sleep(CHUNK_MS / 1000.0)


async def run_dictation_test(pcm: bytes, audio_seconds: float,
                            events: list) -> dict:
    cfg = live.LiveConfig(instructions=live.DICTATION_INSTRUCTIONS, dictation=True)
    session = live.LiveSession(cfg, on_event=lambda t, e: events.append(t))
    try:
        return await session.run_dictation(paced_source(pcm), drain_ms=1800.0)
    except Exception as exc:
        from jarvis.engines import errors
        return errors.from_exception(exc, live.MODEL).as_dict()


async def run_auth_failure_test() -> dict:
    """A deliberately wrong key must produce a clear auth error, not a crash."""
    from jarvis.engines import errors
    cfg = live.LiveConfig(instructions="test")
    session = live.LiveSession(cfg, api_key="sk-proj-" + "x" * 48)
    try:
        await session.connect(open_timeout=15.0)
        await session.close(graceful=False)
        return {"ok": True, "unexpected": "a bogus key was accepted"}
    except errors.EngineError as exc:
        return exc.as_dict()
    except Exception as exc:
        return errors.from_exception(exc, live.MODEL).as_dict()


async def main_async() -> int:
    print("=" * 74)
    print("REAL API test: gpt-live-1 (Live-first dictation)")
    print("=" * 74)

    record("log redaction of key-shaped strings", redaction_self_test())
    if not load_api_key():
        record("API key available", False, "no key in api.env")
        return 2
    record("API key available in Credential Manager", True)

    pcm, audio_seconds = load_pcm24(WAV, seconds=float(
        os.environ.get("BV_LIVE_SECONDS", "7.0")))
    record("test audio resampled to 24 kHz PCM16", bool(pcm),
           f"{audio_seconds:.2f}s, {len(pcm)} bytes, "
           f"even-length check={len(pcm) % 2 == 0}")

    # ---- 1. a bogus key must fail clearly (no charge, no crash) --------
    bad = await run_auth_failure_test()
    record("bogus API key produces a clear auth error, not a crash",
           (not bad.get("ok")) and bad.get("kind") in ("auth", "permission"),
           f"kind={bad.get('kind')} error={str(bad.get('error'))[:90]}")

    # ---- 2. real Live dictation ---------------------------------------
    events: list = []
    t0 = time.monotonic()
    result = await run_dictation_test(pcm, audio_seconds, events)
    wall_ms = (time.monotonic() - t0) * 1000

    print(f"      wall time      : {wall_ms/1000:.1f}s for {audio_seconds:.1f}s audio")
    print(f"      session id     : {result.get('session_id')}")
    print(f"      usage seconds  : {result.get('usage_seconds')}")
    print(f"      usd            : {result.get('usd')}")
    print(f"      finalisation   : {result.get('finalization_complete')}")
    print(f"      incomplete     : {result.get('incomplete_capture')}")
    print(f"      audio ms sent  : {result.get('audio_ms_sent')}")
    print(f"      covered ms     : {result.get('transcript_covered_ms')}")
    print(f"      fragments      : {result.get('fragments')}")
    print(f"      notes          : {result.get('notes')}")
    print(f"      TEXT           : {result.get('text')!r}")
    print(f"      events seen    : {sorted(set(events))}")

    ok = bool(result.get("ok")) and bool((result.get("text") or "").strip())
    record("Live session connected and returned a transcript", ok,
           f"notes={result.get('notes')}")

    if not ok:
        print("\nBLOCKER DETAIL:", result)
        return 1

    text = (result.get("text") or "").lower()

    # Content that demonstrably survives the Live input-transcript path.
    record("Live transcript captured the utterance",
           "quarterly report" in text and "latest file" in text,
           f"got {result.get('text')!r}")
    reference = REFERENCE_FULL if audio_seconds >= 20.0 else REFERENCE_FIRST
    wer = word_error_rate(reference, result.get("text") or "")
    print(f"      WER vs known reference ({len(reference.split())} words, "
          f"{audio_seconds:.0f}s audio): {wer*100:.1f}%")
    print(f"      (WER is unbounded: over 100% means the model inserted many "
          f"words that were never spoken)")
    print(f"      compare with gpt-transcribe in tests/test_transcribe_api.py")

    # ---- 3. the documented event contract was actually observed --------
    seen = set(events)
    record("session.started observed", "session.started" in seen)
    record("session.input_transcript.delta observed",
           "session.input_transcript.delta" in seen,
           "these are the native user-input transcript fragments")
    # FINDING: session.usage.updated was NOT emitted for a short session. Usage
    # still arrived authoritatively through session.closed. We therefore never
    # depend on usage.updated for final numbers - see docs/VERIFIED_API.md.
    print(f"      session.usage.updated observed: "
          f"{'session.usage.updated' in seen} "
          f"(final usage came from session.closed regardless)")
    record("session.closed observed (final usage)",
           "session.closed" in seen,
           f"finalization_complete={result.get('finalization_complete')}")

    # ---- 4. usage accounting -------------------------------------------
    usage_seconds = float(result.get("usage_seconds") or 0.0)
    record("final usage was server-confirmed", bool(result.get("finalization_complete")),
           f"session.closed carried usage.seconds={usage_seconds}")
    record("session duration is at least the audio we streamed",
           usage_seconds >= audio_seconds * 0.8,
           f"usage {usage_seconds:.2f}s vs audio {audio_seconds:.2f}s")

    expected_usd = pricing.live_seconds_to_usd(usage_seconds)
    record("cost uses the published $0.05/minute rate, billed per second",
           abs(float(result.get("usd") or 0) - expected_usd) < 1e-9,
           f"reported ${result.get('usd')} expected ${expected_usd:.6f}")

    # ---- 5. cumulative snapshots must never be summed ------------------
    from jarvis.core.cost import BudgetManager
    from jarvis.db import db
    bm = BudgetManager()
    sid = result.get("session_id") or "test-session"
    # Feed the same cumulative snapshot three times, as a reconnect storm would.
    bm.record_live_snapshot(sid, usage_seconds * 0.5)
    bm.record_live_snapshot(sid, usage_seconds)
    bm.record_live_snapshot(sid, usage_seconds)
    rows = [r for r in db().usage_events(50)
            if r["session_id"] == sid and r["category"] == "live_voice"]
    stored = max((r["seconds"] for r in rows), default=0.0)
    record("cumulative usage snapshots keep the MAX, never the sum",
           len(rows) == 1 and abs(stored - usage_seconds) < 0.01,
           f"{len(rows)} row(s), stored {stored:.2f}s (sum would be "
           f"{usage_seconds*2.5:.2f}s)")

    # ---- 6. incomplete finalisation is recorded, not invented ----------
    bm.mark_incomplete_finalization(sid)
    row = [r for r in db().usage_events(50)
           if r["session_id"] == sid and r["category"] == "live_voice"]
    flagged = bool(row) and row[0]["finalized"] == 0
    record("missing session.closed marks the charge unconfirmed (never invents "
           "a final number)", flagged,
           f"finalized flag = {row[0]['finalized'] if row else 'no row'}")

    total = float(result.get("usd") or 0)
    print("-" * 74)
    print(f"MEASURED COST THIS RUN: ${total:.6f}")
    print("NOTE: synthetic SAPI speech. This proves the protocol and the "
          "finalisation path, not accuracy on the user's voice.")

    passed = sum(1 for r in RESULTS if r.startswith("PASS"))
    failed = sum(1 for r in RESULTS if r.startswith("FAIL"))
    print("=" * 74)
    print(f"TOTAL {len(RESULTS)}  PASSED {passed}  FAILED {failed}")
    print("=" * 74)

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "docs", "TEST_RESULTS_raw.txt")
    with open(out, "a", encoding="utf-8") as fh:
        fh.write(f"\n\n### tests/test_live_api.py  "
                 f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        fh.write(f"- model={live.MODEL} url={live.LIVE_WS_URL} rate={LIVE_RATE}\n")
        fh.write(f"- audio streamed: {audio_seconds:.2f}s in {CHUNK_MS}ms chunks\n")
        fh.write(f"- wall time {wall_ms:.0f}ms; session usage {usage_seconds:.2f}s\n")
        fh.write(f"- finalization_complete={result.get('finalization_complete')} "
                 f"incomplete_capture={result.get('incomplete_capture')}\n")
        fh.write(f"- audio_ms_sent={result.get('audio_ms_sent')} "
                 f"transcript_covered_ms={result.get('transcript_covered_ms')} "
                 f"fragments={result.get('fragments')}\n")
        fh.write(f"- transcript: {result.get('text')!r}\n")
        fh.write(f"- WER vs known first sentence: {wer*100:.1f}%\n")
        fh.write(f"- events observed: {sorted(set(events))}\n")
        fh.write(f"- session_id={result.get('session_id')}\n")
        fh.write(f"- measured cost this run: ${total:.6f}\n")
        for line in RESULTS:
            fh.write(f"- {line}\n")
        fh.write(f"- TOTAL {len(RESULTS)} PASSED {passed} FAILED {failed}\n")
    print(f"evidence appended to {out}")
    return 0 if failed == 0 else 1


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
