"""REAL API test: Economy dictation through gpt-transcribe.

Billable. Each run is a few hundredths of a cent; the measured cost is recorded
in docs/TEST_RESULTS.md. Nothing here is faked: it uploads a real WAV and scores
what actually came back.

The reference text is known because the audio was synthesised by Windows SAPI,
so accuracy is computed locally as a word error rate. NOTE: synthetic speech is
NOT representative of the user's voice, accent, or room noise. This test proves the
integration works and that context hints are transmitted; it does NOT and cannot
prove accuracy on the user's own speech. That requires the opt-in A/B calibration
screen with their own recordings.

Run:  python tests/test_transcribe_api.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._console import use_utf8_stdout  # noqa: E402

use_utf8_stdout()

from jarvis.core import pricing  # noqa: E402
from jarvis.engines import errors, transcribe  # noqa: E402
from tests._credentials import (FIXTURES, load_api_key,  # noqa: E402
                               redaction_self_test, word_error_rate)

WAV = os.path.join(FIXTURES, "sapi_speech.wav")

REFERENCE = (
    "Jarvis test. Open my quarterly report documents and summarise the latest "
    "file. Do not send 1,250 units to Acme. Codex and Claude use Supabase and "
    "TypeScript. Friday. Actually, Thursday works better for the npm and "
    "WooCommerce review."
)

RESULTS: list[str] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    line = f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  :: {detail}" if detail else "")
    RESULTS.append(line)
    print(line)


def wav_seconds(path: str) -> float:
    import wave
    with wave.open(path) as w:
        return w.getnframes() / float(w.getframerate())


def main() -> int:
    print("=" * 74)
    print("REAL API test: gpt-transcribe (Economy dictation)")
    print("=" * 74)

    record("log redaction of key-shaped strings", redaction_self_test())

    if not load_api_key():
        record("API key available", False, "no key in api.env")
        return 2
    record("API key available in Credential Manager", True)

    if not os.path.exists(WAV):
        record("test audio fixture present", False, f"{WAV} missing")
        return 1
    seconds = wav_seconds(WAV)
    record("test audio fixture present", True, f"{seconds:.2f}s of real speech")

    eng = transcribe.TranscribeEngine()

    # ---- non-billable guard rails, checked before spending anything ----
    try:
        eng.transcribe_file(os.path.join(FIXTURES, "does_not_exist.wav"))
        record("missing file is rejected", False, "no error raised")
    except errors.EngineError as exc:
        record("missing file is rejected", exc.kind == "bad_request", str(exc)[:80])

    # ---- call 1: no context hints (baseline) ---------------------------
    t0 = time.monotonic()
    base = eng.transcribe_file(WAV, keywords=None, prompt=None, languages=None,
                               audio_seconds=seconds)
    base_ms = (time.monotonic() - t0) * 1000
    base_wer = word_error_rate(REFERENCE, base.text)
    record("transcribe with no hints returns text", bool(base.text.strip()),
           f"{len(base.text)} chars in {base_ms:.0f}ms")
    print(f"      baseline text: {base.text[:150]!r}")
    print(f"      baseline WER : {base_wer*100:.1f}%")

    # ---- call 2: with vocabulary keywords + prompt + language ----------
    keywords = ["Jarvis", "Quarterly Report", "Acme", "Codex", "Claude",
                "Supabase", "TypeScript", "npm", "WooCommerce", "Postgres"]
    prompt = ("Australian English dictation for a software and e-commerce "
              "business owner. Terms include Codex, Claude, Supabase, "
              "TypeScript, npm, WooCommerce, Zoho, Acme, Quarterly Report.")
    t0 = time.monotonic()
    hinted = eng.transcribe_file(WAV, keywords=keywords, prompt=prompt,
                                 languages=["en"], audio_seconds=seconds)
    hinted_ms = (time.monotonic() - t0) * 1000
    hinted_wer = word_error_rate(REFERENCE, hinted.text)
    record("transcribe with context hints returns text", bool(hinted.text.strip()),
           f"{len(hinted.text)} chars in {hinted_ms:.0f}ms")
    print(f"      hinted text: {hinted.text[:150]!r}")
    print(f"      hinted WER : {hinted_wer*100:.1f}%")

    # ---- the checks that actually matter for the user ---------------------
    low_base = base.text.lower()
    low_hint = hinted.text.lower()

    def has_all(text: str, terms: list[str]) -> tuple[bool, list[str]]:
        missing = [t for t in terms if t.lower() not in text]
        return (not missing), missing

    ok, missing = has_all(low_hint, ["quarterly report", "acme", "codex",
                                     "claude", "supabase", "typescript", "npm",
                                     "woocommerce"])
    record("technical vocabulary present with hints", ok,
           "all present" if ok else f"missing: {missing}")

    # Negation and numbers must survive verbatim - the spec calls this out.
    record("negation 'do not' survived", "not" in low_hint,
           f"text contains 'not': {'not' in low_hint}")
    has_num = ("1,250" in hinted.text or "1250" in hinted.text
               or "1 250" in hinted.text)
    record("number 1,250 survived", has_num,
           f"looking for 1,250 / 1250 -> {has_num}")
    record("self-correction (Friday/Thursday) both present",
           "friday" in low_hint and "thursday" in low_hint)

    # ---- cost accounting ----------------------------------------------
    expected_usd = pricing.transcribe_seconds_to_usd(seconds)
    record("cost uses the published $0.0045/minute rate",
           abs(base.usd - expected_usd) < 1e-9,
           f"reported ${base.usd:.6f}, expected ${expected_usd:.6f} "
           f"for {seconds:.2f}s")

    # ---- record against the budget manager ----------------------------
    from jarvis.core.cost import BudgetManager
    bm = BudgetManager()
    bm.record_transcribe(seconds, model=base.model, request_id=base.request_id)
    bm.record_transcribe(seconds, model=hinted.model, request_id=hinted.request_id)
    snap = bm.snapshot()
    record("spend meter recorded both calls", snap.today_usd > 0,
           f"today's estimate ${snap.today_usd:.6f}, "
           f"transcribe audio {snap.transcribe_seconds_today:.2f}s")

    total = base.usd + hinted.usd
    print("-" * 74)
    print(f"MEASURED COST THIS RUN: ${total:.6f}")
    print(f"baseline WER {base_wer*100:.1f}%  vs  hinted WER {hinted_wer*100:.1f}%")
    print("NOTE: synthetic SAPI speech, not the user's voice. This is an "
          "integration proof, not an accuracy claim.")

    passed = sum(1 for r in RESULTS if r.startswith("PASS"))
    failed = sum(1 for r in RESULTS if r.startswith("FAIL"))
    print("=" * 74)
    print(f"TOTAL {len(RESULTS)}  PASSED {passed}  FAILED {failed}")
    print("=" * 74)

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "docs", "TEST_RESULTS_raw.txt")
    with open(out, "a", encoding="utf-8") as fh:
        fh.write(f"\n\n### tests/test_transcribe_api.py  "
                 f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        fh.write(f"- audio: {seconds:.2f}s Windows SAPI synthetic speech\n")
        fh.write(f"- baseline (no hints): WER {base_wer*100:.1f}% "
                 f"latency {base_ms:.0f}ms usd ${base.usd:.6f}\n")
        fh.write(f"- baseline text: {base.text!r}\n")
        fh.write(f"- hinted: WER {hinted_wer*100:.1f}% latency {hinted_ms:.0f}ms "
                 f"usd ${hinted.usd:.6f}\n")
        fh.write(f"- hinted text: {hinted.text!r}\n")
        fh.write(f"- model={base.model} request_id={base.request_id}\n")
        fh.write(f"- total measured cost this run: ${total:.6f}\n")
        for line in RESULTS:
            fh.write(f"- {line}\n")
        fh.write(f"- TOTAL {len(RESULTS)} PASSED {passed} FAILED {failed}\n")
    print(f"evidence appended to {out}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
