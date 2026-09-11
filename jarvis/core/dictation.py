"""The dictation pipeline: capture -> engine -> optional cleanup -> safe insert.

State machine
-------------
    sleeping -> dictating -> thinking -> working -> sleeping
                 (cancel)    (error)

Every step is explicit because the build spec requires each to be reportable:
"Sleeping, Dictating, Listening, Thinking, Working, Approval needed, Error, and
Muted". Those labels ARE the state values — see the constants below; there is
deliberately no second internal vocabulary to keep in step with them.

Decisions encoded here (each one is a fix for something a test found):

* CAPTURE STARTS BEFORE ANY CLOUD CALL. The target window/control is snapshotted
  first, before the overlay is shown, so the overlay can never become the target.
* A BILLED LIVE SESSION IS ONLY OPEN WHILE CAPTURING. It is explicitly closed -
  never left open because the UI is hidden. A silent open session still costs
  money at $0.05/minute.
* SILENT AUDIO IS NEVER UPLOADED. Local VAD gates the Economy path and flags the
  Live path, so a fabricated transcript can neither be paid for nor inserted.
* AN INCOMPLETE TRANSCRIPT IS NOT INSERTED SILENTLY. If the Live drain could not
  confirm the tail, or the text looks invented, the result is HELD in the pill
  with Copy/Insert instead.
* THE TARGET IS RE-VALIDATED before typing, and a terminal or unknown control
  gets preview/copy rather than an auto-typed multiline paste (which can execute
  a command with no Enter).
* COST IS RECORDED FROM WHAT THE SERVER CONFIRMED, and an unconfirmed final
  usage is marked as such rather than invented.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .. import config
from ..audio import capture as audio_capture
from ..audio import vad
from ..core.asyncrun import RUNNER
from ..db import db
from ..engines import cleanup as cleanup_engine
from ..engines import errors, live, transcribe
from ..logsetup import get as _log
from ..win import insert as insert_mod
from ..win import target as target_mod

log = _log("dictation")

# THE STATE NAMES ARE THE UI STATE NAMES. There is exactly one vocabulary.
#
# An earlier version had two: these constants said "idle"/"capturing"/... while
# every _set_state() call wrote the spec's UI labels ("sleeping"/"dictating"/...).
# Nothing matched, so `end()` returned None on every release (dictation could
# never finish), `toggle()` was dead code, and `active` — defined as
# `state != IDLE` — latched True forever the moment the first state was set,
# which made `begin()` refuse every subsequent press. If you add a state, add it
# here and use the constant; never write a bare string.
IDLE = "sleeping"
CAPTURING = "dictating"
TRANSCRIBING = "thinking"
INSERTING = "working"
ERROR = "error"

# `active` means "an utterance is in flight", NOT "state is not IDLE": a failed
# begin() leaves ERROR behind, and treating that as active would lock dictation
# out permanently.
BUSY_STATES = frozenset({CAPTURING, TRANSCRIBING, INSERTING})


@dataclass
class DictationResult:
    ok: bool
    text: str = ""
    raw: str = ""
    engine: str = ""
    state: str = IDLE
    detail: str = ""
    usd: float = 0.0
    audio_seconds: float = 0.0
    latency_ms: float = 0.0
    inserted: bool = False
    held_for_user: bool = False
    requires_confirmation: bool = False
    target: str = ""
    rejected: bool = False
    quality_notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok, "text": self.text, "raw": self.raw,
            "engine": self.engine, "detail": self.detail,
            "usd": self.usd, "audio_seconds": round(self.audio_seconds, 2),
            "latency_ms": round(self.latency_ms, 1),
            "inserted": self.inserted, "held_for_user": self.held_for_user,
            "requires_confirmation": self.requires_confirmation,
            "target": self.target, "rejected": self.rejected,
            "quality_notes": self.quality_notes,
        }


class DictationController:
    """Owns one utterance at a time. Thread-safe."""

    def __init__(self,
                 on_state: Optional[Callable[[str, str], None]] = None,
                 on_result: Optional[Callable[[DictationResult], None]] = None,
                 on_level: Optional[Callable[[float], None]] = None,
                 budget=None):
        self._lock = threading.RLock()
        self.on_state = on_state
        self.on_result = on_result
        self.on_level = on_level
        self.budget = budget

        self.state = IDLE
        self._capture: Optional[audio_capture.MicrophoneCapture] = None
        self._target: Optional[target_mod.TargetInfo] = None
        self._started_at = 0.0
        self._cancel = threading.Event()
        self._pending: Optional[DictationResult] = None
        self._transcribe = transcribe.TranscribeEngine()
        self._cleanup = cleanup_engine.CleanupEngine()
        self._processing = False
        self._live_future = None

    # ------------------------------------------------------------------ state
    def _set_state(self, state: str, detail: str = "") -> None:
        self.state = state
        if self.on_state:
            try:
                self.on_state(state, detail)
            except Exception as exc:
                log.debug("state callback failed: %s", exc)

    @property
    def active(self) -> bool:
        return self.state in BUSY_STATES

    @property
    def held_text(self) -> str:
        return self._pending.text if self._pending else ""

    # ------------------------------------------------------------------ begin
    def begin(self, target: Optional[target_mod.TargetInfo] = None,
              auto_stop: bool = False) -> bool:
        """Start capturing. Returns False if capture could not start.

        `auto_stop` arms the hands-free silence watchdog (`vad_silence_ms`). It
        is for the TOGGLE path only - hold-to-talk has an explicit release and
        must never be cut off by a pause in speech.
        """
        with self._lock:
            if self.active or self._processing:
                return False
            if self.budget is not None:
                try:
                    self.budget.check_can_start(category="live_voice")
                except Exception as exc:
                    self._set_state(ERROR, str(exc))
                    self._emit(DictationResult(
                        False, detail=str(exc), requires_confirmation=False))
                    return False

            # Snapshot the insert target BEFORE the overlay is shown.
            self._target = target or target_mod.capture_target()
            self._cancel.clear()
            self._pending = None
            self._started_at = time.monotonic()
            self._transcribe.reset()
            self._cleanup.reset()

            cap = audio_capture.MicrophoneCapture(
                rate=live.DEFAULT_RATE,
                device=config.get("input_device"),
                gain=float(config.get("input_gain", 1.0)),
                on_level=self.on_level,
                max_seconds=float(config.get("max_utterance_seconds", 180)),
            )
            try:
                cap.start()
            except Exception as exc:
                log.error("microphone failed to start: %s", exc)
                self._set_state(ERROR, f"microphone unavailable: {exc}")
                self._emit(DictationResult(
                    False, detail=f"could not open the microphone: {exc}"))
                return False
            self._capture = cap
            self._set_state(CAPTURING,
                            f"listening — target: {self._target.describe()}")
        if auto_stop:
            self._arm_silence_watchdog()
        threading.Thread(target=self._watch_capture_limit, args=(cap,),
                         name="dictation-limit", daemon=True).start()
        return True

    def _watch_capture_limit(self, cap) -> None:
        while not self._cancel.wait(0.1):
            with self._lock:
                if self._capture is not cap or self.state != CAPTURING:
                    return
            if cap.overflowed:
                self.end()
                return

    # ------------------------------------------------- hands-free auto-stop
    def _arm_silence_watchdog(self) -> None:
        """End a hands-free utterance after `vad_silence_ms` of silence.

        `vad_silence_ms` was a config key (and a documented feature: "hands-free
        auto-stop") that NOTHING read, so F8 dictation ran until the user
        pressed F8 again - or until the 180 s utterance cap, with a billed Live
        session open the whole time.

        Two guards keep this from cutting the user off:
          * it never fires until real speech has been heard (see
            MicrophoneCapture.silence_seconds), so a slow start is safe; and
          * it needs a FULL uninterrupted `vad_silence_ms` of silence.
        Set `vad_silence_ms` to 0 to switch it off.
        """
        quiet_ms = float(config.get("vad_silence_ms", 900) or 0)
        if quiet_ms <= 0:
            return
        quiet_s = quiet_ms / 1000.0
        cap = self._capture

        def watch() -> None:
            while not self._cancel.is_set():
                time.sleep(0.1)
                with self._lock:
                    if self.state != CAPTURING or self._capture is not cap:
                        return          # ended, cancelled, or superseded
                if cap is None or not cap.running:
                    return
                if cap.silence_seconds >= quiet_s:
                    log.info("hands-free auto-stop after %.0fms of silence",
                             quiet_ms)
                    try:
                        self.end()
                    except Exception as exc:
                        log.error("auto-stop failed: %s", exc)
                    return

        threading.Thread(target=watch, name="dictation-autostop",
                         daemon=True).start()

    # -------------------------------------------------------------------- end
    def end(self) -> Optional[DictationResult]:
        """Finish the utterance and insert the result."""
        with self._lock:
            if self.state != CAPTURING or self._processing:
                return None
            self._processing = True
        try:
            return self._finish()
        finally:
            with self._lock:
                self._processing = False

    def _finish(self) -> Optional[DictationResult]:
        with self._lock:
            if self.state != CAPTURING:
                return None
            cap = self._capture
            target = self._target
            started = self._started_at
            self._set_state(TRANSCRIBING, "finishing the transcript")

        pcm = b""
        speech_seen = False
        overflowed = False
        try:
            if cap is not None:
                cap.stop()
                pcm = cap.take()
                speech_seen = cap.speech_detected
                overflowed = cap.overflowed
        finally:
            if cap is not None:
                cap.stop()
        self._capture = None

        audio_seconds = len(pcm) / 2 / float(live.DEFAULT_RATE)
        engine = str(config.get("dictation_engine", "live")).lower()
        result: DictationResult

        try:
            if engine == "economy":
                result = self._run_economy(pcm, audio_seconds)
            else:
                result = self._run_live(pcm, audio_seconds, speech_seen)
        except errors.EngineError as exc:
            result = DictationResult(False, detail=str(exc), engine=engine,
                                     audio_seconds=audio_seconds)
        except Exception as exc:
            log.exception("dictation failed")
            result = DictationResult(False, detail=f"dictation failed: {exc}",
                                     engine=engine, audio_seconds=audio_seconds)

        result.engine = engine
        result.audio_seconds = audio_seconds
        result.latency_ms = (time.monotonic() - started) * 1000.0
        if overflowed:
            result.requires_confirmation = True
            result.quality_notes.append(
                f"recording stopped at the {config.get('max_utterance_seconds')}s cap")
        if self._cancel.is_set():
            self._set_state(IDLE, "cancelled")
            r = DictationResult(False, detail="cancelled", engine=engine)
            self._emit(r)
            return r

        if not result.ok or not result.text.strip():
            self._set_state(IDLE, result.detail or "nothing captured")
            self._emit(result)
            return result

        # ---- optional cleanup (never allowed to damage the meaning) -----
        raw_text = result.text
        if config.get("cleanup_enabled", False):
            if self.budget is not None:
                try:
                    self.budget.check_can_start(category="backend")
                except Exception:
                    result.raw = raw_text
                    result.requires_confirmation = True
                    result.detail = "cleanup blocked by local spending ceiling; raw text kept"
                    self._insert(result, target)
                    self._set_state(IDLE, "")
                    self._emit(result)
                    return result
            self._set_state(TRANSCRIBING, "applying your text style")
            cleaned = self._cleanup.apply(
                raw_text, context_hint=cleanup_engine.vocab_context())
            # The raw transcript is ALWAYS kept, so a rewrite can never conceal
            # what was actually captured.
            result.raw = raw_text
            result.text = cleaned.text
            if cleaned.usd:
                # Cleanup tokens are a genuine increment, recorded directly with
                # the exact computed cost (CleanupResult carries the USD amount
                # rather than raw token counts).
                result.usd += cleaned.usd
                db().record_usage("backend", cleaned.usd, session_id=None,
                                  meta={"model": cleaned.model,
                                        "note": "dictation cleanup",
                                        "style": cleaned.style})
            if cleaned.rejected:
                result.rejected = True
                result.quality_notes.append(cleaned.detail)
            elif cleaned.applied and cleaned.changed:
                result.quality_notes.append(
                    f"'{cleaned.style}' style applied; raw text kept for comparison")
        else:
            result.raw = raw_text

        if self._cancel.is_set():
            self._set_state(IDLE, "cancelled")
            return DictationResult(False, detail="cancelled", engine=engine)

        # ---- was it a spoken instruction rather than text? --------------
        if self._run_spoken_command(result):
            self._set_state(IDLE, "")
            self._emit(result)
            return result

        # ---- insertion --------------------------------------------------
        self._set_state(INSERTING, "inserting")
        self._insert(result, target)

        self._set_state(IDLE, "")
        self._emit(result)
        return result

    # ------------------------------------------------------- spoken commands
    def _run_spoken_command(self, result: DictationResult) -> bool:
        """Run "open Chrome" / "search for X" instead of typing the words.

        Returns True when the utterance WAS an instruction and has been handled,
        so the caller must not also insert it as text.

        Two safety properties this relies on and must keep:
          * `core.commands.parse` only matches an explicit LEADING verb, and
            returns None for anything else - so ordinary dictation is untouched.
          * the action itself goes through `core.tools.call`, which owns the
            approval gate, the allowlists, the audit trail and the hard stop.
            Nothing here decides whether an action is permitted.

        Both imports are defensive for the same reason app.py's are: a broken or
        absent tool registry must never stop plain dictation working.
        """
        if (result.requires_confirmation or self._cancel.is_set()
                or not config.get("voice_commands_enabled", True)):
            return False
        try:
            from . import commands as commands_mod
        except Exception as exc:
            log.debug("spoken commands unavailable: %s", exc)
            return False

        cmd = commands_mod.parse(result.text, enabled=True)
        if cmd is None:
            return False

        if cmd.verb == "type":
            # Not an action - just strip the spoken "type" prefix and let the
            # normal insertion path handle the rest, target checks and all.
            if cmd.remainder:
                result.raw = result.raw or result.text
                result.text = cmd.remainder
                result.quality_notes.append('removed the spoken "type" prefix')
            return False

        from . import tools as command_tools
        if config.get("voice_command_confirm", True) and not command_tools.is_hard_stopped():
            import ctypes
            import json
            prompt = (cmd.say + "\n\n" + json.dumps(cmd.args, ensure_ascii=False)
                      + "\n\nRun this exact action?")
            if ctypes.windll.user32.MessageBoxW(None, prompt, "Jarvis: confirm action",
                                                0x24 | 0x100) != 6:
                result.ok = False
                result.detail = "spoken action declined; nothing ran"
                return True
            if self._cancel.is_set():
                result.ok = False
                result.detail = "cancelled; nothing ran"
                return True

        try:
            from . import tools as tools_mod
        except Exception as exc:
            log.warning("cannot run '%s': the tool registry is unavailable (%s)",
                        cmd.verb, exc)
            result.detail = ("that looked like a command, but the tool registry "
                             "is unavailable")
            result.ok = False
            return True

        self._set_state(INSERTING, cmd.say)
        db().add_turn("user", result.text,
                      meta={"engine": result.engine, "command": cmd.verb,
                            "tool": cmd.tool, "spoken_command": True})
        out = tools_mod.call(cmd.tool, cmd.args)
        result.inserted = False
        result.ok = bool(out.ok)
        result.detail = out.detail or cmd.say
        result.quality_notes.append(f"ran the spoken command: {cmd.say}")
        if out.needs_approval:
            result.requires_confirmation = True
            result.held_for_user = True
            self._pending = result
        log.info("spoken command %r -> %s ok=%s", cmd.say, cmd.tool, out.ok)
        db().audit("dictation.command",
                   {"verb": cmd.verb, "tool": cmd.tool, "ok": bool(out.ok)},
                   allowed=bool(out.ok))
        return True

    # ------------------------------------------------------------------ cancel
    def cancel(self, reason: str = "cancelled") -> None:
        with self._lock:
            self._cancel.set()
            cap = self._capture
            self._capture = None
            future = self._live_future
            self._pending = None
        if future is not None:
            future.cancel()
        if cap is not None:
            # Discard the buffer rather than keeping ambient audio.
            cap.take()
            cap.stop()
        self._set_state(IDLE, reason)
        db().audit("dictation.cancel", {"reason": reason})

    def toggle(self) -> Optional[DictationResult]:
        if self.state == CAPTURING:
            return self.end()
        if not self.active:
            # Not just IDLE: a previous ERROR must not make F8 stop working.
            self.begin()
        return None

    # ------------------------------------------------------------------ engines
    def _run_economy(self, pcm: bytes, audio_seconds: float) -> DictationResult:
        self._set_state(TRANSCRIBING, "transcribing (Economy)")
        res = self._transcribe.transcribe_captured(
            pcm, live.DEFAULT_RATE,
            keywords=transcribe.config_transcribe_keys(),
            prompt=None,
            languages=["en"],
        )
        if not res.ok:
            return DictationResult(False, detail=res.reason, engine="economy",
                                   requires_confirmation=(res.kind == "no_speech"))
        if self.budget is not None:
            self.budget.record_transcribe(res.audio_seconds, model=res.model)
        return DictationResult(True, text=res.text, raw=res.text,
                               engine="economy", usd=res.usd,
                               audio_seconds=res.audio_seconds,
                               detail="transcribed with gpt-transcribe")

    def _run_live(self, pcm: bytes, audio_seconds: float,
                  speech_seen: bool) -> DictationResult:
        self._set_state(TRANSCRIBING, "finishing the Live transcript")
        info = vad.analyze(pcm, live.DEFAULT_RATE)
        if not pcm or not info["has_speech"]:
            return DictationResult(False, engine="live",
                                   detail="no speech detected; nothing was uploaded")

        async def source():
            step = int(live.DEFAULT_RATE * 0.1) * 2
            for i in range(0, len(pcm), step):
                if self._cancel.is_set():
                    break
                yield pcm[i:i + step]
                await asyncio.sleep(len(pcm[i:i + step]) / (2 * live.DEFAULT_RATE))

        cfg = live.LiveConfig(instructions=live.DICTATION_INSTRUCTIONS,
                              dictation=True)
        session = live.LiveSession(cfg)

        async def run() -> dict:
            try:
                return await session.run_dictation(
                    source(),
                    drain_ms=float(config.get("drain_ms", 1600)),
                    max_seconds=float(config.get("max_utterance_seconds", 180)),
                    speech_detected=speech_seen,
                )
            finally:
                if self.budget is not None and session.session_id:
                    self.budget.record_live_snapshot(session.session_id,
                                                     session.usage_seconds,
                                                     finalized=session.usage_final)

        try:
            self._live_future = RUNNER.submit(run())
            if self._cancel.is_set():
                self._live_future.cancel()
            data = self._live_future.result(timeout=audio_seconds + 65)
        except Exception as exc:
            if self._live_future is not None:
                self._live_future.cancel()
            return DictationResult(False, detail=errors.from_exception(
                exc, live.MODEL).detail, engine="live")
        finally:
            self._live_future = None

        if not data.get("ok"):
            return DictationResult(False, engine="live",
                                   detail=str(data.get("error") or data.get("reason")
                                              or "the Live session failed"),
                                   requires_confirmation=bool(
                                       data.get("requires_confirmation")))
        if self.budget is not None and data.get("session_id"):
            self.budget.record_live_snapshot(
                data["session_id"], float(data.get("usage_seconds") or 0.0),
                finalized=bool(data.get("finalization_complete")))
            if not data.get("finalization_complete"):
                self.budget.mark_incomplete_finalization(data["session_id"])

        notes = list(data.get("notes") or [])
        if not info["has_speech"]:
            notes.append("very little audio energy was detected")
        return DictationResult(
            True, text=data.get("text", ""), raw=data.get("text", ""),
            engine="live", usd=float(data.get("usd") or 0.0),
            requires_confirmation=bool(data.get("requires_confirmation")),
            detail=data.get("detail") or "transcribed with gpt-live-1",
            quality_notes=notes,
        )

    # ------------------------------------------------------------------ insert
    def _insert(self, result: DictationResult, target: Optional[target_mod.TargetInfo]) -> None:
        if target is None:
            result.held_for_user = True
            result.detail = "no target window was captured; the text is held here"
            self._pending = result
            return

        if result.requires_confirmation:
            # Never auto-type a transcript we have reason to distrust.
            result.held_for_user = True
            self._pending = result
            result.detail = ("held for review: " + (result.detail or "")
                             + " — check it before inserting")
            db().audit("dictation.hold", {"reason": result.detail,
                                          "text_len": len(result.text)})
            return

        out = insert_mod.insert_text(result.text, target)
        result.inserted = bool(out.ok and out.method in ("unicode", "paste"))
        result.held_for_user = out.held_for_user
        if out.held_for_user:
            self._pending = result
        if out.detail:
            result.detail = out.detail if result.inserted else \
                f"{out.detail} ({result.detail})"
        db().add_turn("user", result.text, meta={"engine": result.engine,
                                                 "inserted": result.inserted})
        self._set_state(INSERTING if result.inserted else ERROR,
                        "inserted" if result.inserted else out.detail)

    # ------------------------------------------------------------------ helpers
    def reinsert_pending(self) -> DictationResult:
        """Deliberate re-insert of text that was held for review."""
        with self._lock:
            pending = self._pending
        if not pending or not pending.text:
            return DictationResult(False, detail="there is nothing held to insert")
        target = target_mod.capture_target()
        out = insert_mod.insert_text(pending.text, target, force_preview=False)
        pending.inserted = bool(out.ok and out.method in ("unicode", "paste"))
        if pending.inserted:
            self._pending = None
        pending.detail = out.detail
        return pending

    def copy_pending_to_clipboard(self) -> bool:
        with self._lock:
            pending = self._pending
        if not pending or not pending.text:
            return False
        insert_mod._set_clipboard_text(pending.text)
        return True

    def undo_last(self):
        return insert_mod.undo_last_insertion()

    def _emit(self, result: DictationResult) -> None:
        if self.on_result:
            try:
                self.on_result(result)
            except Exception as exc:
                log.error("dictation result callback failed: %s", exc)


class _NoBudget:
    """Stand-in used when no BudgetManager was supplied (tests)."""

    def record_backend(self, *a, **k) -> float:
        return 0.0

    def record_transcribe(self, *a, **k) -> float:
        return 0.0

    def record_live_snapshot(self, *a, **k) -> float:
        return 0.0

    def mark_incomplete_finalization(self, *a, **k) -> None:
        return None
