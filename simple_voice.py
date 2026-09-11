"""The smallest possible thing that lets you TALK to GPT-Live and HEAR it back.

Deliberately not the app. No wake word, no tray, no overlay, no hotkeys, no
settings file, no dictation, no database. If this does not work, the problem is
the API key, the microphone or the speakers - never the app around them.

    .venv\\Scripts\\python.exe simple_voice.py

Then just talk. It greets you, keeps the microphone streaming (the model owns
turn-taking, exactly as the Live docs intend - there is no push-to-talk and no
silence timer), speaks its replies out loud, and prints both sides of the
conversation. Press Ctrl+C to stop.

Costs $0.05 per minute of session while it runs. Closing it stops the meter.

Options:
    --list      list the microphones and speakers, then exit
    --mic N     use input device N (default: the system default)
    --out N     use output device N (default: the system default)
    --no-greet  say nothing and wait for you to speak first
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jarvis import secrets                             # noqa: E402
from jarvis.audio import capture as audio_capture      # noqa: E402
from jarvis.audio import playback                      # noqa: E402
from jarvis.engines import live                        # noqa: E402

GREETING = (
    "Greet the user right now, before they have spoken, in English and in one "
    "short sentence - something like \"Hey, what's up?\". Speak first without "
    "waiting for them, then stop and listen."
)


def describe(index, want_input: bool) -> str:
    """A readable name for a resolved device, so a wrong one is obvious."""
    devices = (audio_capture.list_input_devices() if want_input
               else audio_capture.list_output_devices())
    if index is None:
        for dev in devices:
            if dev.default:
                return f"[{dev.index}] {dev.name}   (system default)"
        return "(system default)"
    for dev in devices:
        if dev.index == index:
            return f"[{index}] {dev.name}"
    return f"[{index}] (unknown)"


def list_devices() -> None:
    print("\nMICROPHONES")
    for dev in audio_capture.list_input_devices():
        print(f"  --mic {dev.index}   {dev.name}{'   (default)' if dev.default else ''}")
    print("\nSPEAKERS")
    for dev in audio_capture.list_output_devices():
        print(f"  --out {dev.index}   {dev.name}{'   (default)' if dev.default else ''}")
    print()


def say(line: str) -> None:
    print(line, flush=True)


async def run(args: argparse.Namespace) -> int:
    if not secrets.get_api_key():
        say("No API key is stored. Launch the app once and enter it on the setup "
            "screen, then run this again.")
        return 2

    mic = audio_capture.resolve_device(args.mic)
    out_dev = audio_capture.resolve_device(args.out, want_input=False)
    say(f"microphone : {describe(mic, want_input=True)}")
    say(f"speakers   : {describe(out_dev, want_input=False)}")
    print(flush=True)

    loop_ref: dict[str, asyncio.AbstractEventLoop] = {}
    audio_seconds = {"n": 0.0}
    sent_chunks = {"n": 0}      # mic blocks actually forwarded to the session

    speaker = playback.Speaker(
        rate=live.DEFAULT_RATE, device=out_dev, volume=1.0)
    speaker.start()

    def on_audio(pcm: bytes) -> None:
        audio_seconds["n"] += len(pcm) / 2 / live.DEFAULT_RATE
        speaker.play(pcm)

    # Take the backend model from the app's own config rather than hardcoding it.
    # A hardcoded name drifts: this file shipped "gpt-6-luna", which does not
    # exist, so every delegation failed with invalid_request_error while the
    # conversation itself still worked - a confusing half-failure.
    from jarvis import config as jarvis_config
    backend = (jarvis_config.get("backend_model") or "").strip() or "gpt-5.6-luna"
    say(f"backend model : {backend}")

    session = live.LiveSession(
        live.LiveConfig(
            instructions=(
                "You are a friendly voice assistant. Keep replies short - a "
                "sentence or two. Be warm and natural."
            ),
            voice=live.DEFAULT_VOICE,
            delegation={
                "type": "responses",
                "responses": {
                    "model": backend,
                    "instructions": "Answer briefly, in plain spoken text.",
                    "parallel_tool_calls": False,
                },
            },
            dictation=False,
        ),
        on_audio=on_audio,
    )

    await session.connect()
    if not session.started:
        say(f"FAILED to open a session: {session.last_error}")
        speaker.stop()
        return 1
    say(f"session open  (id {session.session_id})")
    say("$0.05/min starts now. Just talk. Ctrl+C to stop.\n")

    # THE ASSIGNMENT THAT WAS MISSING. `on_chunk` below reads this to hand mic
    # audio to the session from the capture thread. It was never set, so every
    # chunk was dropped and the assistant never heard a word - the single reason
    # this app was silent while everything else reported success.
    loop_ref["loop"] = asyncio.get_running_loop()

    def on_chunk(pcm: bytes) -> None:
        # THE BUG THAT MADE THIS SILENT: this used to read loop_ref["loop"], but
        # nothing ever assigned it, so every chunk returned early and the model
        # received no audio at all. It then had nothing to answer, and the app
        # looked broken while being perfectly healthy. Fall back to the running
        # loop so the microphone cannot silently stop feeding the session.
        loop = loop_ref.get("loop")
        if loop is None or loop.is_closed() or session.closed:
            return
        try:
            asyncio.run_coroutine_threadsafe(session.append_audio(pcm), loop)
            sent_chunks["n"] += 1
        except Exception:
            pass

    mic_capture = audio_capture.MicrophoneCapture(
        rate=live.DEFAULT_RATE, device=mic, gain=1.0,
        on_chunk=on_chunk, max_seconds=86400)
    mic_capture.start()

    # The docs require input audio to be flowing before the greeting is asked
    # for, or its context injection never completes.
    await asyncio.sleep(1.0)

    if not args.no_greet:
        ok = await session.greet(GREETING)
        if not ok:
            say("[greeting was not acknowledged - talking still works]")

    # ---- print both sides, updating each speaker's line in place ------------
    last = {"you": "", "jarvis": ""}

    def emit(who: str, text: str) -> None:
        text = text.strip()
        if not text or text == last[who]:
            return
        other = "jarvis" if who == "you" else "you"
        if last[other]:                    # the other one was mid-sentence
            print()
            last[other] = ""               # so it starts a fresh line next time
        last[who] = text
        label = "YOU   " if who == "you" else "JARVIS"
        print(f"\r{label}: {text}", end="", flush=True)

    started = time.monotonic()
    try:
        while not session.closed:
            await asyncio.sleep(0.35)
            emit("jarvis", session.output_transcript.text())
            emit("you", session.input_transcript.text())
            if session.last_error:
                print()
                say(f"[error] {session.last_error}")
                break
            if time.monotonic() - started > 3600:
                break
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        print()
        mic_capture.stop()
        try:
            report = await session.close(graceful=True)
        except Exception:
            report = None
        speaker.stop()

    if report is not None:
        say(f"\nsession {report.usage_seconds:.1f}s of voice time "
            f"(${report.usage_seconds / 60 * 0.05:.4f}), "
            f"{audio_seconds['n']:.1f}s of reply audio played")
    say(f"your microphone sent {sent_chunks['n']} blocks to the model")
    if sent_chunks["n"] == 0:
        say("*** NOTHING was sent - the microphone never fed the session, which "
            "means the model had nothing to answer. That is a bug, not a "
            "misunderstanding: tell me and I will fix it. ***")
    return 0


async def selftest(args: argparse.Namespace) -> int:
    """Prove each half separately, without needing anyone to speak.

    Checks the three things that can independently be broken, and says which one
    it is: the session opening, the microphone actually delivering audio, and the
    speakers actually being handed reply audio.
    """
    mic = audio_capture.resolve_device(args.mic)
    out_dev = audio_capture.resolve_device(args.out, want_input=False)
    ok = True

    say("1. devices")
    say(f"     microphone : {describe(mic, want_input=True)}")
    say(f"     speakers   : {describe(out_dev, want_input=False)}")

    say("2. API key")
    if not secrets.get_api_key():
        say("     FAIL - no key stored. Run the app once and enter it.")
        return 2
    say("     ok")

    say("3. microphone delivering audio (3s, just stay quiet or say anything)")
    levels: list[float] = []
    frames = {"n": 0}

    def on_chunk(pcm: bytes) -> None:
        frames["n"] += 1
        if len(pcm) >= 2:
            samples = memoryview(pcm).cast("h")
            peak = max(abs(s) for s in samples) / 32768.0
            levels.append(peak)

    cap = audio_capture.MicrophoneCapture(
        rate=live.DEFAULT_RATE, device=mic, gain=1.0,
        on_chunk=on_chunk, max_seconds=30)
    cap.start()
    await asyncio.sleep(3.0)
    cap.stop()

    if not frames["n"]:
        say("     FAIL - the microphone delivered nothing at all")
        ok = False
    else:
        loudest = max(levels) if levels else 0.0
        say(f"     {frames['n']} blocks, loudest {loudest:.3f} "
            f"({'HEARD something' if loudest > 0.02 else 'only silence/near-silence'})")
        if loudest <= 0.02:
            say("     NOTE: that is fine if you were quiet - but if you spoke and "
                "this stayed near zero, the WRONG MICROPHONE is selected, which is "
                "why 'Hey Jarvis' does nothing.")

    say("4. session + reply audio")
    say("     (keep talking while this runs - the model needs input audio flowing)")
    audio_seconds = {"n": 0.0}
    speaker = playback.Speaker(rate=live.DEFAULT_RATE, device=out_dev, volume=1.0)
    speaker.start()

    def on_audio(pcm: bytes) -> None:
        audio_seconds["n"] += len(pcm) / 2 / live.DEFAULT_RATE
        speaker.play(pcm)

    session = live.LiveSession(
        live.LiveConfig(instructions="You are a friendly voice assistant.",
                        voice=live.DEFAULT_VOICE, dictation=False),
        on_audio=on_audio)

    loop = asyncio.get_running_loop()

    # Real microphone audio into the session, exactly as the live path does. The
    # first version of this selftest opened the session and sent NOTHING, and the
    # greeting never injected (`context_injection_incomplete`) - which is the
    # single most important thing to know about GPT-Live: with no input audio
    # flowing, it will not speak, and the app looks completely dead.
    def feed(pcm: bytes) -> None:
        if session.closed:
            return
        try:
            asyncio.run_coroutine_threadsafe(session.append_audio(pcm), loop)
        except Exception:
            pass

    cap2 = audio_capture.MicrophoneCapture(
        rate=live.DEFAULT_RATE, device=mic, gain=1.0,
        on_chunk=feed, max_seconds=60)
    cap2.start()
    try:
        await session.connect()
        if not session.started:
            say(f"     FAIL - session did not open: {session.last_error}")
            return 1
        say(f"     session opened (id {session.session_id})")
        await asyncio.sleep(1.0)          # let input audio establish first
        greet_ok = await session.greet(GREETING)
        say(f"     greeting acknowledged: {greet_ok}")
        await asyncio.sleep(9.0)
        said = session.output_transcript.text().strip()
        say(f"     reply audio handed to the speakers: {audio_seconds['n']:.1f}s")
        if said:
            say(f"     it said: {said!r}")
        if audio_seconds["n"] < 0.5:
            say("     FAIL - the model produced no audio")
            if not frames["n"]:
                say("       ...because no microphone audio reached it at all")
            ok = False
        else:
            say("     You should have HEARD that out loud just now.")
    finally:
        cap2.stop()
        try:
            report = await session.close(graceful=True)
        except Exception:
            report = None
        speaker.stop()

    if report is not None:
        say(f"     session {report.usage_seconds:.1f}s (${report.usage_seconds / 60 * 0.05:.4f})")
    say("\nSELFTEST: " + ("everything above works" if ok else "see the FAIL lines"))
    return 0 if ok else 1


async def miclevel(args: argparse.Namespace) -> int:
    """A live level meter, so a wrong or dead microphone is visible immediately.

    Costs nothing - no session is opened, nothing leaves the machine.
    """
    mic = audio_capture.resolve_device(args.mic)
    say(f"microphone : {describe(mic, want_input=True)}")
    say("TALK NOW. A bar should move with your voice. Ctrl+C to stop.\n")

    peak = {"v": 0.0}
    blocks = {"n": 0}

    def on_chunk(pcm: bytes) -> None:
        blocks["n"] += 1
        if len(pcm) < 2:
            return
        samples = memoryview(pcm).cast("h")
        level = max(abs(s) for s in samples) / 32768.0
        peak["v"] = max(peak["v"], level)
        filled = int(min(level * 40, 40))
        bar = "#" * filled + "-" * (40 - filled)
        note = "  <-- your voice" if level > 0.05 else ""
        print(f"\r  [{bar}] {level:.3f}{note}   ", end="", flush=True)

    cap = audio_capture.MicrophoneCapture(
        rate=live.DEFAULT_RATE, device=mic, gain=1.0,
        on_chunk=on_chunk, max_seconds=600)
    cap.start()
    try:
        while True:
            await asyncio.sleep(0.5)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        cap.stop()

    print()
    say(f"\n{blocks['n']} audio blocks arrived.")
    if blocks["n"] == 0:
        say("That is the problem: the microphone delivered NOTHING.")
    elif peak["v"] < 0.02:
        say(f"Peak was only {peak['v']:.3f}. If you spoke, the app is listening to "
            f"the WRONG MICROPHONE - which is why nothing you say reaches it.")
    else:
        say(f"Peak {peak['v']:.3f} - this microphone works.")
    return 0


def volume_report() -> None:
    """Print the Windows master volume and mute state.

    First thing to check when the app "works" but nothing is audible: every
    layer can be correct while the endpoint is simply muted or at zero.
    """
    try:
        from jarvis.core.tools import _EndpointVolume
    except Exception as exc:
        say(f"  (could not read the system volume: {exc})")
        return
    ep = _EndpointVolume()
    if not ep.available:
        say("  (could not read the system volume on this machine)")
        return
    level = ep.get_level()
    muted = ep.get_mute()
    pct = "unknown" if level is None else f"{level * 100:.0f}%"
    say(f"  Windows master volume : {pct}")
    say(f"  Windows muted         : {muted}")
    if muted:
        say("  *** THE OUTPUT IS MUTED. That alone explains hearing nothing. ***")
    elif level is not None and level < 0.05:
        say("  *** The volume is essentially ZERO. ***")


def make_chime(rate: int = live.DEFAULT_RATE) -> bytes:
    """Three rising beeps - unmistakable, and clearly not speech.

    A test tone rather than a spoken phrase on purpose: if a chime cannot be
    heard, no amount of working API will help, and the fault is the speaker
    routing rather than anything upstream.
    """
    import array
    import math

    seconds = 1.5
    n = int(rate * seconds)
    buf = array.array("h", bytes(2 * n))
    for start, dur, freq in ((0.00, 0.28, 523.25),
                             (0.36, 0.28, 659.25),
                             (0.72, 0.62, 783.99)):
        i0, i1 = int(start * rate), min(int((start + dur) * rate), n)
        for i in range(i0, i1):
            t = (i - i0) / rate
            # short attack and decay, so the beep does not click
            env = min(1.0, t / 0.02) * min(1.0, max(0.0, (dur - t) / 0.06))
            sample = 0.45 * env * math.sin(2 * math.pi * freq * t)
            buf[i] = int(max(-1.0, min(1.0, sample)) * 32767)
    return buf.tobytes()


async def play_on(index, chime: bytes, label: str) -> bool:
    """Play the chime once on one device, verifying it actually drained.

    "Handed to the speaker" is a weak claim - it only means play() was called.
    PortAudio can accept a buffer and produce nothing. This waits for the queue
    to empty and reports the observed drain, so "it played" is measured rather
    than assumed.
    """
    speaker = playback.Speaker(rate=live.DEFAULT_RATE, device=index, volume=1.0)
    speaker.start()
    try:
        queued_before = speaker.queued_bytes
        speaker.play(chime)
        queued_after = speaker.queued_bytes
        drained_at = None
        waited = 0.0
        while waited < 5.0:
            if speaker.queued_bytes <= 0:
                drained_at = waited
                break
            await asyncio.sleep(0.1)
            waited += 0.1
        if drained_at is None:
            say(f"      ! the speaker never consumed the audio "
                f"({speaker.queued_bytes} bytes still queued)")
            return False
        say(f"      played and drained in {drained_at:.1f}s "
            f"(queued {queued_before} -> {queued_after} bytes)")
        return True
    except Exception as exc:
        say(f"      ! the speaker refused it: {exc}")
        return False
    finally:
        speaker.stop()


async def soundcheck(args: argparse.Namespace) -> int:
    """Play the chime on EVERY output, one at a time, naming each.

    This is how you find where sound actually comes out. A headset commonly
    exposes several endpoints (USB, Bluetooth, "Speakers") and Windows is happy
    to send audio to one while you are listening on another - which looks
    exactly like the app producing no sound at all.
    """
    devices = audio_capture.list_output_devices()
    if args.out is not None:
        devices = [d for d in devices if d.index == args.out] or devices[:1]

    chime = make_chime()
    say("Before the beeps, the state of the Windows output:")
    volume_report()
    print()
    say(f"Playing a chime on each of {len(devices)} outputs, one at a time.")
    say("Listen for three rising beeps and note WHICH ONE you hear.\n")

    for n, dev in enumerate(devices, 1):
        tag = "  [Windows default]" if dev.default else ""
        say(f"  {n}/{len(devices)}  >>> {dev.name}{tag}")
        await play_on(dev.index, chime, dev.name)
        await asyncio.sleep(0.6)

    print()
    say("Done. If you heard the beeps only on SOME of those, tell me which")
    say("number - that is the device we point Jarvis at.")
    say("If you heard NOTHING at all, the problem is Windows volume or the")
    say("headset itself, not Jarvis: check the headset's own dial/mute button")
    say("and the Windows volume mixer (right-click the speaker icon).")
    return 0


async def tone(args: argparse.Namespace) -> int:
    """Play the chime once on the resolved output device."""
    index = audio_capture.resolve_device(args.out, want_input=False)
    say(f"Playing a chime on: {describe(index, want_input=False)}")
    await play_on(index, make_chime(), "chime")
    say("If you did not hear it, run:  simple_voice.py --soundcheck")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Talk to GPT-Live and hear it back - nothing else.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="list devices and exit")
    ap.add_argument("--mic", type=int, default=None, help="input device index")
    ap.add_argument("--out", type=int, default=None, help="output device index")
    ap.add_argument("--selftest", action="store_true",
                    help="check devices, mic, session and speakers, then exit")
    ap.add_argument("--miclevel", action="store_true",
                    help="live microphone level meter (free - no API calls)")
    ap.add_argument("--tone", action="store_true",
                    help="play a test chime on the chosen speakers (free)")
    ap.add_argument("--soundcheck", action="store_true",
                    help="play a chime on EVERY speaker, one at a time (free)")
    ap.add_argument("--no-greet", action="store_true",
                    help="say nothing; wait for you to speak first")
    args = ap.parse_args()

    if args.list:
        list_devices()
        return 0
    if args.tone:
        return asyncio.run(tone(args))
    if args.soundcheck:
        return asyncio.run(soundcheck(args))
    if args.miclevel:
        try:
            return asyncio.run(miclevel(args))
        except KeyboardInterrupt:
            return 0
    if args.selftest:
        return asyncio.run(selftest(args))
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        say("\nstopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())
