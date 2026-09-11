"""The Jarvis application: tray, hooks, overlay, sessions, and wiring.

WHAT RUNS WHERE
---------------
  main thread            : the tray icon's message loop (and nothing else)
  hook thread            : the low-level keyboard/mouse hooks
  hotkey-dispatch thread : turns hook events into dictation actions
  overlay thread         : renders the floating pill at ~60 fps while visible
                           (see HANDOFF.md: this is the app's largest CPU cost,
                           ~5 ms/frame; the render loop idles when hidden and
                           re-renders at 5 Hz once the island has settled)
  session thread         : lock/unlock/suspend notifications
  asyncio thread         : all cloud I/O
  dictation              : runs on the dispatcher thread, one utterance at a time

The settings UI is a SEPARATE PROCESS (see jarvis/ui/window.py). That keeps
WebView2 out of this process and removes the need for any localhost IPC: the
settings process edits config.json and the SQLite database directly, and this
process notices a changed config by mtime.

SAFETY BEHAVIOURS ENCODED HERE
-------------------------------
* EMERGENCY STOP sets a hard stop: no new side effects are authorised, active
  capture is cancelled, and every app-owned task has its cancellation state
  reported - we never claim a task stopped when we only asked it to.
* LOCK/SUSPEND stops capture and closes any billed session, because a locked
  screen must not keep paying for a Live session.
* PAUSE releases the microphone and restores the mouse button's normal action.
* ESC is only swallowed while an interaction is active, never when idle.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
from typing import Optional

from . import config, secrets
from .audio import capture as audio_capture
from .audio import playback as audio_playback
from .core import pricing
from .core.asyncrun import RUNNER
from .core.cost import BudgetExceeded, BudgetManager
from .core import dictation as dictation_states
from .core.dictation import DictationController, DictationResult

# core/tools.py is imported defensively: the tool registry must never prevent the
# app from starting, because dictation is independently useful without it.
try:
    from .core.tools import set_hard_stop, is_hard_stopped  # type: ignore
except Exception:  # pragma: no cover
    def is_hard_stopped() -> bool:
        return False
    def set_hard_stop(value: bool) -> None:  # type: ignore
        """Fallback when the tool registry is unavailable."""
        return None

from .db import db
from .engines import live
from .logsetup import get as _log
from .win import hotkeys as hotkeys_mod
from .win import overlay as overlay_mod
from .win import session as session_mod
from .win import tray as tray_mod

log = _log("app")


class Application:
    def __init__(self):
        self.cfg = config.load()
        self.budget = BudgetManager(on_warning=self._on_budget_warning)
        self.dictation = DictationController(
            on_state=self._on_dictation_state,
            on_result=self._on_dictation_result,
            on_level=self._on_mic_level,
            budget=self.budget,
        )
        # No geometry overrides: the island's own defaults ARE the design
        # (a bare 76 px orb that grows wings). Passing sizes here is how the
        # previews and the shipped overlay drifted apart last time.
        self.overlay = overlay_mod.Overlay(
            fps=60,
            reduced_motion=bool(self.cfg.get("reduced_motion", False)),
            offset_y=int(self.cfg.get("overlay_offset_y", 10)),
            on_action=self._on_overlay_action,
        )
        self.tray = tray_mod.Tray(
            on_start_dictation=self._tray_dictation,
            on_toggle_assistant=self.toggle_assistant,
            on_toggle_pause=self.toggle_pause,
            on_toggle_wake=self.toggle_wake,
            on_settings=self.open_settings,
            on_emergency_stop=self.emergency_stop,
            on_quit=self.quit,
        )
        bindings = dict(self.cfg.get("bindings") or {})
        self.hotkeys = hotkeys_mod.HotkeyManager(bindings=bindings)
        self.hotkeys.update_bindings(bindings)
        self.hotkeys.subscribe(self._on_hotkey)

        self.sessions = session_mod.SessionMonitor(
            on_locked=self._on_locked, on_unlocked=self._on_unlocked,
            on_suspend=self._on_suspend, on_resume=self._on_resume)

        self.wake = None
        self.paused = False
        self.assistant: Optional["AssistantSession"] = None
        self._stop = threading.Event()
        self._cfg_mtime = self._config_mtime()
        self._cfg_thread: Optional[threading.Thread] = None
        self._state = "sleeping"
        self._last_status_detail = ""
        self._input_lock = threading.RLock()

    # ------------------------------------------------------------------ startup
    def start(self) -> None:
        overlay_mod.enable_dpi_awareness()
        db().prune_conversation()

        if not self.cfg.get("setup_complete"):
            log.info("setup is not complete yet; opening the setup wizard")
            self.open_settings(setup=True)

        self.overlay.start()
        self.hotkeys.start()
        self.sessions.start()
        self._start_wake_if_enabled()

        self._cfg_thread = threading.Thread(target=self._watch_config, name="config-watch",
                                            daemon=True)
        self._cfg_thread.start()

        self.tray.wake_enabled = bool(self.cfg.get("wake_enabled"))
        self.tray.start()
        self.overlay.set_state(hint=self._idle_hint())
        self._set_state("sleeping", "ready")
        log.info("Jarvis started (engine=%s, key=%s)",
                 self.cfg.get("dictation_engine"),
                 "stored" if secrets.has_api_key() else "MISSING")
        self._main_loop()

    def _main_loop(self) -> None:
        """Keep the process alive and honour quit requests from the settings UI."""
        quit_marker = config.data_dir() / "quit.request"
        try:
            quit_marker.unlink(missing_ok=True)
        except OSError:
            pass
        while not self._stop.is_set():
            # The overlay HWND belongs to this thread. Windows cannot deliver
            # its Copy/Insert/Resume clicks unless THIS thread pumps messages.
            import ctypes.wintypes as wt
            msg = wt.MSG()
            while overlay_mod.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                overlay_mod.user32.TranslateMessage(ctypes.byref(msg))
                overlay_mod.user32.DispatchMessageW(ctypes.byref(msg))
            if quit_marker.exists():
                try:
                    quit_marker.unlink(missing_ok=True)
                except OSError:
                    pass
                log.info("quit requested by the settings window")
                self.quit()
                break
            self._stop.wait(0.02)

    # ------------------------------------------------------------------ state
    def _set_state(self, state: str, detail: str = "") -> None:
        self._state = state
        self._last_status_detail = detail
        try:
            self.overlay.set_state(state=state, detail=detail)
            self.tray.set_state(state)
            snap = self.budget.snapshot()
            self.overlay.set_state(spend_usd=snap.today_usd)
        except Exception as exc:
            log.debug("state update failed: %s", exc)

    def _idle_hint(self) -> str:
        """The one line the collapsed island shows if the user hovers on it."""
        binding = (self.cfg.get("bindings") or {}).get("mouse_dictate", "xbutton2")
        if config.get("wake_enabled"):
            phrases = config.get("wake_phrases") or ["hey jarvis"]
            return f"Say “{phrases[0].title()}”, or hold the mouse side button"
        if binding in (None, "none", "disabled"):
            return "Press F8 to dictate"
        return "Hold the mouse side button to dictate"

    # --------------------------------------------------------------- overlay
    def _on_mic_level(self, rms: float) -> None:
        self.overlay.set_level(rms)

    # The pill's chips dispatch on an EXACT name, never a substring. The spec
    # requires "stop speaking", "stop current task" and "go to sleep" to be
    # distinct commands, and substring matching collapsed them: "Stop speaking"
    # contains "stop", so clicking barge-in fired the full emergency stop.
    # Anything not in this table is logged and ignored rather than guessed at.
    def _on_overlay_action(self, _idx: int, name: str) -> None:
        """Clicks on the pill's action chips."""
        action = (name or "").strip().lower()
        handlers = {
            "insert": self._action_insert,
            "re-insert": self._action_insert,
            "copy": self._action_copy,
            "stop speaking": self._action_stop_speaking,
            "resume": self.resume,
            "stop": self.emergency_stop,
        }
        handler = handlers.get(action)
        if handler is None:
            log.debug("overlay chip %r has no handler; ignored", name)
            return
        try:
            handler()
        except Exception as exc:
            log.error("overlay action %r failed: %s", name, exc)

    def _action_insert(self) -> None:
        result = self.dictation.reinsert_pending()
        if result.inserted:
            self.overlay.hide()
        else:
            self.overlay.set_state(detail=result.detail, actions=["Copy", "Insert"])

    def _action_copy(self) -> None:
        if self.dictation.copy_pending_to_clipboard():
            self.tray.notify("Jarvis", "Copied to the clipboard.")

    def _action_stop_speaking(self) -> None:
        """Barge-in: silence the assistant's current reply, keep the session."""
        session = self.assistant
        if session is None:
            return
        dropped = session.stop_speaking()
        log.info("barge-in: dropped %d queued audio bytes", dropped)

    # ------------------------------------------------------------- dictation
    def _on_dictation_state(self, state: str, detail: str) -> None:
        # A running job belongs on the island's LEFT wing, with its own icon and
        # progress bar; a status sub-line belongs on the right. Routing the same
        # string to both put "Opening Chrome" on screen twice.
        if state == dictation_states.INSERTING and detail:
            self.overlay.set_state(task=detail, detail="", progress=-1.0)
        else:
            self.overlay.set_state(task="", progress=-1.0)
        self._set_state(state, detail)
        self.hotkeys.set_interaction_active(self.dictation.active)
        if state == dictation_states.CAPTURING:
            self._stop_wake()
            self.overlay.show()
        elif state in ("sleeping", "error") and not self.dictation.held_text:
            if not self.assistant:
                self._start_wake_if_enabled()
            # Leave the pill up briefly when there is something to read.
            threading.Timer(0.6, self._maybe_hide).start()

    def _maybe_hide(self) -> None:
        if not self.dictation.active and not self.assistant and not self.dictation.held_text:
            self.overlay.hide()

    def _on_dictation_result(self, result: DictationResult) -> None:
        if result.held_for_user:
            self.overlay.set_state(
                state="approval" if result.requires_confirmation else "working",
                transcript=result.text,
                detail=result.detail or "held for review",
                actions=["Copy", "Insert"],
                esc_hint="ESC to dismiss")
            self.overlay.show()
        elif result.ok and result.inserted:
            self.overlay.set_state(state="working", transcript=result.text,
                                   detail=f"inserted into {result.target}"
                                          if result.target else "inserted")
            self.tray.set_state("working")
            threading.Timer(1.2, self._maybe_hide).start()
        elif not result.ok and result.detail:
            self.overlay.set_state(state="error", transcript=result.text,
                                   detail=result.detail, esc_hint="ESC to dismiss")
            self.overlay.show()
        snap = self.budget.snapshot()
        self.overlay.set_state(spend_usd=snap.today_usd)
        if result.quality_notes:
            log.info("dictation notes: %s", "; ".join(result.quality_notes))

    # ----------------------------------------------------------------- hotkeys
    def _on_hotkey(self, event: hotkeys_mod.HotkeyEvent) -> None:
        try:
            if event.action == "dictate_press":
                if self._can_dictate():
                    self.dictation.begin()
            elif event.action == "dictate_release":
                self._finish_dictation()
            elif event.action == "dictate_toggle":
                if self.dictation.state == dictation_states.CAPTURING:
                    self._finish_dictation()
                elif self._can_dictate():
                    # Hands-free: arm the silence auto-stop, so F8 does not have
                    # to be pressed again (and a billed session does not stay
                    # open) when the user simply stops talking.
                    self.dictation.begin(auto_stop=True)
            elif event.action == "assistant_toggle":
                threading.Thread(target=self.toggle_assistant, daemon=True).start()
            elif event.action == "emergency_stop":
                set_hard_stop(True)
                threading.Thread(target=self.emergency_stop, daemon=True).start()
            elif event.action == "cancel":
                if self.dictation.active:
                    self.dictation.cancel()
                elif self.assistant:
                    threading.Thread(target=self.close_assistant, args=("cancelled",),
                                     daemon=True).start()
                elif self.dictation.held_text:
                    self.dictation.cancel("dismissed")
                self.overlay.hide()
        except Exception as exc:
            log.error("hotkey handler failed: %s", exc)

    def _can_dictate(self) -> bool:
        if self.paused or self.assistant or session_mod.is_locked() or is_hard_stopped():
            return False
        return True

    def _finish_dictation(self) -> None:
        # Never keep the shortcut dispatcher busy through an API call.
        threading.Thread(target=self.dictation.end, name="dictation-finish",
                         daemon=True).start()

    # ------------------------------------------------------------------- pause
    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self.tray.paused = self.paused
        self.hotkeys.set_enabled(not self.paused)
        # Restore the mouse button's normal action whenever we are paused.
        self.hotkeys.set_mouse_suppression(not self.paused)
        if self.paused:
            self.close_assistant("microphone paused")
            if self.dictation.active:
                self.dictation.cancel("microphone paused")
            self._stop_wake()
            self._set_state("muted", "microphone paused")
        else:
            self._start_wake_if_enabled()
            self._set_state("sleeping", "microphone active")
        self.tray.set_state(self._state)

    def _tray_dictation(self) -> None:
        if not self._can_dictate():
            return
        if self.dictation.state == dictation_states.CAPTURING:
            self._finish_dictation()
        else:
            # Same hands-free semantics as F8: there is no key to release here.
            self.dictation.begin(auto_stop=True)

    # -------------------------------------------------------------------- wake
    def _start_wake_if_enabled(self) -> None:
        if (self.wake is not None or self.paused or self.assistant
                or self.dictation.active or session_mod.is_locked()
                or self._stop.is_set() or is_hard_stopped()
                or not config.get("wake_enabled")):
            return
        if config.get("push_to_talk_only"):
            return
        try:
            from .audio import wake
        except Exception as exc:
            log.warning("wake-word module unavailable: %s", exc)
            return
        try:
            if not wake.WakeWordDetector.is_available(config.get("wake_model_dir")):
                log.warning("wake word enabled but the model is not installed; "
                            "push-to-talk still works")
                return
            self.wake = wake.WakeWordDetector(
                phrases=list(config.get("wake_phrases") or ["hey jarvis"]),
                model_dir=config.get("wake_model_dir"),
                threshold=float(config.get("wake_threshold", 0.22)),
                on_detect=self._on_wake_detected)
            self.wake.start()
            log.info("local wake listener active (audio stays on this machine)")
        except Exception as exc:
            log.warning("could not start the wake listener: %s", exc)
            self.wake = None

    def _stop_wake(self) -> None:
        if self.wake is not None:
            try:
                self.wake.stop()
            except Exception:
                pass
            self.wake = None

    def toggle_wake(self) -> None:
        enabled = not bool(config.get("wake_enabled"))
        config.set_value("wake_enabled", enabled)
        self.tray.wake_enabled = enabled
        if enabled:
            self._start_wake_if_enabled()
        else:
            self._stop_wake()
        self.tray.notify("Jarvis",
                         f"Wake word {'enabled' if enabled else 'disabled'}.")

    def _on_wake_detected(self, keyword: str, score: float) -> None:
        """Called on the decode thread: must not block."""
        if self.paused or self.dictation.active or self.assistant:
            return
        if session_mod.is_locked():
            return
        log.info("wake word %r detected", keyword)
        # Wake phrase -> ask the assistant (a dictation phrase would collide with
        # the user talking to the room).
        threading.Thread(target=self.open_assistant, name="wake-open",
                         daemon=True).start()

    # ------------------------------------------------------------------- lock
    def _on_locked(self, reason: str) -> None:
        log.info("session locked (%s): stopping capture and closing sessions", reason)
        if self.dictation.active:
            self.dictation.cancel("screen locked")
        self._stop_wake()
        self.close_assistant("screen locked")
        self._set_state("sleeping", f"paused: {reason}")

    def _on_unlocked(self) -> None:
        log.info("session unlocked")
        self._start_wake_if_enabled()
        self._set_state("sleeping", "ready")

    def _on_suspend(self) -> None:
        self._on_locked("suspended")

    def _on_resume(self) -> None:
        hotkeys_mod.release_stuck_modifiers()
        self._on_unlocked()

    # --------------------------------------------------------------- assistant
    def toggle_assistant(self) -> None:
        if self.assistant:
            self.close_assistant("user closed")
        else:
            self.open_assistant()

    def open_assistant(self) -> None:
        if self.paused or session_mod.is_locked() or is_hard_stopped():
            self.overlay.set_state(state="muted",
                                   detail="the microphone is paused",
                                   esc_hint="ESC to dismiss")
            self.overlay.show()
            return
        if self.assistant is not None:
            return
        if self.dictation.active or self.dictation._processing:
            return
        if not secrets.has_api_key():
            self.overlay.set_state(state="error", transcript="",
                                   detail="no API key stored — open Settings",
                                   esc_hint="ESC to dismiss")
            self.overlay.show()
            return
        try:
            self.budget.check_can_start(category="live_voice")
        except BudgetExceeded as exc:
            self.overlay.set_state(state="error", detail=str(exc),
                                   esc_hint="ESC to dismiss")
            self.overlay.show()
            return
        self.overlay.set_state(state="listening", transcript="", detail="connecting…",
                               esc_hint="ESC to stop")
        self.overlay.show()
        try:
            with self._input_lock:
                if self.assistant is not None:
                    return
                self._stop_wake()
                self.assistant = AssistantSession(self)
                current = self.assistant
            self.hotkeys.set_interaction_active(True)
            current.open()
        except Exception as exc:
            log.error("assistant failed to open: %s", exc)
            self.close_assistant("connection failed")
            self.overlay.set_state(state="error", detail=f"could not connect: {exc}",
                                   esc_hint="ESC to dismiss")

    def close_assistant(self, reason: str = "closed") -> None:
        session = self.assistant
        if session is None:
            return
        self.assistant = None
        self.hotkeys.set_interaction_active(self.dictation.active)
        try:
            session.close(reason)
        except Exception as exc:
            log.warning("assistant close failed: %s", exc)
        self.overlay.set_state(state="sleeping", transcript="", detail=reason)
        self._maybe_hide()
        self._start_wake_if_enabled()

    # -------------------------------------------------------- emergency stop
    def emergency_stop(self) -> None:
        """Stop everything and report honestly what was and was not cancelled."""
        log.warning("EMERGENCY STOP")
        db().audit("emergency_stop", {"at": time.time()})
        set_hard_stop(True)
        if self.dictation.active:
            self.dictation.cancel("emergency stop")
        self._stop_wake()
        self.close_assistant("emergency stop")

        still_running: list[str] = []
        for task in db().list_tasks(limit=20):
            if task.get("status") == "running":
                db().update_task(int(task["id"]), status="cancel_requested")
                still_running.append(f"#{task['id']} {task.get('title') or task['agent']}")
        summary = ("stopped: dictation, microphone capture and the voice session"
                   + (f". Cancellation requested for {len(still_running)} agent task(s): "
                      + ", ".join(still_running[:3]) if still_running else
                      ". No agent tasks were running."))
        self.overlay.set_state(state="error", transcript="", detail=summary,
                               esc_hint="ESC to dismiss",
                               actions=["Resume"], )
        self.overlay.show()
        self.tray.notify("Jarvis", summary[:200])
        # The hard stop LATCHES. It is cleared only by resume(), which is reached
        # from the pill's "Resume" chip or the tray. A previous version armed a
        # 0.1 s timer here that cleared it again, which meant the emergency stop
        # blocked new side effects for one tenth of a second - and its own
        # comment claimed the opposite. Do not re-add that timer.

    def resume(self) -> None:
        """Clear the emergency stop. Deliberate, user-initiated, never automatic."""
        log.warning("emergency stop cleared by the user")
        db().audit("emergency_stop.resume", {"at": time.time()})
        set_hard_stop(False)
        self.overlay.set_state(state="sleeping", transcript="",
                               detail="stop cleared - ready", actions=[],
                               esc_hint="")
        self._set_state("sleeping", "ready")
        self._maybe_hide()

    # -------------------------------------------------------------- settings
    def open_settings(self, setup: bool = False) -> None:
        """Launch the settings UI as its own process (no IPC, no WebView2 here)."""
        argv = ([sys.executable, "--settings"] if config.is_frozen()
                else [sys.executable, "-m", "jarvis.ui.window"])
        if setup:
            argv.append("--setup")
        try:
            creation = 0x00000008 if os.name == "nt" else 0  # DETACHED_PROCESS
            subprocess.Popen(argv, creationflags=creation, close_fds=True,
                             cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            log.info("settings window launched")
        except Exception as exc:
            log.error("could not launch the settings window: %s", exc)
            self.tray.notify("Jarvis", f"Could not open Settings: {exc}")

    # ------------------------------------------------------------ config watch
    @staticmethod
    def _config_mtime() -> float:
        try:
            return config.config_path().stat().st_mtime
        except OSError:
            return 0.0

    def _watch_config(self) -> None:
        """Reload settings changed by the settings process (file-based, no IPC)."""
        while not self._stop.is_set():
            self._stop.wait(1.5)
            mtime = self._config_mtime()
            if mtime == self._cfg_mtime:
                continue
            self._cfg_mtime = mtime
            try:
                # reload(), not load(): load() returns the process-wide cache and
                # would hand back exactly the values we are trying to replace.
                self.cfg = config.reload()
            except Exception as exc:
                log.warning("config reload failed: %s", exc)
                continue
            log.info("config changed on disk; reapplying")
            conflict = self.hotkeys.update_bindings(self.cfg.get("bindings") or {})
            if conflict:
                log.warning("binding conflict after reload: %s", conflict)
                self.tray.notify("Jarvis", f"Shortcut conflict: {conflict}")
            self.overlay.set_reduced_motion(bool(self.cfg.get("reduced_motion", False)))
            was_wake = self.wake is not None
            self._stop_wake()
            if config.get("wake_enabled"):
                self._start_wake_if_enabled()
            elif was_wake:
                log.info("wake listener stopped by a settings change")

    # ------------------------------------------------------------------ budget
    def _on_budget_warning(self, level: str, message: str) -> None:
        self.tray.notify("Jarvis — spending", message)
        self.overlay.set_state(state="error" if level == "error" else "working",
                               detail=message, esc_hint="ESC to dismiss")
        if level == "error":
            self.overlay.show()
            # Close billed sessions conservatively when the ceiling is reached.
            threading.Thread(target=self.close_assistant,
                             args=("local spending ceiling reached",), daemon=True).start()

    # -------------------------------------------------------------------- quit
    def quit(self) -> None:
        log.info("shutting down")
        self._stop.set()
        try:
            self.close_assistant("quitting")
        except Exception:
            pass
        try:
            if self.dictation.active:
                self.dictation.cancel("quitting")
        except Exception:
            pass
        self._stop_wake()
        for stop in (self.hotkeys.stop, self.sessions.stop, self.tray.stop,
                     self.overlay.stop):
            try:
                stop()
            except Exception:
                pass
        try:
            db().close()
        except Exception:
            pass


# Backend (delegation) instructions. The official Live docs prescribe splitting
# prompts: conversation style and delegation guidance live in the voice session's
# `instructions`, while BUSINESS RULES AND TOOL-USE instructions belong in
# `delegation.responses.instructions`. Without this the backend model ran with no
# system prompt at all. Override with the `backend_instructions` config key.
BACKEND_INSTRUCTIONS = (
    "You are the backend model behind a voice assistant running on the user's Windows "
    "PC. Whatever you return is read aloud, so reply with plain spoken text: no "
    "markdown, no headings, no bullet points, no code fences. Be concise - a "
    "sentence or two unless more is genuinely required. "
    "Use the web_search tool whenever the answer depends on current information, "
    "then answer directly from what you found. "
    "Report exactly what you did or found. Never claim an action succeeded unless "
    "you actually performed it, and if something failed or you are unsure, say so "
    "plainly rather than guessing."
)

# Headroom the local ceiling must still have before the assistant is allowed to
# use the dearer escalated backend model.
ESCALATION_HEADROOM_USD = 0.05


class AssistantSession:
    """A GPT-Live conversation with delegation, with real barge-in.

    ECHO CONTROL: while the model's audio is playing we mute the session's own
    input using the documented `session.input_audio.mute` / `unmute` events, so
    the microphone cannot feed the assistant's voice back to it. That is a real
    control we can assert, unlike claiming acoustic echo cancellation, which
    depends on the physical setup and is not something this app performs.

    DELEGATION: the default is the documented *Responses* delegation, where
    GPT-Live calls a configured backend model and returns results. The backend
    model is the cheap one by default and the more capable one only when
    escalation is allowed and affordable - and the app reports which was used.
    """

    def __init__(self, app: Application):
        self.app = app
        self.cfg = app.cfg
        self.session: Optional[live.LiveSession] = None
        self.speaker = audio_playback.Speaker(rate=live.DEFAULT_RATE,
                                             device=self.cfg.get("output_device"))
        self.capture: Optional[audio_capture.MicrophoneCapture] = None
        self._muted = False
        self._last_audio = 0.0
        self._monitor: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._opened_at = 0.0
        self._last_activity = 0.0
        self._idle_closing = False
        self._was_speaking = False
        self._suppress_reply = False
        self._last_meter = 0.0
        self._backend_recorded = set()
        self._tool_bridge = None

    # ------------------------------------------------------------------ open
    def _delegation(self, escalate: bool = False) -> dict:
        backend = str(self.cfg.get("backend_model", "gpt-5.6-luna"))
        # Escalate to the more capable (dearer) model only when the budget can
        # still AFFORD it. `would_exceed(x)` is True when spending another $x
        # would break the ceiling, so the test has to be negated - an earlier
        # version omitted the `not` and therefore switched to the expensive
        # model precisely when the user had already run out of budget.
        if (escalate and self.cfg.get("allow_escalation")
                and self.cfg.get("backend_model_escalated")
                and not self.app.budget.would_exceed(ESCALATION_HEADROOM_USD)):
            backend = str(self.cfg.get("backend_model_escalated"))
        tools = []
        if self.cfg.get("tools_enabled", True):
            tools = [{"type": "web_search"}]
            from .core.delegation import function_schemas
            tools.extend(function_schemas())
        if self.cfg.get("backend_instructions"):
            backend_instructions = str(self.cfg.get("backend_instructions"))
        else:
            backend_instructions = BACKEND_INSTRUCTIONS
        return {"type": "responses",
                "responses": {"model": backend,
                              "instructions": backend_instructions,
                              "tools": tools,
                              "tool_choice": "auto" if tools else "none",
                              # Each call raises its OWN blocking, default-No
                              # native approval dialog, so calls must not arrive
                              # batched: the official delegation guide documents
                              # parallel_tool_calls=false for exactly this case
                              # ("false when calls must run sequentially").
                              "parallel_tool_calls": False}}

    def open(self) -> None:
        config_session = live.LiveConfig(
            instructions=self._instructions(),
            voice=str(self.cfg.get("voice") or live.DEFAULT_VOICE),
            delegation=self._delegation(),
            dictation=False,
        )
        self.session = live.LiveSession(
            config_session, on_audio=self._on_audio, on_event=self._on_event)
        RUNNER.run(self.session.connect(), timeout=30)
        if self._stop.is_set():
            self.close("cancelled while connecting")
            return
        from .core.delegation import DelegatedTools
        self._tool_bridge = DelegatedTools(self)
        self._opened_at = time.monotonic()
        self._last_activity = self._opened_at
        self.speaker.start()
        self.capture = audio_capture.MicrophoneCapture(
            rate=live.DEFAULT_RATE,
            device=audio_capture.resolve_device(self.cfg.get("input_device")),
            gain=float(self.cfg.get("input_gain", 1.0)),
            on_chunk=self._on_mic_chunk,
            on_level=self.app._on_mic_level,
            max_seconds=86400,
        )
        self.capture.start()
        self._monitor = threading.Thread(target=self._monitor_output, name="assistant-mon",
                                         daemon=True)
        self._monitor.start()
        backend = self._delegation()["responses"]["model"]
        self.app.overlay.set_state(
            state="listening", transcript="", detail=f"listening · backend {backend}",
            esc_hint="ESC to stop", actions=["Stop speaking"], session_seconds=0)
        self.app.overlay.show()

    def _instructions(self) -> str:
        return (
            "You are the user's voice assistant on their Windows PC. Keep replies "
            "very short - a sentence or two. Delegate requests that need current "
            "information or real work to the backend. Never claim you have done "
            "something on the user's computer unless the backend reported it. If "
            "a request is ambiguous, ask one short question."
        )

    # ------------------------------------------------------------------ audio
    def _on_mic_chunk(self, pcm: bytes) -> None:
        if self._stop.is_set() or self.session is None or self._muted:
            return
        RUNNER.submit(self.session.append_audio(pcm))
        if self.capture is not None:
            self.capture.take()  # assistant streams, it does not retain recordings

    def _on_audio(self, pcm: bytes) -> None:
        if self._stop.is_set() or self._suppress_reply:
            return
        self._last_audio = time.monotonic()
        self._last_activity = self._last_audio
        self.speaker.play(pcm)

    def stop_speaking(self) -> int:
        """Barge-in. Drops the queued reply audio; the session stays open.

        Distinct from emergency_stop(): this silences the current sentence and
        nothing else. Returns the number of bytes dropped.
        """
        dropped = self.speaker.flush("barge-in")
        self._suppress_reply = True
        self._last_audio = 0.0
        self._last_activity = time.monotonic()
        self.app.overlay.set_state(state="listening", detail="stopped speaking")
        return dropped

    def _on_event(self, etype: str, event: dict) -> None:
        if etype in ("session.input_transcript.delta", "session.output_transcript.delta"):
            self._last_activity = time.monotonic()
        if etype == "session.input_transcript.delta":
            self._suppress_reply = False
            # What the user said. This is what the pill quotes (see mockup.png);
            # the assistant's own words go in the detail line instead.
            text = self.session.input_transcript.text() if self.session else ""
            self.app.overlay.set_state(state="listening", transcript=text)
        elif etype == "session.output_transcript.delta":
            text = self.session.output_transcript.text() if self.session else ""
            self.app.overlay.set_state(state="working", detail=text)
        elif etype == "session.usage.updated":
            # Cumulative snapshot, and NOT emitted for short sessions - the pill
            # clock is driven locally in _monitor_output(); this only corrects it
            # upward when the server does report.
            secs = float(((event.get("usage") or {}).get("seconds")) or 0.0)
            if secs > 0:
                self.app.overlay.set_state(session_seconds=secs)
        elif etype == "response.event":
            if self._tool_bridge is not None:
                self._tool_bridge.handle(event)
            nested = (event.get("event") or {}).get("type", "")
            if nested:
                self.app.overlay.set_state(state="working", detail="Processing your request")
        elif etype == "session.closed":
            if not self._stop.is_set():
                threading.Thread(target=self.app.close_assistant,
                                 args=("voice session ended",), daemon=True).start()

    def _monitor_output(self) -> None:
        """Echo control, the visible session clock, and the inactivity timeout.

        A Live session is billed per second of CONNECTED duration, so leaving one
        open costs money whether or not anyone is talking. Spec section 11
        requires a configurable inactivity timeout, a visible clock and an
        explicit keep-listening override; `idle_timeout_seconds` and
        `keep_listening_override` previously existed in config.py and were read
        by nothing at all.

        The timeout never fires mid-utterance: activity is any inbound event
        (which includes the user's own input-transcript fragments), any assistant
        audio, and the speaker still draining.
        """
        while not self._stop.is_set():
            self._stop.wait(0.15)
            if self.session is None:
                continue
            now = time.monotonic()
            speaking = self.speaker.playing or (now - self._last_audio) < 0.5
            try:
                if speaking and not self._muted:
                    self._muted = True
                    RUNNER.submit(self.session.mute_input("echo_guard"))
                elif not speaking and self._muted:
                    self._muted = False
                    RUNNER.submit(self.session.unmute_input("echo_guard"))
            except Exception:
                pass

            # The microphone is muted while the assistant talks, so without
            # this the orb would go flat exactly when there is most to see.
            # Feed it the SPEAKER level instead and flag which voice it is.
            if speaking != self._was_speaking:
                self.app.overlay.set_state(speaking=speaking)
                self._was_speaking = speaking
            if speaking:
                try:
                    self.app.overlay.set_level(self.speaker.level)
                except Exception:
                    pass

            # Visible session clock, driven locally: session.usage.updated is not
            # emitted for short sessions, so a server-only clock reads 0:00.
            self.app.overlay.set_state(session_seconds=now - self._opened_at)

            if now - self._last_meter >= 1.0:
                self._last_meter = now
                current = self.session
                if current is not None and current.session_id:
                    seconds = max(current.usage_seconds, now - self._opened_at)
                    self.app.budget.record_live_snapshot(current.session_id, seconds,
                                                         finalized=False)
                    self.app.overlay.set_state(spend_usd=self.app.budget.snapshot().today_usd)
                    if current._dead:
                        threading.Thread(target=self.app.close_assistant,
                                         args=("connection lost",), daemon=True).start()

            self._check_idle(now, speaking)

    def _check_idle(self, now: float, speaking: bool) -> None:
        timeout = float(self.cfg.get("idle_timeout_seconds", 45) or 0)
        if timeout <= 0 or self.cfg.get("keep_listening_override"):
            return
        if self._idle_closing or speaking:
            return
        last_heard = self._last_activity
        if self.capture is not None and self.capture.level > 0.008:
            self._last_activity = now
            return
        if self._tool_bridge is not None and self._tool_bridge.busy:
            return
        if self.session is not None:
            # A fragment arriving means the user is mid-sentence; never cut that off.
            last_heard = max(last_heard, self.session.input_transcript.last_arrival)
        if now - last_heard < timeout:
            return
        self._idle_closing = True
        log.info("closing the Live session after %.0fs idle (billed per second)",
                 timeout)
        threading.Thread(
            target=lambda: self.app.close_assistant(
                f"closed after {int(timeout)}s with no speech"),
            name="assistant-idle-close", daemon=True).start()

    # ------------------------------------------------------------------ close
    def close(self, reason: str = "closed") -> None:
        self._stop.set()
        if self._tool_bridge is not None:
            self._tool_bridge.cancel()
        if self.capture is not None:
            try:
                self.capture.take()   # discard rather than retain
                self.capture.stop()
            except Exception:
                pass
            self.capture = None
        try:
            self.speaker.flush("session closing")
            self.speaker.stop()
        except Exception:
            pass
        session = self.session
        self.session = None
        if session is None:
            return
        try:
            report = RUNNER.run(session.close(graceful=True, timeout=15.0),
                                timeout=25)
        except Exception as exc:
            log.warning("graceful close failed: %s", exc)
            return
        seconds = float(getattr(report, "usage_seconds", 0.0) or 0.0)
        complete = bool(getattr(report, "complete", False))
        if session.session_id:
            self.app.budget.record_live_snapshot(session.session_id, seconds,
                                                 finalized=complete)
            if not complete:
                self.app.budget.mark_incomplete_finalization(session.session_id)
        log.info("assistant closed (%s): %.1fs, finalisation=%s, $%.5f",
                 reason, seconds, complete, pricing.live_seconds_to_usd(seconds))
        snap = self.app.budget.snapshot()
        self.app.overlay.set_state(state="sleeping", transcript="", detail=reason,
                                   session_seconds=0.0, spend_usd=snap.today_usd,
                                   speaking=False, actions=[], esc_hint="",
                                   hint=self.app._idle_hint())
