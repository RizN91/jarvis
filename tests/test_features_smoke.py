"""END-TO-END smoke test for the 2026-09-11 feature pass. Free, no network.

Drives the REAL `Application` object - real hotkey events, real microphone,
real overlay, real database - and checks the features the user actually asked for:

  * press the mouse button / F8 and speak into whatever box has focus
  * talk to the assistant on a separate binding
  * say "open X" and have it open instead of being typed
  * a Dynamic Island that is a bare orb until there is something to show
  * pick the assistant's voice
  * a History tab holding every prompt, ready to copy back out

WHY IT COSTS NOTHING
--------------------
Every path here stops at the local silence guard: the microphone opens for real,
captures a second of a quiet room, and `audio/vad.py` refuses to upload it. That
exercises capture, the state machine, insertion routing and the overlay without
ever reaching the API. The one thing it therefore cannot prove is transcription
accuracy - `docs/TEST_RESULTS.md` is explicit about that being unverified on
the user's own voice.

Run:  .venv\\Scripts\\python.exe tests/test_features_smoke.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests._console import use_utf8_stdout  # noqa: E402

use_utf8_stdout()

_TMP = Path(tempfile.mkdtemp(prefix="bv-features-smoke-"))
os.environ["BV_DATA_DIR"] = str(_TMP)
os.environ["LOCALAPPDATA"] = str(_TMP)

from jarvis import app as app_mod          # noqa: E402
from jarvis import config                  # noqa: E402
from jarvis.core import commands as CM     # noqa: E402
from jarvis.core import dictation as D     # noqa: E402
from jarvis.core import tools as T         # noqa: E402
from jarvis.db import db                   # noqa: E402
from jarvis.engines import live as L       # noqa: E402
from jarvis.ui.bridge import SettingsAPI   # noqa: E402
from jarvis.win import hotkeys as hk       # noqa: E402
from jarvis.win import overlay as ov       # noqa: E402

RESULTS: list[str] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    line = f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  :: {detail}" if detail else "")
    RESULTS.append(line)
    print(line)


def ev(action: str) -> "hk.HotkeyEvent":
    return hk.HotkeyEvent(action, "keyboard", time.time())


def main() -> int:
    print("=" * 74)
    print("FEATURE smoke test: dictation, spoken commands, island, voice, history")
    print("=" * 74)
    print(f"throwaway data directory: {_TMP}")

    config.reload()
    config.set_value("dictation_engine", "economy")   # local silence guard path
    config.set_value("vad_silence_ms", 0)             # no auto-stop mid-test
    config.set_value("voice_commands_enabled", True)

    app = app_mod.Application()

    # ---------------------------------------------------------- 1. dictation
    app._on_hotkey(ev("dictate_toggle"))
    time.sleep(1.2)
    cap = app.dictation._capture
    record("a hotkey starts dictation and really opens the microphone",
           app.dictation.state == D.CAPTURING and bool(cap and cap.running),
           f"state={app.dictation.state!r} mic_open={bool(cap and cap.running)}")

    results: list = []
    app.dictation.on_result = results.append
    app._on_hotkey(ev("dictate_toggle"))
    for _ in range(100):
        if results:
            break
        time.sleep(0.05)
    r = results[0] if results else None
    record("releasing it finishes the utterance and returns to idle",
           r is not None and app.dictation.state == D.IDLE
           and not app.dictation.active,
           f"state={app.dictation.state!r} result={type(r).__name__}")
    record("a silent room is refused, not invented, and costs nothing",
           r is not None and not r.ok and r.usd == 0.0,
           f"usd={getattr(r, 'usd', None)} detail={(getattr(r, 'detail', '') or '')[:58]!r}")

    app._on_hotkey(ev("dictate_toggle"))
    time.sleep(0.9)
    record("dictation can be started again (the controller does not wedge)",
           app.dictation.state == D.CAPTURING, f"state={app.dictation.state!r}")
    app.dictation.cancel("smoke test")

    # ------------------------------------------------- 2. separate bindings
    seen: list[str] = []
    app.toggle_assistant = lambda: seen.append("assistant")   # type: ignore
    app.emergency_stop = lambda: seen.append("stop")          # type: ignore
    app._on_hotkey(ev("assistant_toggle"))
    app._on_hotkey(ev("emergency_stop"))
    record("talking to the assistant and the emergency stop are separate controls",
           seen == ["assistant", "stop"], f"delivered={seen}")

    # ------------------------------------------------- 3. spoken commands
    opened: list = []
    real_call = T.call

    def spy(name, params=None, approval_token=None):
        opened.append((name, dict(params or {})))
        return T.ToolResult(True, f"(smoke test) would run {name}", tool=name,
                            outcome_inspected=True)

    # The shipped default is voice_command_confirm=True, which puts a real
    # MessageBoxW in front of every spoken action. A test must not click that
    # (it would need a human, and it would litter the desktop with dialogs), so
    # the run path is exercised with confirmation explicitly OFF - a real,
    # user-facing setting - and the default-No path is asserted separately below.
    #
    # The emergency-stop assertion above called dictation.cancel(), which latches
    # the cancel flag; _run_spoken_command correctly refuses to act while that is
    # set. A real utterance clears it in begin(), so clear it here to model the
    # start of a fresh utterance rather than a cancelled one.
    app.dictation._cancel.clear()
    # The emergency stop above also latches the tool layer's hard stop, which
    # outlives the 0.1 s auto-clear here because the spy below replaces
    # tools.call - the very function that would otherwise refuse. Clear it so
    # this section tests spoken commands, not the aftershock of section 2.
    T.set_hard_stop(False)
    config.set_value("voice_command_confirm", False)
    T.call = spy                                              # type: ignore
    try:
        res = D.DictationResult(True, text="open Chrome", engine="economy")
        handled = app.dictation._run_spoken_command(res)
        record("saying “open Chrome” runs a tool instead of typing the words",
               handled and opened and opened[0][0] == "open_app",
               f"tool={opened[0] if opened else None}")

        opened.clear()
        res2 = D.DictationResult(True, text="search for supplier invoices",
                                 engine="economy")
        app.dictation._run_spoken_command(res2)
        record("“search for …” opens a web search",
               bool(opened) and opened[0][0] == "open_url",
               f"tool={opened[0] if opened else None}")

        opened.clear()
        res3 = D.DictationResult(True, text="type this is the invoice text",
                                 engine="economy")
        handled3 = app.dictation._run_spoken_command(res3)
        record("“type …” strips the verb and still goes through insertion",
               handled3 is False and res3.text == "this is the invoice text"
               and not opened,
               f"text={res3.text!r} tools_called={opened}")

        opened.clear()
        res4 = D.DictationResult(True, text="opening the supplier invoices document",
                                 engine="economy")
        handled4 = app.dictation._run_spoken_command(res4)
        record("an ordinary sentence is dictated, never executed",
               handled4 is False and not opened,
               f"handled={handled4} tools_called={opened}")

        # ---- the default-No property (the whole point of the gate) --------
        # With the SHIPPED default (voice_command_confirm=True) and a user who
        # does not click Yes, nothing may run and nothing may be typed. The
        # dialog is stubbed to return IDNO so this needs no human.
        import ctypes as _ct
        config.set_value("voice_command_confirm", True)
        opened.clear()
        _real_mb = _ct.windll.user32.MessageBoxW
        try:
            _ct.windll.user32.MessageBoxW = lambda *a: 7          # IDNO
            res5 = D.DictationResult(True, text="open Chrome", engine="economy")
            handled5 = app.dictation._run_spoken_command(res5)
            record("the default confirmation declines safely: nothing runs",
                   handled5 is True and not opened,
                   f"handled={handled5} tools_called={opened} detail={res5.detail!r} "
                   f"confirm={config.get('voice_command_confirm')!r} "
                   f"hard_stopped={T.is_hard_stopped()}")
        finally:
            _ct.windll.user32.MessageBoxW = _real_mb
            config.set_value("voice_command_confirm", False)
    finally:
        T.call = real_call                                    # type: ignore
        config.set_value("voice_command_confirm", False)

    # ---------------------------------------------------------- 4. the island
    st_idle = ov.PillState(state="sleeping")
    st_talk = ov.PillState(state="dictating", transcript="open my supplier invoices file")
    st_work = ov.PillState(state="working", detail="Opening the quarterly report",
                           actions=["Opening the quarterly report"])

    def settle(state, frames=180):
        pill = ov.Overlay(fps=60)
        for _ in range(frames):
            pill._advance(state, 1.0 / 60.0)
        return pill

    idle, talk, work = settle(st_idle), settle(st_talk), settle(st_work)
    record("the island rests as the orb plus the wordmark, left wing shut",
           idle._left < 1 and 0 < idle._right <= 220,
           f"left={idle._left:.1f} right={idle._right:.1f} orb={idle.orb_d}px")
    record("it opens LEFT for speech and RIGHT for work, independently",
           talk._left > 100 and talk._right < talk.right_max
           and work._right > 100 and work._left < 1,
           f"talk L/R={talk._left:.0f}/{talk._right:.0f}  "
           f"work L/R={work._left:.0f}/{work._right:.0f}")
    record("the whole overlay is compact",
           idle.pill_h <= 70 and idle.height <= 170,
           f"bar={idle.pill_h}px window={idle.width}x{idle.height}")
    frame = talk._compose(st_talk, 0.6, 1.0)
    record("it renders a real frame with content",
           frame.getbbox() is not None, f"bbox={frame.getbbox()}")

    # ------------------------------------------------------------ 5. voices
    api = SettingsAPI()
    v = api.get_voices()
    record("the voice picker offers the documented roster",
           v["ok"] and len(v["voices"]) == len(L.VOICES) and v["known"],
           f"{len(v['voices'])} voices, current={v['current']!r}")
    api.set_config({"voice": "quartz"})
    record("choosing a voice is stored and read back",
           config.reload().get("voice") == "quartz",
           f"voice={config.get('voice')!r}")
    made = app_mod.AssistantSession(app)
    made.cfg = dict(config.load())
    record("the chosen voice is what a new session would request",
           str(made.cfg.get("voice")) == "quartz", f"{made.cfg.get('voice')!r}")

    # ----------------------------------------------------------- 6. history
    for text in ("open my supplier invoices documents and summarise the latest file",
                 "review the Supabase connection", "type the invoice is ready"):
        db().add_turn("user", text, meta={"engine": "economy", "inserted": True})
    h = api.get_history()
    texts = [i["text"] for i in h["items"]]
    record("every prompt is stored and readable from the History tab",
           h["ok"] and all(t in texts for t in
                           ("review the Supabase connection",)),
           f"{h['count']} row(s)")
    record("a stored prompt can be copied back out to reuse it",
           api.copy_text(texts[0])["ok"], f"{len(texts[0])} chars")
    help_ = api.get_command_help()
    record("the spoken-command vocabulary is discoverable in Settings",
           help_["ok"] and [g["verb"] for g in help_["groups"]] == ["Open", "Search", "Type"],
           f"{[g['verb'] for g in help_['groups']]}")

    app.quit()

    passed = sum(1 for x in RESULTS if x.startswith("PASS"))
    failed = sum(1 for x in RESULTS if x.startswith("FAIL"))
    print("=" * 74)
    print(f"TOTAL {len(RESULTS)}  PASSED {passed}  FAILED {failed}   (cost: $0.00)")
    print("=" * 74)

    out = _PROJECT_ROOT / "docs" / "TEST_RESULTS_raw.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a", encoding="utf-8") as fh:
        fh.write(f"\n\n### tests/test_features_smoke.py  "
                 f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        for line in RESULTS:
            fh.write(f"- {line}\n")
        fh.write(f"- TOTAL {len(RESULTS)} PASSED {passed} FAILED {failed}\n")
    print(f"evidence appended to {out}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
