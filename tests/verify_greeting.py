"""Prove the documented greeting works: it speaks FIRST, before the user does.

This is the "hey jarvis -> it says hey what's up" behaviour. The documented
recipe is a fresh `session.instructions.append` with `delegation_id: null`, a
wait for `session.instructions.appended`, then a short `session.commentary.append`
to prompt the model to begin - with input audio still flowing the whole time.

Billable: one short live session, a few cents at most.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jarvis.app import GREETING_TEXT, LIVE_INSTRUCTIONS  # noqa: E402
from jarvis.engines import live  # noqa: E402
from tests._credentials import load_api_key  # noqa: E402


async def main() -> int:
    load_api_key(verbose=False)

    chunks: list[bytes] = []
    events: set[str] = set()

    def on_audio(pcm: bytes) -> None:
        chunks.append(pcm)

    def on_event(kind: str, event: dict) -> None:
        events.add(str(event.get("type") or kind))

    session = live.LiveSession(
        live.LiveConfig(
            instructions=LIVE_INSTRUCTIONS,
            voice=live.DEFAULT_VOICE,
            delegation={
                "type": "responses",
                "responses": {
                    "model": "gpt-6-luna",
                    "instructions": "Answer briefly and out loud.",
                    "parallel_tool_calls": False,
                },
            },
            dictation=False,
        ),
        on_audio=on_audio,
        on_event=on_event,
    )

    await session.connect()
    print(f"  session.started : {session.started}   id={session.session_id or '(none)'}")
    if not session.started:
        print(f"FAIL  session never started; error={session.last_error}")
        return 1

    # Input audio must ALREADY be flowing before the greeting is requested. The
    # docs list keeping input audio running as step 3, but in practice an append
    # sent into a session with no audio never completes its context injection
    # (`context_injection_incomplete`). The app gets this right by starting the
    # microphone before it greets; the earlier version of this test did not.
    silence = b"\x00\x00" * int(live.DEFAULT_RATE * 0.1)   # 100 ms of pcm16 silence
    pumping = True

    async def pump() -> None:
        while pumping and not session.closed:
            try:
                await session.append_audio(silence)
            except Exception:
                return
            await asyncio.sleep(0.1)

    pump_task = asyncio.create_task(pump())
    await asyncio.sleep(1.0)                 # let audio establish before greeting

    # The documented greeting, exactly as the assistant sends it.
    ok = await session.greet(GREETING_TEXT)
    print(f"  greet() ack'd   : {ok}")

    # Wait for it to speak of its own accord - no speech is sent in, which is the
    # whole point: it must talk first. Keep the session alive for a few seconds
    # AFTER the first audio so the sentence actually completes; closing the moment
    # audio appears tears the session down mid-injection and reports
    # `context_injection_incomplete` at close time.
    deadline = time.monotonic() + 22.0
    while time.monotonic() < deadline and not chunks:
        await asyncio.sleep(0.25)
    if chunks:
        await asyncio.sleep(6.0)             # let the greeting finish
    pumping = False
    pump_task.cancel()

    total = sum(len(c) for c in chunks)
    seconds = total / 2 / live.DEFAULT_RATE
    spoke = session.output_transcript.text().strip()
    report = await session.close(graceful=True)

    print(f"  audio chunks    : {len(chunks)}")
    print(f"  bytes spoken    : {total}  ({seconds:.2f}s of audio)")
    print(f"  it said         : {spoke!r}")
    print(f"  usage           : {report.usage_seconds:.2f}s")
    print(f"  events          : {', '.join(sorted(events))}")
    print(f"  last_error      : {session.last_error}")
    print(f"  closed_reason   : {session.closed_reason!r}")
    print(f"  rejected        : {session._append_rejected}")

    if not chunks:
        print(f"FAIL  it did not speak first (no audio came back unprompted)")
        print(f"      last_error = {session.last_error}")
        return 1
    if "session.instructions.appended" not in events:
        print("FAIL  no session.instructions.appended acknowledgment was seen")
        return 1

    print(f"PASS  it spoke first without being spoken to: {seconds:.2f}s of audio")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
