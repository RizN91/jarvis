"""Prove the two-way voice loop: the model is asked to speak, and audio arrives.

This exercises the path the assistant uses and nothing else had covered - a
conversation session (dictation=False) with Responses delegation, where the
model's speech comes back as `session.output_audio.delta` and is forwarded to
`on_audio`. The dictation suites stream audio IN and check the transcript; none
of them ever asked the model to talk.

Billable: one short session, roughly $0.01.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._console import use_utf8_stdout  # noqa: E402

use_utf8_stdout()

from jarvis import config as jarvis_config  # noqa: E402
from jarvis.engines import live  # noqa: E402
from tests._credentials import load_api_key  # noqa: E402


async def main() -> int:
    if not load_api_key(verbose=False):
        print("FAIL  no API key available")
        return 1

    cfg = jarvis_config.load()
    voice = str(cfg.get("voice") or live.DEFAULT_VOICE)
    backend = str(cfg.get("backend_model") or "gpt-5.6-luna")

    print("=" * 74)
    print(" Two-way voice: can the assistant actually SPEAK?")
    print("=" * 74)
    print(f"  model    : {live.MODEL}")
    print(f"  voice    : {voice}  (documented: {live.is_known_voice(voice)})")
    print(f"  backend  : {backend}")
    print()

    audio: list[bytes] = []
    seen: list[str] = []
    errors: list[dict] = []

    def on_event(kind: str, event: dict) -> None:
        seen.append(kind)
        if event.get("type") == "error" or kind == "error":
            errors.append(event)

    session = live.LiveSession(
        live.LiveConfig(
            instructions=("You are Jarvis, a friendly voice assistant. "
                          "Reply with ONE short spoken sentence."),
            voice=voice,
            delegation={
                "type": "responses",
                # Same nesting the app sends: the backend settings live under
                # "responses". A flat {"model": ...} is rejected with
                # "Missing required parameter: 'session.delegation.responses'".
                "responses": {
                    "model": backend,
                    "instructions": "Answer briefly and out loud, one sentence.",
                    "tools": [],
                    "tool_choice": "none",
                    "parallel_tool_calls": False,
                },
            },
            dictation=False,
        ),
        on_audio=lambda pcm: audio.append(pcm),
        on_event=on_event,
    )

    started = time.monotonic()
    try:
        await session.connect()
    except Exception as exc:
        print(f"FAIL  could not open a session: {type(exc).__name__}: {exc}")
        return 1

    print(f"  session.started : {session.started}   id={session.session_id or '(none)'}")
    if not session.started:
        print(f"FAIL  the session never reported started; error={session.last_error}")
        return 1

    # Send REAL speech in, which is what a user does. `response.create` on its
    # own asked the model to talk unprompted and it stayed silent, so the loop
    # that matters is: audio in -> spoken answer out.
    import wave as _wave

    from jarvis.audio import resample as _resample

    wav = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "tests", "fixtures", "sapi_speech.wav")
    with _wave.open(wav) as fh:
        rate, channels = fh.getframerate(), fh.getnchannels()
        raw = fh.readframes(min(fh.getnframes(), int(rate * 6)))
    if channels > 1:
        import numpy as np
        raw = np.frombuffer(raw, dtype="<i2").reshape(-1, channels)[:, 0].tobytes()
    pcm24 = _resample.resample_offline(raw, rate, live.DEFAULT_RATE)

    print(f"  sending speech  : {len(pcm24)/(live.DEFAULT_RATE*2):.1f}s at {live.DEFAULT_RATE} Hz")
    # Paced, not dumped: the docs are explicit that sending a whole file at once
    # does not simulate a live microphone.
    step = int(live.DEFAULT_RATE * 2 * 0.1)          # 100 ms chunks
    for i in range(0, len(pcm24), step):
        await session.append_audio(pcm24[i:i + step])
        await asyncio.sleep(0.1)

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline and not audio:
        await asyncio.sleep(0.2)
        if session.closed:
            break

    wall = time.monotonic() - started
    chunks = len(audio)
    total = sum(len(c) for c in audio)
    seconds_of_speech = total / (live.DEFAULT_RATE * 2)   # PCM16 mono

    report = await session.close(graceful=True)

    print()
    print(f"  audio chunks received : {chunks}")
    print(f"  bytes of speech       : {total}  ({seconds_of_speech:.2f}s at {live.DEFAULT_RATE} Hz)")
    print(f"  output transcript     : {session.output_transcript.text()[:160]!r}")
    print(f"  events seen           : {sorted(set(seen))}")
    print(f"  usage                 : {report.usage_seconds:.2f}s, ${(report.usage_seconds/60)*0.05:.5f}")
    print(f"  wall time             : {wall:.1f}s")
    if errors:
        print(f"  ERRORS                : {errors[:2]}")
    print()

    ok = chunks > 0 and total > 0
    print(f"{'PASS' if ok else 'FAIL'}  the assistant produced SPEECH "
          f"({chunks} chunks, {seconds_of_speech:.2f}s of audio)")
    if not ok:
        print("      the model accepted the session but returned no audio - "
              "the user would hear nothing.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
