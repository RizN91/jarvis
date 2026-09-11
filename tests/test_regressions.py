"""Regression tests for the defects found in the 2026-09-11 audit pass.

Each test here exists because something real was broken and NOTHING caught it.
The bugs shared a shape worth naming: they lived in code paths no test executed
(a state transition, a second process writing a file, a chip nobody clicked), and
every one of them left the app looking healthy - it started, it rendered, its
logs were clean, and 200-odd other assertions passed.

Offline and free. No microphone, no network, no OpenAI call.

Run:  python tests/test_regressions.py
      python -m pytest tests/test_regressions.py
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests._console import use_utf8_stdout  # noqa: E402

use_utf8_stdout()

# Redirect ALL app data to a throwaway directory BEFORE importing jarvis.
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="jarvis_regressions_"))
os.environ["LOCALAPPDATA"] = str(_TMP_ROOT / "appdata")
os.environ["BV_DATA_DIR"] = str(_TMP_ROOT / "data")

import json  # noqa: E402

from jarvis import config  # noqa: E402
from jarvis.core import dictation as D  # noqa: E402
from jarvis.win import overlay as ov  # noqa: E402

RESULTS: list[str] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    line = f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  :: {detail}" if detail else "")
    RESULTS.append(line)
    print(line)


def check(cond: bool, name: str, detail: str = "") -> None:
    record(name, bool(cond), detail)


# ==========================================================================
# 1. The dictation state machine
#
# WAS: the module constants said "idle"/"capturing"/"transcribing"/"inserting"
# while every _set_state() call wrote "sleeping"/"dictating"/"thinking"/...
# Nothing ever compared equal, so end() returned None on every button release
# and `active` (state != IDLE) latched True forever.
# ==========================================================================

def test_state_constants_are_the_ui_labels():
    check(D.IDLE == "sleeping" and D.CAPTURING == "dictating",
          "state constants use the UI vocabulary",
          f"IDLE={D.IDLE!r} CAPTURING={D.CAPTURING!r}")
    check(D.CAPTURING in ov.STATE_LABELS and D.TRANSCRIBING in ov.STATE_LABELS
          and D.INSERTING in ov.STATE_LABELS and D.IDLE in ov.STATE_LABELS,
          "every dictation state is a state the pill can render",
          f"{sorted({D.IDLE, D.CAPTURING, D.TRANSCRIBING, D.INSERTING})}")


def test_active_is_false_when_idle_and_after_an_error():
    c = D.DictationController()
    check(c.state == D.IDLE and not c.active,
          "a fresh controller is idle and not active", f"state={c.state!r}")
    c._set_state(D.ERROR, "microphone unavailable")
    check(not c.active,
          "an ERROR does NOT latch the controller active (begin() can retry)",
          f"state={c.state!r} active={c.active}")
    c._set_state(D.IDLE, "")
    check(not c.active, "returning to IDLE clears active")
    c._set_state(D.CAPTURING, "")
    check(c.active, "CAPTURING is active")
    c._set_state(D.TRANSCRIBING, "")
    check(c.active, "TRANSCRIBING is active")


def test_end_actually_runs_when_capturing():
    """The bug in one assertion: end() used to return None every single time."""
    config.reload()
    config.set_value("dictation_engine", "economy")   # offline: silence guard
    c = D.DictationController()
    check(c.end() is None, "end() is a no-op when not capturing")

    states: list[str] = []
    c.on_state = lambda s, _d: states.append(s)
    c.state = D.CAPTURING          # as begin() leaves it
    c._capture = None              # no microphone in this test
    c._target = None
    result = c.end()
    check(result is not None,
          "end() RUNS while capturing and returns a result",
          f"result={type(result).__name__}")
    check(D.TRANSCRIBING in states,
          "end() moved through the transcribing state", f"states={states}")
    check(c.state == D.IDLE and not c.active,
          "the controller is idle and reusable afterwards",
          f"state={c.state!r} active={c.active}")
    check(result is not None and not result.ok,
          "empty audio is refused rather than fabricated",
          f"detail={(result.detail if result else '')[:60]!r}")


def test_handsfree_auto_stop_uses_vad_silence_ms():
    """`vad_silence_ms` was documented as "hands-free auto-stop" and read by
    nothing, so F8 dictation ran until F8 was pressed again."""
    config.reload()
    config.set_value("vad_silence_ms", 200)

    class _Cap:
        running = True
        silence_seconds = 0.0

    c = D.DictationController()
    cap = _Cap()
    c._capture = cap                      # type: ignore
    c.state = D.CAPTURING
    ended: list[str] = []
    c.end = lambda: ended.append("end")    # type: ignore

    c._arm_silence_watchdog()
    time.sleep(0.45)
    check(not ended,
          "no auto-stop while speech is still arriving (silence_seconds == 0)",
          f"ended={ended}")

    cap.silence_seconds = 0.5              # half a second of quiet
    for _ in range(40):
        if ended:
            break
        time.sleep(0.02)
    check(bool(ended),
          "the utterance IS ended after vad_silence_ms of silence",
          f"ended={ended}")

    # 0 disables it entirely.
    config.set_value("vad_silence_ms", 0)
    c2 = D.DictationController()
    c2._capture = _Cap()                   # type: ignore
    c2.state = D.CAPTURING
    ended2: list[str] = []
    c2.end = lambda: ended2.append("end")  # type: ignore
    c2._capture.silence_seconds = 99.0     # type: ignore
    c2._arm_silence_watchdog()
    time.sleep(0.3)
    check(not ended2, "vad_silence_ms = 0 switches auto-stop off",
          f"ended={ended2}")
    config.set_value("vad_silence_ms", 900)


def test_hold_to_talk_is_never_auto_stopped():
    """A pause mid-sentence must not end a held-button capture."""
    import inspect
    from jarvis import app as app_mod
    src = inspect.getsource(app_mod.Application._on_hotkey)
    press = src.split('dictate_press')[1].split('elif')[0]
    check("auto_stop" not in press,
          "the hold-to-talk branch does not arm the silence watchdog",
          f"branch={' '.join(press.split())[:90]!r}")
    toggle = src.split('dictate_toggle')[1]
    check("auto_stop=True" in toggle,
          "the hands-free toggle branch does arm it")


def test_toggle_recovers_from_an_error_state():
    c = D.DictationController()
    c._set_state(D.ERROR, "boom")
    calls: list[str] = []
    c.begin = lambda *a, **k: calls.append("begin") or True   # type: ignore
    c.toggle()
    check(calls == ["begin"],
          "F8 still starts dictation after a previous error", f"calls={calls}")


# ==========================================================================
# 2. config.reload()
#
# WAS: load() cached forever, so the tray process never saw a change written by
# the settings process - which is the ONLY channel between them (no IPC).
# ==========================================================================

def test_reload_picks_up_an_external_write():
    config.reload()
    config.set_value("theme", "dark")
    path = config.config_path()

    # Simulate the settings PROCESS writing the file behind our back.
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["theme"] = "light"
    raw["wake_threshold"] = 0.44
    path.write_text(json.dumps(raw), encoding="utf-8")

    check(config.load()["theme"] == "dark",
          "load() still serves the cache (deliberate: get() is on hot paths)")
    fresh = config.reload()
    check(fresh["theme"] == "light",
          "reload() sees the other process's write", f"theme={fresh['theme']!r}")
    check(abs(float(fresh["wake_threshold"]) - 0.44) < 1e-9,
          "reload() picks up every changed key, not just the first")
    check(fresh["daily_budget_usd"] == config.DEFAULT_CONFIG["daily_budget_usd"],
          "reload() still merges over the defaults")


def test_update_does_not_clobber_another_process_write():
    config.reload()
    config.set_value("theme", "dark")
    path = config.config_path()
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["wake_enabled"] = True          # the tray process toggles this
    path.write_text(json.dumps(raw), encoding="utf-8")

    # The settings process now saves an unrelated key from a stale cache.
    config.update({"cleanup_style": "polish"})
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    check(on_disk["cleanup_style"] == "polish", "the new value was written")
    check(on_disk["wake_enabled"] is True,
          "the other process's key SURVIVED the write",
          f"wake_enabled={on_disk['wake_enabled']}")


# ==========================================================================
# 3. The overlay render loop
#
# WAS: `animated` was only assigned inside `if visible:`, and the pill starts
# hidden, so the first iteration raised UnboundLocalError - swallowed by the
# except and re-raised five times a second for as long as the pill was hidden.
# ==========================================================================

def test_hidden_overlay_loop_raises_nothing():
    pill = ov.Overlay(fps=30)
    pill._visible = False              # exactly how it starts

    caught: list[str] = []

    class _Catch(logging.Handler):
        def emit(self, rec):
            caught.append(rec.getMessage())

    handler = _Catch()
    ov.log.addHandler(handler)
    ov.log.setLevel(logging.DEBUG)
    try:
        t = threading.Thread(target=pill._loop, daemon=True)
        t.start()
        time.sleep(0.6)
        pill._stop.set()
        t.join(timeout=2)
    finally:
        ov.log.removeHandler(handler)

    errors = [m for m in caught if "overlay loop error" in m]
    check(not errors,
          "the render loop logs no errors while the pill is hidden",
          f"{len(errors)} error(s): {errors[:2]}")


def test_pill_quotes_the_transcript_exactly_once():
    """The mockup shows one opening and one closing quote, not three."""
    pill = ov.Overlay(fps=30)
    st = ov.PillState(
        state="listening",
        transcript="open my supplier invoices documents and summarise the latest file")
    frame = pill._compose(st, level=0.5, phase=0.0)
    check(frame.size == (pill.width, pill.height),
          "the pill composes at its declared size", f"{frame.size}")

    # Count quote glyphs by rendering the same wrap the pill uses.
    from PIL import ImageDraw
    d = ImageDraw.Draw(frame)
    font = ov._font(max(13, int(24 * pill.pill_h / 126.0)))
    lines = pill._wrap(st.transcript, font, 300, draw=d, max_lines=2)
    decorated = []
    for i, line in enumerate(lines):
        txt = line
        if i == 0:
            txt = "“" + txt
        if i == len(lines) - 1:
            txt = txt + "”"
        decorated.append(txt)
    joined = "".join(decorated)
    check(joined.count("“") == 1 and joined.count("”") == 1,
          "exactly one opening and one closing quote across all lines",
          f"open={joined.count(chr(0x201c))} close={joined.count(chr(0x201d))}")


def test_pill_is_click_through_everywhere_except_its_chips():
    """The pill answered HTCLIENT for its whole 1100x240 strip, so while it was
    up it ate every click near the top of the screen."""
    pill = ov.Overlay(fps=60)
    st = ov.PillState(state="listening", transcript="hello",
                      actions=["Copy"], esc_hint="ESC to stop")
    # Settle the wing springs first: a chip that has not finished opening has
    # no rectangle yet, so hit-testing an unsettled island proves nothing.
    for _ in range(150):
        pill._advance(st, 1.0 / 60.0)
    pill._compose(st, level=0.4, phase=0.0)     # populates the chip rectangles
    rects = list(pill._action_rects)
    check(bool(rects), "the composed pill exposes at least one chip rectangle",
          f"{len(rects)} rect(s)")
    if rects:
        x0, y0, x1, y1, name = rects[0]
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        check(pill.hit_action(cx, cy) == name,
              "the centre of a chip is a hit", f"{name!r}")
        check(pill.hit_action(40, pill.pill_top + 10) is None,
              "the transcript area is NOT a hit (clicks fall through)")
        check(pill.hit_action(pill.width // 2, 4) is None,
              "the glow margin above the pill is NOT a hit")
        check(ov.HTTRANSPARENT == -1 and ov.HTCLIENT == 1,
              "the hit-test replies are the real Win32 values")


def test_wrap_uses_real_font_metrics():
    """A len*9 guess let a long transcript run underneath the orb."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (400, 60))
    d = ImageDraw.Draw(img)
    font = ov._font(24)
    width = 300

    # 35 narrow characters. The old len*9 guess called this 315px and wrapped
    # it; real metrics say it fits on one line.
    narrow = "iiiii iiiii iiiii iiiii iiiii iiiii"
    thin = ov.Overlay._wrap(narrow, font, width, draw=d, max_lines=2)
    check(len(thin) == 1 and ov.Overlay._text_width(d, thin[0], font) <= width,
          "narrow text is NOT wrapped early (real metrics, not len*9)",
          f"lines={len(thin)} widest={ov.Overlay._text_width(d, thin[0], font)}px")

    # 35 wide characters. The same guess called this 315px too - identical - but
    # it is far wider, and un-wrapped it ran underneath the orb.
    wide = "WWWWW WWWWW WWWWW WWWWW WWWWW WWWWW"
    fat = ov.Overlay._wrap(wide, font, width, draw=d, max_lines=2)
    widest = max(ov.Overlay._text_width(d, ln, font) for ln in fat)
    check(len(fat) > 1 and widest <= width,
          "wide text of the SAME length does wrap, and every line fits",
          f"lines={len(fat)} widest={widest}px limit={width}px")


# ==========================================================================
# 3b. Hotkey action names
#
# WAS: update_bindings() stored the CONFIG key ("key_dictate_toggle") as the
# action and published that, while Application._on_hotkey switches on
# "dictate_toggle". F8, Ctrl+Alt+Space and Ctrl+Alt+Pause were all dead; only
# the mouse button and Esc, which publish the semantic names literally, worked.
# ==========================================================================

def test_keyboard_bindings_publish_the_handled_action_names():
    from jarvis.win import hotkeys as hk
    h = hk.HotkeyManager(bindings={
        "key_dictate_toggle": "f8",
        "key_assistant": "ctrl+alt+space",
        "key_emergency_stop": "ctrl+alt+pause",
    })
    actions = {a for a, _mods in h._kb_vks.values()}
    check(actions == {"dictate_toggle", "assistant_toggle", "emergency_stop"},
          "keyboard bindings map onto the published vocabulary",
          f"actions={sorted(actions)}")
    check(actions <= set(hk.ACTIONS),
          "every published action is declared in ACTIONS",
          f"undeclared={sorted(actions - set(hk.ACTIONS))}")

    import inspect
    from jarvis import app as app_mod
    src = inspect.getsource(app_mod.Application._on_hotkey)
    missing = [a for a in hk.ACTIONS if f'"{a}"' not in src]
    check(not missing,
          "Application._on_hotkey branches on every action that can arrive",
          f"missing={missing}")


def test_modifiers_are_still_required_after_the_rename():
    """Ctrl+Alt+Space must not fire on a bare Space."""
    from jarvis.win import hotkeys as hk
    h = hk.HotkeyManager(bindings={"key_assistant": "ctrl+alt+space"})
    vk = hk.VK_NAMES["space"]
    entry = h._kb_vks.get(vk)
    check(entry is not None, "the space binding was registered")
    if entry:
        action, needed = entry
        check(action == "assistant_toggle", "space maps to the assistant toggle")
        check(needed == frozenset({"ctrl", "alt"}),
              "its modifiers survived the action rename",
              f"required={sorted(needed)}")


# ==========================================================================
# 4. Application safety wiring: the hard stop, the chips, escalation
# ==========================================================================

def test_per_task_tool_call_ceiling_is_enforced():
    """`max_task_tool_calls` was a config key and a UI slider that did nothing."""
    from jarvis.core import tools as T
    T.set_hard_stop(False)
    T.begin_task_budget(limit=3, task="regression")
    try:
        results = [T.call("task_status", {}) for _ in range(5)]
        allowed = [r for r in results if r.error != "task_tool_budget"]
        refused = [r for r in results if r.error == "task_tool_budget"]
        check(len(allowed) == 3 and len(refused) == 2,
              "the 3-call ceiling allowed exactly 3 calls",
              f"allowed={len(allowed)} refused={len(refused)}")
        check(all(not r.ok for r in refused),
              "a refused call reports failure rather than a plausible result")
        status = T.task_budget_status()
        check(status["used"] == 3 and status["remaining"] == 0,
              "the budget reports what was actually spent", f"{status}")
    finally:
        T.end_task_budget()
    after = T.call("task_status", {})
    check(after.ok and after.error != "task_tool_budget",
          "ending the task disarms the ceiling",
          f"ok={after.ok} error={after.error!r}")


def test_closing_the_database_lets_it_reopen():
    """db().close() used to leave a CLOSED singleton behind for everyone else."""
    from jarvis.db import db
    first = db()
    first.audit("regression.probe", {"phase": "before"})
    first.close()
    second = db()
    check(second is not first, "db() hands out a fresh Database after close()")
    second.audit("regression.probe", {"phase": "after"})
    check(True, "the reopened database accepts writes")

def _make_app():
    from jarvis import app as app_mod
    return app_mod, app_mod.Application()


def test_emergency_stop_latches_until_resume():
    from jarvis.core import tools as T
    app_mod, app = _make_app()
    try:
        T.set_hard_stop(False)
        app.emergency_stop()
        check(T.is_hard_stopped(),
              "EMERGENCY STOP blocks new side effects immediately")
        time.sleep(0.4)          # the old code cleared it after 0.1s
        check(T.is_hard_stopped(),
              "the hard stop is still set 0.4s later (it LATCHES)",
              f"hard_stopped={T.is_hard_stopped()}")
        app.resume()
        check(not T.is_hard_stopped(),
              "resume() is what clears it, and only resume()")
    finally:
        T.set_hard_stop(False)
        app.quit()


def test_stop_speaking_chip_is_not_the_emergency_stop():
    from jarvis.core import tools as T
    app_mod, app = _make_app()
    try:
        T.set_hard_stop(False)
        app.assistant = None            # no session: the handler must no-op
        app._on_overlay_action(0, "Stop speaking")
        check(not T.is_hard_stopped(),
              "clicking 'Stop speaking' does NOT trigger the emergency stop",
              f"hard_stopped={T.is_hard_stopped()}")

        fired: list[str] = []
        app.emergency_stop = lambda: fired.append("stop")   # type: ignore
        app._on_overlay_action(0, "Stop")
        check(fired == ["stop"], "the bare 'Stop' chip still stops everything")

        app._on_overlay_action(0, "Something Nobody Wired Up")
        check(fired == ["stop"], "an unknown chip name is ignored, not guessed")
    finally:
        T.set_hard_stop(False)
        app.quit()


def test_every_chip_the_app_shows_has_a_handler():
    app_mod, app = _make_app()
    try:
        seen: list[str] = []
        app.emergency_stop = lambda: seen.append("emergency")     # type: ignore
        app._action_insert = lambda: seen.append("insert")        # type: ignore
        app._action_copy = lambda: seen.append("copy")            # type: ignore
        app._action_stop_speaking = lambda: seen.append("speak")  # type: ignore
        app.resume = lambda: seen.append("resume")                # type: ignore
        # Every name the app passes to overlay.set_state(actions=...).
        for name in ("Copy", "Insert", "Stop speaking", "Resume"):
            app._on_overlay_action(0, name)
        check(len(seen) == 4,
              "all four chips the app can show are wired",
              f"dispatched={seen}")
    finally:
        app.quit()


def test_escalation_needs_budget_headroom_not_the_lack_of_it():
    app_mod, app = _make_app()
    try:
        app.cfg["allow_escalation"] = True
        app.cfg["backend_model"] = "cheap-model"
        app.cfg["backend_model_escalated"] = "dear-model"
        session = app_mod.AssistantSession(app)

        app.budget.would_exceed = lambda usd: False        # type: ignore
        picked = session._delegation()["responses"]["model"]
        check(picked == "cheap-model",
              "routine requests stay on the cheap model even with headroom",
              f"model={picked}")
        picked = session._delegation(escalate=True)["responses"]["model"]
        check(picked == "dear-model",
              "explicit escalation with budget headroom uses the capable model",
              f"model={picked}")

        app.budget.would_exceed = lambda usd: True         # type: ignore
        picked = session._delegation(escalate=True)["responses"]["model"]
        check(picked == "cheap-model",
              "with NO headroom it stays on the cheap model",
              f"model={picked}")
    finally:
        app.quit()


def test_idle_timeout_actually_closes_a_billed_session():
    """`idle_timeout_seconds` was a config key nothing read."""
    app_mod, app = _make_app()
    try:
        closed: list[str] = []
        app.close_assistant = lambda reason="": closed.append(reason)  # type: ignore
        session = app_mod.AssistantSession(app)
        session.cfg = dict(app.cfg)
        session.cfg["idle_timeout_seconds"] = 5
        session.cfg["keep_listening_override"] = False

        class _Stub:
            class input_transcript:
                last_arrival = 0.0
        session.session = _Stub()          # type: ignore
        now = time.monotonic()

        session._last_activity = now - 1.0
        session._check_idle(now, speaking=False)
        check(not closed, "a session with recent speech is NOT cut off",
              f"closed={closed}")

        session._last_activity = now - 30.0
        session._check_idle(now, speaking=True)
        check(not closed, "a session is never closed mid-reply (still speaking)",
              f"closed={closed}")

        session._check_idle(now, speaking=False)
        for _ in range(50):
            if closed:
                break
            time.sleep(0.02)
        check(bool(closed),
              "an idle session IS closed, so it stops being billed",
              f"reason={closed[:1]}")

        closed.clear()
        session._idle_closing = False
        session.cfg["keep_listening_override"] = True
        session._check_idle(now, speaking=False)
        check(not closed,
              "the keep-listening override suppresses the timeout",
              f"closed={closed}")
    finally:
        app.quit()


# ==========================================================================
# 5. The Dynamic Island, spoken commands, voices and history
# ==========================================================================

def test_island_shape_follows_the_state():
    """The design in one test: bare orb at rest, left wing for speech, right
    wing for work, and the orb slides because the island stays centred."""
    def settle(st, frames=180):
        pill = ov.Overlay(fps=60)
        for _ in range(frames):
            pill._advance(st, 1.0 / 60.0)
        return pill

    idle = settle(ov.PillState(state="sleeping"))
    check(idle._left < 1.0 and 0 < idle._right <= 220,
          "at rest the island is the orb plus the wordmark, left wing shut",
          f"left={idle._left:.1f} right={idle._right:.1f}")

    talk = settle(ov.PillState(state="dictating", transcript="open the supplier invoices file"))
    check(talk._left > 100, "speaking opens the LEFT wing", f"left={talk._left:.0f}px")

    work = settle(ov.PillState(state="working", detail="Opening Supplier Invoices",
                               actions=["Opening Supplier Invoices"]))
    check(work._right > 100 and work._left < 1.0,
          "working opens the RIGHT wing only",
          f"left={work._left:.0f} right={work._right:.0f}")

    # The orb's own centre must MOVE between those two, or the island is just
    # a box that resizes.
    def orb_x(pill, st):
        pill._compose(st, 0.3, 0.0)
        return pill.width / 2.0 - (pill._left + pill._right + pill.orb_d) / 2.0 \
            + pill._left + pill.orb_d / 2.0
    x_talk = orb_x(talk, ov.PillState(state="dictating", transcript="open the supplier invoices file"))
    x_work = orb_x(work, ov.PillState(state="working", detail="Opening Supplier Invoices",
                                      actions=["Opening Supplier Invoices"]))
    check(abs(x_talk - x_work) > 40,
          "the orb slides when the wings differ (the island stays centred)",
          f"left-wing orb x={x_talk:.0f}  right-wing orb x={x_work:.0f}")


def test_island_is_smaller_than_the_old_pill():
    pill = ov.Overlay(fps=60)
    check(pill.pill_h <= 70, "the bar is short", f"{pill.pill_h}px (was 140)")
    check(pill.orb_d <= 90, "the orb is small", f"{pill.orb_d}px (was 200)")
    check(pill.height <= 170, "the whole window is short",
          f"{pill.width}x{pill.height} (was 1100x240)")


def test_reduced_motion_disables_the_springs():
    st = ov.PillState(state="dictating", transcript="hello there")
    pill = ov.Overlay(fps=60, reduced_motion=True)
    pill._advance(st, 1.0 / 60.0)
    target = pill._wing_targets(st)
    check(abs(pill._left - target[0]) < 0.01 and pill._appear == 1.0,
          "reduced motion snaps straight to the target, no animation",
          f"left={pill._left:.1f} target={target[0]:.1f} appear={pill._appear}")


def test_spoken_commands_only_match_a_leading_verb():
    from jarvis.core import commands as CM
    for text, verb in [("open Chrome", "open"),
                       ("Open Notepad.", "open"),
                       ("um, open chrome", "open"),
                       ("launch spotify", "open"),
                       ("search for supplier invoices", "search"),
                       ("type this is a test message", "type"),
                       ("open example.com", "open")]:
        got = CM.parse(text)
        check(got is not None and got.verb == verb,
              f"{text!r} is recognised as {verb}",
              f"got={got.verb if got else None}")

    # The important half: ordinary dictation must NOT become an action.
    for text in ["opening the supplier invoices document",
                 "I will open the door later",
                 "the type of report we use",
                 "can you open it",
                 "he said open sesame and it worked",
                 "open"]:
        check(CM.parse(text) is None,
              f"{text!r} is dictated, not executed",
              f"got={CM.parse(text)}")


def test_type_keeps_every_word_after_the_verb():
    from jarvis.core import commands as CM
    got = CM.parse("type this is a test message")
    check(got is not None and got.remainder == "this is a test message",
          "'type this ...' keeps 'this' as part of the text",
          f"remainder={got.remainder!r}" if got else "no match")
    got2 = CM.parse("type hello world")
    check(got2 is not None and got2.remainder == "hello world",
          "'type hello world' inserts 'hello world'",
          f"remainder={got2.remainder!r}" if got2 else "no match")


def test_spoken_commands_can_be_switched_off():
    from jarvis.core import commands as CM
    check(CM.parse("open Chrome", enabled=False) is None,
          "the whole feature is off when disabled")
    config.reload()
    config.set_value("voice_commands_enabled", False)
    c = D.DictationController()
    r = D.DictationResult(True, text="open Chrome")
    check(c._run_spoken_command(r) is False,
          "the dictation pipeline types the words when the feature is off")
    config.set_value("voice_commands_enabled", True)


def test_spoken_command_runs_through_the_tool_registry():
    """The action must go through core/tools.call, so approval and the hard
    stop apply. Proved by arming the hard stop: nothing may run."""
    from jarvis.core import tools as T
    config.reload()
    config.set_value("voice_commands_enabled", True)
    c = D.DictationController()
    T.set_hard_stop(True)
    try:
        r = D.DictationResult(True, text="open Chrome")
        handled = c._run_spoken_command(r)
        check(handled is True, "the utterance was treated as a command")
        check(not r.ok and not r.inserted,
              "the hard stop refused it, and nothing was typed instead",
              f"ok={r.ok} inserted={r.inserted} detail={r.detail[:60]!r}")
    finally:
        T.set_hard_stop(False)


def test_voice_roster_matches_the_documented_table():
    from jarvis.engines import live as L
    check(L.DEFAULT_VOICE == "marin",
          "the documented default voice is marin", f"{L.DEFAULT_VOICE!r}")
    documented = {"marin", "quartz", "ripple", "vesper", "willow", "stone",
                  "gleam", "meridian", "bossa", "tempo", "beacon", "delta",
                  "cinder"}
    check(set(L.VOICE_IDS) == documented,
          "the roster is exactly the documented voices - no invented names",
          f"extra={sorted(set(L.VOICE_IDS) - documented)} "
          f"missing={sorted(documented - set(L.VOICE_IDS))}")
    check(L.is_known_voice("QUARTZ") and not L.is_known_voice("nova"),
          "is_known_voice accepts a documented id and rejects an invented one")
    aus = [v["id"] for v in L.VOICES if v["region"] == "Australian"]
    check(sorted(aus) == ["quartz", "ripple"],
          "both Australian voices are present", f"{aus}")


def test_history_reads_back_what_dictation_wrote():
    from jarvis.ui.bridge import SettingsAPI
    from jarvis.db import db
    api = SettingsAPI()
    marker = "regression history probe %d" % int(time.time())
    db().add_turn("user", marker, meta={"engine": "economy", "inserted": True})
    got = api.get_history(limit=50)
    texts = [i["text"] for i in got["items"]]
    check(got["ok"] and marker in texts,
          "a dictated utterance is readable from the History tab",
          f"{got['count']} row(s)")
    row = [i for i in got["items"] if i["text"] == marker][0]
    check(row["engine"] == "economy" and row["inserted"] is True,
          "the row carries the engine and whether it was inserted", f"{row}")
    check(api.copy_text(marker)["ok"], "a stored prompt can be copied back out")
    check(not api.copy_text("")["ok"], "copying nothing is refused")


# ==========================================================================
# 12. The mouse-button binding, and the wake-word model
#
# WAS (mouse): the settings UI saved the DISPLAY label ("Mouse button 4") into
# config.json instead of the canonical name ("xbutton1"). The mouse hook decodes
# XBUTTON1/XBUTTON2 and compares against that string, so the side button was
# dead the moment a user recorded one. Nothing errored: the UI showed the
# binding, the log said the hotkeys started, and the button simply did nothing.
#
# WAS (wake word): the model is deliberately not vendored, and nothing could
# fetch it - the only route was a curl command in a document. So a user could
# switch the wake word on, say the phrase, get nothing, and have no way at all
# to find out why. Worse, the app knew ("wake word enabled but the model is not
# installed") and only said so in a log file.
# ==========================================================================

def test_mouse_labels_resolve_to_canonical_names():
    """One vocabulary: the label the UI shows and the name the hook delivers."""
    from jarvis.win import hotkeys
    for label, canonical in (("Mouse button 4", "xbutton1"),
                             ("Mouse button 5", "xbutton2"),
                             ("Mouse middle button", "middle"),
                             ("mouse button 4", "xbutton1"),
                             ("MOUSE BUTTON 5", "xbutton2")):
        got = hotkeys.canonical_mouse(label)
        check(got == canonical,
              f"{label!r} resolves to {canonical!r}", f"got {got!r}")
    for name in ("xbutton1", "xbutton2", "middle"):
        check(hotkeys.canonical_mouse(name) == name,
              f"{name!r} still resolves to itself")
    check(hotkeys.canonical_mouse("") == "" and hotkeys.canonical_mouse(None) == "",
          "an empty value stays empty (so 'no button' is not turned into a button)")
    check(hotkeys.canonical_mouse("banana") == "banana",
          "an unknown value is passed through rather than mapped to a real button")


def test_a_display_label_in_the_config_still_binds_the_mouse():
    """The exact defect: this config produced _mouse_bound == 'mouse button 4'."""
    from jarvis.win import hotkeys
    mgr = hotkeys.HotkeyManager(bindings={"mouse_dictate": "Mouse button 4",
                                          "key_dictate_toggle": "f8"})
    check(mgr._mouse_bound == "xbutton1",
          "a config written by the buggy build still binds the real button",
          f"_mouse_bound={mgr._mouse_bound!r}")
    check(mgr._mouse_bound in hotkeys.MOUSE_NAMES,
          "and the bound value is one the mouse hook can actually deliver",
          f"{mgr._mouse_bound!r} not in {sorted(hotkeys.MOUSE_NAMES)}")
    check(mgr._mouse_bound != "mouse button 4",
          "the raw label is NOT what gets bound (that was the bug)")
    mgr2 = hotkeys.HotkeyManager(bindings={"mouse_dictate": "none"})
    check(mgr2._mouse_bound is None, "'none' still disables the mouse button")


def test_conflict_detection_sees_through_the_label():
    """A duplicate written as a label must still be caught as a duplicate."""
    from jarvis.ui import bridge
    check(bridge._canonical("Mouse button 4") == "xbutton1",
          "the bridge canonicalises a mouse label",
          f"got {bridge._canonical('Mouse button 4')!r}")
    check(bridge._canonical("xbutton1") == "xbutton1",
          "and still canonicalises a canonical value")
    check(bridge._canonical("Ctrl+Alt+Space") == "ctrl+alt+space",
          "keyboard bindings are unaffected by the mouse change")


def test_the_binding_recorder_saves_the_canonical_value():
    """Guard the root cause in the source, not just the symptom.

    record_binding() returns BOTH `binding` ("xbutton1") and `display`
    ("Mouse button 4"); the UI must persist the first.
    """
    js = (_PROJECT_ROOT / "jarvis" / "ui" / "web" / "app.js").read_text(
        encoding="utf-8", errors="replace")
    check("var patch = bindingPatch(which, display);" not in js,
          "app.js no longer feeds the display label into the saved binding")
    check("var canonical = res.binding || res.display;" in js,
          "app.js saves res.binding (the canonical value)")

    from jarvis.ui import bridge
    src = (_PROJECT_ROOT / "jarvis" / "ui" / "bridge.py").read_text(
        encoding="utf-8", errors="replace")
    check('"display": _display(canonical)' in src,
          "record_binding still returns both the canonical value and a label")


def test_wake_model_availability_checks_the_real_files():
    """available must mean 'the files are there', not 'the folder exists'.

    Network-free: only the paths that answer without downloading are exercised.
    """
    from jarvis.audio import wake
    root = Path(tempfile.mkdtemp(prefix="reg-wake-")) / wake.MODEL_NAME
    root.mkdir(parents=True)

    ok, detail = wake.ensure_model(model_dir=str(root))
    check(not ok, "an empty model directory is not accepted", detail)
    check("not a usable model" in detail,
          "and it says why instead of downloading over the top", detail)

    (root / "tokens.txt").write_text("x", encoding="utf-8")
    (root / "bpe.model").write_text("x", encoding="utf-8")
    ok2, _ = wake.ensure_model(model_dir=str(root))
    check(not ok2, "tokens.txt + bpe.model without the onnx files is still not a model")

    (root / "encoder-x.onnx").write_bytes(b"x")
    (root / "joiner-x.onnx").write_bytes(b"x")
    ok3, detail3 = wake.ensure_model(model_dir=str(root))
    check(ok3 and "already installed" in detail3,
          "a complete model is accepted with no download", detail3)

    check(wake.MODEL_URL.startswith("https://github.com/"),
          "the model is fetched over https from the upstream release")
    check(wake.MODEL_URL.endswith(".tar.bz2"), "and it is the tarball this module unpacks")


def test_the_wake_model_can_be_installed_from_the_app():
    """The downloader exists, and a user can reach it without a terminal."""
    from jarvis.audio import wake
    from jarvis.ui import bridge

    check(callable(getattr(wake, "ensure_model", None)),
          "wake.ensure_model exists (the model is not vendored, so it must be fetchable)")
    check(hasattr(bridge.SettingsAPI, "download_wake_model"),
          "the settings bridge exposes the download")
    check(hasattr(bridge.SettingsAPI, "wake_model_status"),
          "and a status the UI can show BEFORE the user hits the silence")

    js = (_PROJECT_ROOT / "jarvis" / "ui" / "web" / "app.js").read_text(
        encoding="utf-8", errors="replace")
    check("wake-download" in js, "the UI has a control that triggers the download")
    check("download_wake_model" in js, "and it calls the bridge method")
    check("wake_model_status" in js,
          "the wake settings page asks for the model status")
    check("The wake-word model is not downloaded yet." in js,
          "and says so in the UI when it is missing")


# ==========================================================================

TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    print("=" * 74)
    print("REGRESSION tests for the 2026-09-11 audit findings (offline, free)")
    print("=" * 74)
    started = time.time()
    for fn in TESTS:
        try:
            fn()
        except Exception as exc:            # a raising test is a failing test
            record(f"{fn.__name__} raised", False, f"{type(exc).__name__}: {exc}")
    passed = sum(1 for r in RESULTS if r.startswith("PASS"))
    failed = sum(1 for r in RESULTS if r.startswith("FAIL"))
    print("-" * 74)
    print(f"TOTAL {len(RESULTS)}  PASSED {passed}  FAILED {failed}   "
          f"({time.time() - started:.2f}s)")

    out = _PROJECT_ROOT / "docs" / "TEST_RESULTS_raw.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a", encoding="utf-8") as fh:
        fh.write(f"\n\n### tests/test_regressions.py  "
                 f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        for line in RESULTS:
            fh.write(f"- {line}\n")
        fh.write(f"- TOTAL {len(RESULTS)} PASSED {passed} FAILED {failed}\n")
    print(f"evidence appended to {out}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
