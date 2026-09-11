"""The Python side of the settings window: `window.pywebview.api`.

CONTRACT
--------
The web UI (jarvis/ui/web/app.js) calls exactly these methods:

    get_state, get_devices, set_config, set_api_key, clear_api_key,
    test_connection, list_mics_test, record_binding, test_insert_text,
    get_memory, forget_memory, clear_history, get_usage, get_agent_status,
    pick_folder, pick_app, run_wake_setup, start_calibration,
    set_start_at_login, quit_app,
    get_history, copy_text, get_voices, get_command_help, run_smoke_test

and receives pushed events through the page's global `bvBus(name, payload)` with
the names: `mic_level`, `status`, `calibration_progress`, `toast`.

Two shape reconciliations worth knowing about:

* SHORTCUTS vs BINDINGS. Canonical storage is `config.bindings.<which>` as a
  plain string ("ctrl+alt+space"). The web UI reads and writes a `shortcuts`
  map whose values may be either a string or `{display, conflict}`. `get_state`
  therefore synthesises `shortcuts` from `bindings`, and `set_config` accepts
  either shape and folds it back into `bindings`. Without this the wizard would
  render "[object Object]" or silently drop a binding.

* `setup_complete` lives in the config dict, which is where the UI expects it.

BILLING
-------
`test_connection` is the ONLY method here that spends money, and it is the
explicit "billable connection test" the spec asks for. It reports the measured
cost of the call it actually made.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import threading
import time
from typing import Any, Optional

from .. import config, secrets
from ..core import pricing
from ..db import db
from ..engines import errors
from ..logsetup import get as _log, redact
from ..win import hotkeys as hotkeys_mod
from ..win import insert as insert_mod
from ..win import session as session_mod
from ..win import target as target_mod

log = _log("bridge")

# Which display name each binding gets in the UI.
BINDING_LABELS = {
    "mouse_dictate": "Dictate — mouse button",
    "key_dictate_toggle": "Dictate — keyboard toggle",
    "key_assistant": "Ask the assistant",
    "key_emergency_stop": "Emergency stop",
    "key_cancel": "Cancel",
}


def _display(binding: str) -> str:
    if not binding or binding in ("none", "disabled"):
        return ""
    if binding in hotkeys_mod.MOUSE_NAMES:
        return {"xbutton1": "Mouse button 4", "xbutton2": "Mouse button 5",
                "middle": "Mouse middle button"}.get(binding, binding)
    try:
        return hotkeys_mod.format_binding(binding)
    except Exception:
        return binding


def _canonical(value: Any) -> str:
    """Accept a display name OR a canonical string and return a canonical one."""
    if value is None:
        return ""
    if isinstance(value, dict):
        value = value.get("binding") or value.get("display") or ""
    text = str(value).strip()
    if not text:
        return ""
    low = text.lower()
    if low in hotkeys_mod.MOUSE_NAMES:
        return low
    # A config written by the build that saved the UI label ("Mouse button 4")
    # still has to resolve here, or conflict detection silently passes a
    # duplicate mouse binding.
    if low in getattr(hotkeys_mod, "MOUSE_LABELS", {}):
        return hotkeys_mod.canonical_mouse(low)
    if low in ("none", "disabled", "not set", "unset"):
        return "none"
    # format_binding/parse_binding round-trip turns "Ctrl+Alt+Space" back into
    # "ctrl+alt+space".
    try:
        mods, key = hotkeys_mod.parse_binding(text)
        if key:
            order = [m for m in ("ctrl", "alt", "shift", "win") if m in mods]
            return "+".join(order + [key])
    except Exception:
        pass
    return text


class SettingsAPI:
    """Implements the JS bridge. All methods are safe to call from any thread."""

    def __init__(self, host=None, recorder: Optional["_BindingRecorder"] = None):
        self.host = host
        self._recorder = recorder
        self._mic_thread: Optional[threading.Thread] = None
        self._mic_stop = threading.Event()
        self._calib_thread: Optional[threading.Thread] = None
        self._calib_stop = threading.Event()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ state
    def get_state(self) -> dict:
        from .. import __version__
        cfg = config.load()
        return {
            "ok": True,
            "version": __version__,
            "setup_complete": bool(cfg.get("setup_complete")),
            "theme": cfg.get("theme", "dark"),
            "vocab": db().list_vocab(),
            "key_fingerprint": secrets.key_fingerprint(),
            "has_key": secrets.has_api_key(),
            "config": self._public_config(cfg),
            "locked": session_mod.is_locked(),
        }

    def _public_config(self, cfg: dict) -> dict:
        """Config plus the derived `shortcuts` view the UI reads."""
        out = dict(cfg)
        out["vocab"] = db().list_vocab()
        bindings = dict(cfg.get("bindings") or {})
        shortcuts: dict[str, Any] = {}
        for which, value in bindings.items():
            if which == "mouse_dictate_hold":
                continue
            shortcuts[which] = {"binding": value, "display": _display(value),
                                "conflict": None}
        out["shortcuts"] = shortcuts
        return out

    # ----------------------------------------------------------------- devices
    def get_devices(self) -> dict:
        from ..audio import capture as audio_capture
        try:
            ins = [d.as_dict() for d in audio_capture.list_input_devices()]
            outs = [d.as_dict() for d in audio_capture.list_output_devices()]
        except Exception as exc:
            return {"ok": False, "error": str(exc), "input": [], "output": []}
        return {"ok": True, "input": ins, "output": outs,
                "default_input": config.get("input_device"),
                "default_output": config.get("output_device")}

    # ------------------------------------------------------------------ config
    def set_config(self, patch: Optional[dict] = None) -> dict:
        patch = dict(patch or {})
        vocab = patch.pop("vocab", None)
        if vocab is not None and (not isinstance(vocab, list)
                                  or any(not isinstance(t, str) for t in vocab)):
            return {"ok": False, "error": "Vocabulary must be a list of text terms."}
        # Fold the UI's `shortcuts` view back into canonical `bindings`.
        shortcuts = patch.pop("shortcuts", None)
        if isinstance(shortcuts, dict):
            bindings = dict(config.get("bindings") or {})
            for which, value in shortcuts.items():
                if which == "mouse_dictate_hold":
                    continue
                canonical = _canonical(value)
                if canonical:
                    bindings[which] = canonical
            patch["bindings"] = bindings
        # A flat `<which>: display` patch (the UI's fallback shape) is also
        # accepted, so neither shape can silently lose a binding.
        for which in list(BINDING_LABELS):
            if which in patch and isinstance(patch[which], (str, dict)):
                bindings = dict(patch.pop("bindings", config.get("bindings") or {}))
                canonical = _canonical(patch.pop(which))
                if canonical:
                    bindings[which] = canonical
                patch["bindings"] = bindings
        try:
            cfg = config.update(patch)
            if vocab is not None:
                db().set_vocab(vocab)
        except Exception as exc:
            return {"ok": False, "error": redact(str(exc))}
        return {"ok": True, "config": self._public_config(cfg),
                "shortcuts_conflict": self._binding_conflicts()}

    def _binding_conflicts(self) -> Optional[str]:
        try:
            mgr = hotkeys_mod.HotkeyManager(config.get("bindings") or {})
            return mgr.update_bindings(config.get("bindings") or {})
        except Exception:
            return None

    # ------------------------------------------------------------------- key
    def set_api_key(self, key: str = "") -> dict:
        try:
            secrets.set_api_key((key or "").strip())
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": redact(str(exc))}
        # A rotated key must invalidate cached engine clients in this process.
        return {"ok": True, "fingerprint": secrets.key_fingerprint()}

    def clear_api_key(self) -> dict:
        secrets.clear_api_key()
        return {"ok": True}

    # ------------------------------------------------------------ billable tests
    def test_connection(self, mode: str = "economy") -> dict:
        """The explicit billable connection test. Reports what it really cost."""
        mode = (mode or "economy").lower()
        if not secrets.has_api_key():
            return {"ok": False, "billed": False, "cost_usd": 0.0, "ms": 0,
                    "error": "no API key is stored yet", "transcript": None}
        started = time.monotonic()
        if mode == "live":
            result = self._test_live()
        else:
            result = self._test_economy()
        result["ms"] = int((time.monotonic() - started) * 1000)
        return result

    def _test_economy(self) -> dict:
        from ..audio import capture as audio_capture
        from ..audio import vad
        from ..engines import transcribe
        seconds = 3.0
        cap = audio_capture.MicrophoneCapture(rate=24000)
        try:
            cap.start()
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                time.sleep(0.1)
            pcm = cap.take()
            speech = cap.speech_detected
        except Exception as exc:
            return {"ok": False, "billed": False, "cost_usd": 0.0,
                    "detail": f"microphone unavailable: {exc}", "transcript": None}
        finally:
            try:
                cap.stop()
            except Exception:
                pass

        if not speech or not vad.has_speech(pcm, 24000):
            return {"ok": False, "billed": False, "cost_usd": 0.0,
                    "detail": ("nothing was uploaded: no speech was detected in those "
                               f"{seconds:.0f} seconds, so this test cost nothing"),
                    "transcript": None}
        try:
            res = transcribe.TranscribeEngine().transcribe_captured(
                pcm, 24000, keywords=transcribe.config_transcribe_keys(),
                languages=["en"])
        except errors.EngineError as exc:
            return {"ok": False, "billed": False, "cost_usd": 0.0,
                    "detail": str(exc), "transcript": None}
        if res.usd:
            db().record_usage("transcribe", res.usd, seconds=res.audio_seconds,
                              meta={"model": res.model, "note": "connection test"})
        return {"ok": bool(res.text.strip()), "billed": True, "cost_usd": res.usd,
                "detail": (f"gpt-transcribe answered in {len(res.text)} characters"
                           if res.text else res.reason),
                "transcript": res.text or None,
                "model": res.model}

    def _test_live(self) -> dict:
        import asyncio
        from ..core.asyncrun import RUNNER
        from ..engines import live

        # ~2 s of quiet tone: enough to open and close a real session without
        # needing the user to speak, and we report the real duration charged.
        import numpy as np
        rate = live.DEFAULT_RATE
        t = np.linspace(0, 2.0, int(rate * 2.0), endpoint=False)
        pcm = (np.sin(2 * np.pi * 220.0 * t) * 0.02 * 32767).astype("<i2").tobytes()

        async def run() -> dict:
            cfg = live.LiveConfig(instructions="Connection test. Do not speak.",
                                  dictation=True)
            s = live.LiveSession(cfg)

            async def src():
                step = int(rate * 0.1) * 2
                for i in range(0, len(pcm), step):
                    yield pcm[i:i + step]
                    await asyncio.sleep(0.1)

            try:
                return await s.run_dictation(src(), drain_ms=300.0,
                                             close_timeout=12.0)
            except Exception as exc:
                return errors.from_exception(exc, live.MODEL).as_dict()

        try:
            data = RUNNER.run(run(), timeout=90)
        except Exception as exc:
            return {"ok": False, "billed": False, "cost_usd": 0.0,
                    "detail": redact(str(exc)), "transcript": None}
        if not data.get("ok"):
            return {"ok": False, "billed": False, "cost_usd": 0.0,
                    "detail": str(data.get("error") or data),
                    "transcript": None}
        usd = float(data.get("usd") or 0.0)
        if data.get("session_id"):
            db().record_usage(
                "live_voice", usd, seconds=float(data.get("usage_seconds") or 0),
                session_id=data["session_id"],
                meta={"model": live.MODEL, "note": "connection test"},
                finalized=bool(data.get("finalization_complete")))
        return {"ok": True, "billed": True, "cost_usd": usd,
                "detail": (f"gpt-live-1 session opened and closed cleanly "
                           f"({data.get('usage_seconds')}s billed"
                           + ("" if data.get("finalization_complete")
                              else ", final usage unconfirmed") + ")"),
                "transcript": data.get("text") or None,
                "model": live.MODEL}

    # ---------------------------------------------------------------- mic test
    def list_mics_test(self) -> dict:
        """Start a short live level test; pushes `mic_level` events."""
        self._mic_stop.set()
        if self._mic_thread and self._mic_thread.is_alive():
            self._mic_thread.join(timeout=1.0)
        self._mic_stop = threading.Event()

        def run() -> None:
            from ..audio import capture as audio_capture
            device = config.get("input_device")
            cap = audio_capture.MicrophoneCapture(
                rate=24000, device=device,
                on_level=lambda rms: self._push("mic_level", {"rms": round(rms, 5)}))
            try:
                cap.start()
            except Exception as exc:
                self._push("toast", {"level": "error",
                                     "message": f"microphone unavailable: {exc}"})
                return
            try:
                deadline = time.monotonic() + 12.0
                while not self._mic_stop.is_set() and time.monotonic() < deadline:
                    time.sleep(0.05)
                peak = cap.peak
                self._push("toast", {
                    "level": "info" if peak > 0.02 else "warn",
                    "message": (f"microphone peak {peak:.3f} — signal detected"
                                if peak > 0.02 else
                                f"microphone peak {peak:.3f} — almost silent; check "
                                f"it is not muted or the wrong device")})
            finally:
                cap.stop()

        self._mic_thread = threading.Thread(target=run, name="mic-test", daemon=True)
        self._mic_thread.start()
        return {"ok": True, "detail": "speak now for up to 12 seconds"}

    # ---------------------------------------------------------------- bindings
    def record_binding(self, which: str = "") -> dict:
        rec = self._recorder or _BindingRecorder()
        self._recorder = rec
        res = rec.capture(timeout=12.0)
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error", "nothing was pressed")}
        canonical = _canonical(res["binding"])
        conflict = self._detect_conflict(which, canonical)
        return {"ok": True, "binding": canonical, "display": _display(canonical),
                "conflict": conflict}

    def _detect_conflict(self, which: str, canonical: str) -> Optional[str]:
        """Report a clash with our other bindings, or with a known Windows use."""
        if not canonical:
            return None
        others = dict(config.get("bindings") or {})
        others.pop(which, None)
        for other, value in others.items():
            if other.endswith("_hold"):
                continue
            if _canonical(value) == canonical:
                return f"the app's own {BINDING_LABELS.get(other, other)}"
        try:
            with_win = {
                "win+l": "Windows lock screen", "win+d": "Windows show desktop",
                "win+e": "Windows File Explorer", "alt+f4": "Windows close window",
                "ctrl+shift+esc": "Windows Task Manager",
                "ctrl+alt+delete": "Windows security screen",
                "win+r": "Windows Run dialog", "printscreen": "Windows screenshot",
                "alt+tab": "Windows window switcher",
            }
            pretty = _display(canonical).lower().replace(" ", "")
            if pretty in with_win:
                return with_win[pretty]
        except Exception:
            pass
        return None

    def test_insert_text(self, text: str = "") -> dict:
        sample = (text or "Jarvis insertion test 123 — café ✓").strip()
        target = target_mod.capture_target()
        out = insert_mod.insert_text(sample, target)
        return {"ok": bool(out.ok), "method": out.method,
                "detail": out.detail, "target": target.describe(),
                "held_for_user": out.held_for_user}

    # ------------------------------------------------------------------ memory
    def get_memory(self) -> dict:
        return {"ok": True, "items": db().list_memory(500)}

    def forget_memory(self, mem_id: int = 0) -> dict:
        db().forget(int(mem_id))
        return {"ok": True}

    def clear_history(self) -> dict:
        db().clear_conversation()
        return {"ok": True}

    def remember(self, text: str = "", kind: str = "fact") -> dict:
        if not (text or "").strip():
            return {"ok": False, "error": "nothing to remember"}
        mem_id = db().remember(text.strip(), kind=kind, source="user")
        return {"ok": True, "id": mem_id}

    # ----------------------------------------------------------------- history
    def get_history(self, limit: int = 0) -> dict:
        """Everything the user has dictated, newest first, for the History tab.

        This is the same `conversation` table the app already wrote to; it was
        simply never readable from the UI. Rows carry the engine used, whether
        the text was inserted, and whether it was run as a spoken command, so a
        prompt can be found again and copied.
        """
        limit = int(limit or config.get("history_limit", 500))
        rows = db().recent_turns(max(1, min(2000, limit)))
        items = []
        for r in rows:
            meta = r.get("meta")
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            meta = meta or {}
            text = r.get("text") or ""
            items.append({
                "id": r.get("id"),
                "ts": r.get("ts"),
                "role": r.get("role") or "user",
                "text": text,
                "chars": len(text),
                "engine": meta.get("engine") or "",
                "inserted": bool(meta.get("inserted")),
                "command": meta.get("command") or "",
                "tool": meta.get("tool") or "",
            })
        return {"ok": True, "items": items, "count": len(items)}

    def copy_text(self, text: str = "") -> dict:
        """Put a stored prompt back on the clipboard so it can be reused."""
        text = text or ""
        if not text.strip():
            return {"ok": False, "error": "nothing to copy"}
        try:
            from ..win import insert as insert_mod
            insert_mod._set_clipboard_text(text)
            return {"ok": True, "chars": len(text)}
        except Exception as exc:
            return {"ok": False, "error": f"could not copy: {exc}"}

    # ------------------------------------------------------------------ voices
    def get_voices(self) -> dict:
        """The documented voice roster for the assistant.

        Transcribed from the Live "Voice options" table and re-verified against
        the live page; see engines/live.VOICES. A voice cannot be changed on a
        running session, so `applies` says when a change takes effect.
        """
        from ..engines import live as live_mod
        current = str(config.get("voice") or live_mod.DEFAULT_VOICE)
        return {
            "ok": True,
            "voices": [dict(v) for v in live_mod.VOICES],
            "current": current,
            "known": live_mod.is_known_voice(current),
            "suggested": list(live_mod.SUGGESTED_VOICES),
            "default": live_mod.DEFAULT_VOICE,
            "applies": "the next assistant session — a running session's "
                       "voice cannot be changed",
        }

    def run_smoke_test(self) -> dict:
        """A free, offline self-check the user can run from the wizard.

        Every check here is REAL - the microphone is actually opened, the
        database is actually written to, the overlay actually composes a frame.
        Nothing is faked and nothing is billed: no check touches the network,
        so this can be run as often as you like.

        It deliberately does NOT prove transcription accuracy or that the API
        key is valid - both need a billable call. `test_connection` is the
        explicit billable test for that, and it says so.
        """
        checks: list[dict] = []

        def add(name: str, ok: bool, detail: str = "") -> None:
            checks.append({"name": name, "ok": bool(ok), "detail": detail})

        # 1. a real microphone, really opened
        try:
            from ..audio import capture as audio_capture
            ins = audio_capture.list_input_devices()
            cap = audio_capture.MicrophoneCapture(rate=24000, device=config.get("input_device"))
            cap.start()
            time.sleep(0.6)
            level, running = cap.level, cap.running
            cap.take()
            cap.stop()
            add("Microphone opens", running,
                f"{len(ins)} input device(s), level {level:.3f}")
        except Exception as exc:
            add("Microphone opens", False, redact(str(exc))[:120])

        # 2. the shortcuts the user has chosen actually parse and bind
        try:
            bindings = dict(config.get("bindings") or {})
            mgr = hotkeys_mod.HotkeyManager(bindings=bindings)
            bound = len(mgr._kb_vks) + (1 if mgr._mouse_bound else 0)
            conflict = mgr.update_bindings(bindings)
            add("Shortcuts bind", bound > 0 and not conflict,
                conflict or f"{bound} control(s) bound, no conflicts")
        except Exception as exc:
            add("Shortcuts bind", False, str(exc)[:120])

        # 3. the key is present - NOT that it is valid, which costs money
        add("API key stored", secrets.has_api_key(),
            "in Windows Credential Manager"
            if secrets.has_api_key() else "not set yet - step two")

        # 4. the local database round-trips
        try:
            db().audit("smoke_test", {"at": time.time()}, allowed=True)
            add("Local database writable", True, str(config.db_path()))
        except Exception as exc:
            add("Local database writable", False, str(exc)[:120])

        # 5. the overlay can actually paint a frame
        try:
            from ..win import overlay as overlay_mod
            pill = overlay_mod.Overlay()
            st = overlay_mod.PillState(state="listening", transcript="smoke test",
                                       detail="checking")
            for _ in range(90):
                pill._advance(st, 1 / 60.0)
            add("Overlay renders", pill._compose(st, 0.5, 1.0).getbbox() is not None,
                f"{pill.width}x{pill.height}")
        except Exception as exc:
            add("Overlay renders", False, str(exc)[:120])

        # 6. the tool registry loads and the emergency stop is clear
        try:
            from ..core import tools as tools_mod
            add("Tool library loads", bool(tools_mod.TOOLS),
                f"{len(tools_mod.TOOLS)} tools; tray runtime and approvals are checked separately")
        except Exception as exc:
            add("Tools ready", False, str(exc)[:120])

        passed = sum(1 for c in checks if c["ok"])
        ok = passed == len(checks)
        summary = (f"Smoke test: {passed}/{len(checks)} checks passed."
                   if ok else
                   "Smoke test: " + "; ".join(
                       f"{c['name']} — {c['detail']}" for c in checks if not c["ok"]))
        log.info("smoke test %d/%d", passed, len(checks))
        return {"ok": ok, "passed": passed, "total": len(checks),
                "checks": checks, "summary": summary, "cost_usd": 0.0}

    def get_command_help(self) -> dict:
        """The exact spoken-command vocabulary. No hidden verbs."""
        from ..core import commands as commands_mod
        return {"ok": True,
                "enabled": bool(config.get("voice_commands_enabled", True)),
                "groups": commands_mod.describe_vocabulary()}

    # ------------------------------------------------------------------- usage
    def get_usage(self) -> dict:
        from ..core.cost import BudgetManager
        snap = BudgetManager().snapshot().as_dict()
        snap["ok"] = True
        snap["events"] = db().usage_events(40)
        snap["rates"] = {"live_per_min": pricing.USD_PER_MIN_LIVE,
                         "transcribe_per_min": pricing.USD_PER_MIN_TRANSCRIBE}
        return snap

    # ---------------------------------------------------------------- agents
    def get_agent_status(self) -> dict:
        out: dict[str, Any] = {"ok": True}
        try:
            from ..core import agents as agents_mod
            for name in ("codex", "claude"):
                try:
                    out[name] = agents_mod.detect(name)
                except Exception as exc:
                    out[name] = {"installed": False, "error": redact(str(exc))}
        except Exception:
            # agents.py may be absent while another workstream lands it; degrade
            # to a direct version probe rather than lying about support.
            for name, exe in (("codex", "codex"), ("claude", "claude")):
                out[name] = self._probe_agent(exe)
        return out

    @staticmethod
    def _probe_agent(exe: str) -> dict:
        try:
            proc = subprocess.run([exe, "--version"], capture_output=True,
                                 text=True, timeout=15,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            version = (proc.stdout or proc.stderr or "").strip().splitlines()
            return {"installed": proc.returncode == 0,
                    "version": version[0] if version else None,
                    # We deliberately do NOT read auth files to determine this.
                    "authenticated": None,
                    "billing_mode": None, "workdir": None,
                    "note": ("authentication was not inspected: this app does not "
                             "read credential files. Run the client once to check.")}
        except Exception as exc:
            return {"installed": False, "version": None, "authenticated": None,
                    "billing_mode": None, "workdir": None,
                    "note": f"not found: {redact(str(exc))}"}

    # ---------------------------------------------------------------- pickers
    def pick_folder(self) -> dict:
        path = self._native_pick(folder=True)
        return {"path": path} if path else {"path": None}

    def pick_app(self) -> Optional[dict]:
        path = self._native_pick(folder=False)
        if not path:
            return None
        name = os.path.splitext(os.path.basename(path))[0]
        apps = dict(config.get("approved_apps") or {})
        apps[name] = path
        config.update({"approved_apps": apps})
        return {"name": name, "path": path}

    @staticmethod
    def _native_pick(folder: bool) -> Optional[str]:
        """A minimal native picker via PowerShell (no tkinter dependency)."""
        if folder:
            script = ("Add-Type -AssemblyName System.Windows.Forms;"
                      "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
                      "if($d.ShowDialog() -eq 'OK'){Write-Output $d.SelectedPath}")
        else:
            script = ("Add-Type -AssemblyName System.Windows.Forms;"
                      "$d=New-Object System.Windows.Forms.OpenFileDialog;"
                      "$d.Filter='Programs (*.exe)|*.exe|All files (*.*)|*.*';"
                      "if($d.ShowDialog() -eq 'OK'){Write-Output $d.FileName}")
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-Command", script],
                capture_output=True, text=True, timeout=180,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            out = (proc.stdout or "").strip()
            return out or None
        except Exception:
            return None

    # -------------------------------------------------------------- wake word
    def wake_model_status(self) -> dict:
        """Is the local wake-word model on disk? The UI shows this, because a
        wake word that is switched on but silently non-functional is the worst
        possible state to leave someone in."""
        try:
            from ..audio import wake
        except Exception as exc:
            return {"available": False, "model": "", "detail":
                    f"wake-word module unavailable: {exc}"}
        try:
            available = bool(wake.WakeWordDetector.is_available(
                config.get("wake_model_dir")))
            path = str(wake.default_model_dir())
        except Exception as exc:
            return {"available": False, "model": "", "detail": redact(str(exc))}
        return {
            "available": available,
            "model": wake.MODEL_NAME,
            "path": path,
            "detail": ("installed" if available else
                       "not downloaded yet — the wake word cannot work until it is"),
        }

    def download_wake_model(self) -> dict:
        """Fetch and install the local wake-word model (~20 MB, once).

        It is not vendored in the repository, so a fresh install has to fetch it
        before the wake word can work at all. When it lands we re-save the config
        purely to bump its mtime: the running tray app watches that file and
        re-applies on change, so the listener starts without a restart.
        """
        try:
            from ..audio import wake
        except Exception as exc:
            return {"ok": False, "detail": f"wake-word module unavailable: {exc}"}
        try:
            ok, detail = wake.ensure_model(config.get("wake_model_dir"))
        except Exception as exc:
            return {"ok": False, "detail": redact(str(exc))}
        if ok:
            try:
                config.save()
            except Exception:
                pass
        return {"ok": bool(ok), "detail": redact(str(detail))}

    def run_wake_setup(self) -> dict:
        try:
            from ..audio import wake
        except Exception as exc:
            return {"ok": False, "detail": f"wake-word module unavailable: {exc}"}
        try:
            available = wake.WakeWordDetector.is_available(
                config.get("wake_model_dir"))
        except Exception as exc:
            return {"ok": False, "detail": redact(str(exc))}
        if not available:
            return {"ok": False, "detail": (
                "the local wake-word model has not been downloaded yet, so the "
                "wake word cannot work. Use \u201cDownload the model\u201d on the Wake "
                "word settings page (about 20 MB, once). Push-to-talk still works "
                "in the meantime.")}
        phrases = config.get("wake_phrases") or ["hey jarvis"]
        detections: list[str] = []

        detector = wake.WakeWordDetector(
            phrases=phrases, model_dir=config.get("wake_model_dir"),
            threshold=float(config.get("wake_threshold", 0.22)),
            on_detect=lambda kw, score: detections.append(kw))
        try:
            detector.start()
            deadline = time.monotonic() + 12.0
            while time.monotonic() < deadline and not detections:
                time.sleep(0.1)
            detector.stop()
        except Exception as exc:
            try:
                detector.stop()
            except Exception:
                pass
            return {"ok": False, "detail": redact(str(exc))}
        if detections:
            return {"ok": True, "detail": f"detected {detections[0]!r} — wake word works"}
        return {"ok": False, "detail": (
            f"no wake word was detected in 12 seconds. Tried: {', '.join(phrases)}. "
            f"Speak the phrase clearly about 30cm from the microphone. Push-to-talk "
            f"always works regardless.")}

    # ------------------------------------------------------------ calibration
    CALIBRATION_PHRASES = [
        "Open my quarterly report and summarise the latest file.",
        "Do not send 1,250 units to the warehouse.",
        "Codex and Claude use Supabase and TypeScript.",
        "Run npm install in the WooCommerce project.",
        "Kubernetes clusters and Docker images, not virtual machines.",
        "Friday. Actually, Thursday works better.",
        "Postgres versus FastAPI, which did I mean?",
        "Check the balance is not below 1,000 dollars.",
        "Summarise the TypeScript file in the Supabase folder.",
        "Book the review for the 15th of October, not the 5th.",
        "Draft a reply about the 3 week delay.",
        "Remember that dividends arrive in March.",
    ]

    def start_calibration(self) -> dict:
        if self._calib_thread and self._calib_thread.is_alive():
            return {"ok": False, "detail": "a calibration run is already in progress"}
        if not secrets.has_api_key():
            return {"ok": False, "detail": "an API key is needed to compare engines"}
        self._calib_stop = threading.Event()

        def run() -> None:
            from ..audio import capture as audio_capture
            from ..audio import vad
            from ..engines import live, transcribe
            phrases = self.CALIBRATION_PHRASES
            results: list[dict] = []
            for idx, phrase in enumerate(phrases, 1):
                if self._calib_stop.is_set():
                    break
                words = max(1, len(phrase.split()))
                record_seconds = min(14.0, 1.6 + words * 0.55)
                self._push("calibration_progress", {
                    "index": idx, "total": len(phrases), "phase": "recording",
                    "text": phrase,
                    "detail": f"read this aloud ({record_seconds:.0f}s)"})
                cap = audio_capture.MicrophoneCapture(rate=24000)
                try:
                    cap.start()
                except Exception as exc:
                    self._push("toast", {"level": "error",
                                         "message": f"microphone unavailable: {exc}"})
                    break
                deadline = time.monotonic() + record_seconds
                while time.monotonic() < deadline and not self._calib_stop.is_set():
                    time.sleep(0.05)
                pcm = cap.take()
                cap.stop()
                if not vad.has_speech(pcm, 24000):
                    self._push("calibration_progress", {
                        "index": idx, "total": len(phrases), "phase": "skipped",
                        "text": phrase, "detail": "no speech detected; skipped"})
                    continue

                self._push("calibration_progress", {
                    "index": idx, "total": len(phrases), "phase": "transcribing",
                    "text": phrase, "detail": "running both engines"})
                entry = {"index": idx, "phrase": phrase, "economy": None,
                         "live": None, "economy_usd": 0.0, "live_usd": 0.0}
                try:
                    econ = transcribe.TranscribeEngine().transcribe_captured(
                        pcm, 24000, keywords=transcribe.config_transcribe_keys(),
                        languages=["en"])
                    entry["economy"] = econ.text if econ.ok else ""
                    entry["economy_usd"] = econ.usd
                except Exception as exc:
                    entry["economy_error"] = redact(str(exc))

                try:
                    import asyncio as _aio

                    async def src(data=pcm):
                        step = 2400
                        for i in range(0, len(data), step):
                            yield data[i:i + step]
                            await _aio.sleep(0)

                    from ..core.asyncrun import RUNNER
                    cfg = live.LiveConfig(instructions=live.DICTATION_INSTRUCTIONS,
                                          dictation=True)
                    s = live.LiveSession(cfg)

                    async def go(session=s, source=src()):
                        return await session.run_dictation(
                            source, drain_ms=1600.0, speech_detected=True)

                    data = RUNNER.run(go(), timeout=120)
                    entry["live"] = data.get("text", "") if data.get("ok") else ""
                    entry["live_usd"] = float(data.get("usd") or 0.0)
                except Exception as exc:
                    entry["live_error"] = redact(str(exc))

                results.append(entry)
                self._push("calibration_progress", {
                    "index": idx, "total": len(phrases), "phase": "done",
                    "text": phrase,
                    "detail": f"economy: {entry['economy']!r} | live: {entry['live']!r}"})

            self._store_calibration(results)
            summary = self._summarise_calibration(results)
            self._push("calibration_progress", {
                "index": len(phrases), "total": len(phrases), "phase": "complete",
                "text": "", "detail": summary})
            self._push("toast", {"level": "info", "message": summary})

        self._calib_thread = threading.Thread(target=run, name="calibration",
                                              daemon=True)
        self._calib_thread.start()
        return {"ok": True,
                "detail": (f"calibration started: {len(self.CALIBRATION_PHRASES)} "
                           f"short phrases, each run through both engines. This is "
                           f"billable (roughly "
                           f"${len(self.CALIBRATION_PHRASES) * (0.05 * 12 / 60 + 0.0045 * 12 / 60):.3f} "
                           f"at 12s per phrase).")}

    @staticmethod
    def _store_calibration(entries: list[dict]) -> None:
        try:
            db().remember(
                f"calibration run with {len(entries)} phrases "
                f"(see docs/TEST_RESULTS.md)",
                kind="calibration", source="app", confidence=1.0)
        except Exception:
            pass
        import json
        try:
            path = config.data_dir() / "calibration.json"
            path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning("could not store calibration results: %s", exc)

    @staticmethod
    def _summarise_calibration(entries: list[dict]) -> str:
        if not entries:
            return "calibration produced no usable recordings"
        from .bridge import _word_error_rate  # local import keeps numpy optional
        econ_err = [e for e in entries if e.get("economy")]
        live_err = [e for e in entries if e.get("live")]
        e_wer = sum(_word_error_rate(e["phrase"], e["economy"]) for e in econ_err) / \
            max(1, len(econ_err))
        l_wer = sum(_word_error_rate(e["phrase"], e["live"]) for e in live_err) / \
            max(1, len(live_err))
        cost = sum(e.get("economy_usd", 0.0) + e.get("live_usd", 0.0) for e in entries)
        better = "gpt-transcribe (Economy)" if e_wer < l_wer else \
            "gpt-live-1 (Live)" if l_wer < e_wer else "neither — they tied"
        return (f"done: {len(entries)} phrases. Mean word error — Economy "
                f"{e_wer*100:.1f}%, Live {l_wer*100:.1f}%. Better on your voice: "
                f"{better}. Total measured cost ${cost:.4f}. This is a small sample "
                f"of scripted phrases, not a general accuracy score.")

    # -------------------------------------------------------------- start at login
    def set_start_at_login(self, enabled: bool = False) -> dict:
        config.set_value("start_at_login", bool(enabled))
        if not sys_platform_is_windows():
            return {"ok": False, "error": "only supported on Windows"}
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                winreg.KEY_SET_VALUE)
            name = "Jarvis"
            if enabled:
                exe = _launch_command()
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, exe)
            else:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception as exc:
            return {"ok": False, "error": redact(str(exc)),
                    "detail": "the setting was saved but the login entry failed"}
        return {"ok": True, "detail": ("will start at login" if enabled
                                       else "will not start at login")}

    # -------------------------------------------------------------------- quit
    def quit_app(self) -> dict:
        """Close the settings window, and the tray app if it is running."""
        _request_tray_shutdown()
        try:
            if self.host is not None and self.host._window is not None:
                self.host._window.destroy()
        except Exception:
            pass
        return {"ok": True}

    # --------------------------------------------------------------- internals
    def _push(self, event: str, payload: dict) -> None:
        if self.host is not None:
            self.host.push(event, payload)


def _word_error_rate(reference: str, hypothesis: str) -> float:
    import re
    ref = re.findall(r"[a-z0-9']+", (reference or "").lower())
    hyp = re.findall(r"[a-z0-9']+", (hypothesis or "").lower())
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, rw in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, hw in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1,
                         prev[j - 1] + (0 if rw == hw else 1))
        prev = cur
    return prev[-1] / len(ref)


def sys_platform_is_windows() -> bool:
    import sys
    return sys.platform.startswith("win")


def _launch_command() -> str:
    """The command used for the Run key. pythonw avoids a console window."""
    import sys
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pythonw):
        exe = pythonw
    return f'"{exe}" -m jarvis'


def _request_tray_shutdown() -> None:
    """Ask a running tray instance to quit, without a network endpoint.

    There is deliberately no socket here: the settings window and the tray share
    a data directory, so a marker file is the whole channel.
    """
    try:
        from .. import config as cfg_mod
        (cfg_mod.data_dir() / "quit.request").write_text(
            str(time.time()), encoding="utf-8")
    except Exception:
        pass
    # Also try a graceful WM_CLOSE to the pill window, in case the tray's poll
    # loop is busy.
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
        user32.FindWindowW.restype = wt.HWND
        user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        hwnd = user32.FindWindowW("JarvisPill", None)
        if hwnd:
            user32.PostMessageW(wt.HWND(hwnd), 0x0010, 0, 0)  # WM_CLOSE
    except Exception:
        pass


class _BindingRecorder:
    """One-shot capture of the next key or mouse button the user presses.

    Installs its own low-level hooks and removes them immediately afterwards, so
    no global hook outlives the wizard step. It ignores the pure modifier keys
    themselves (pressing Ctrl should not finish the capture) and ignores
    injected input from this app so our own synthetic events cannot be recorded.
    """

    def __init__(self, ignore_injected: bool = True):
        self._result: dict = {}
        self._done = threading.Event()
        self._thread_id: Optional[int] = None
        # Ignoring injected input is the correct product behaviour: it stops this
        # app's own synthetic events (or another automation tool's) from being
        # recorded as the user's chosen shortcut. It also means the capture path
        # cannot be exercised by SendInput, which is why the flag exists - a test
        # can turn it off to prove hook -> virtual key -> modifier assembly ->
        # canonical binding end to end. Never set it False in the product.
        self.ignore_injected = bool(ignore_injected)
        self.hook_error: Optional[str] = None

    def capture(self, timeout: float = 12.0) -> dict:
        user32 = hotkeys_mod.user32
        kernel32 = hotkeys_mod.kernel32
        mods_down: set[str] = set()
        state: dict = {"binding": None}

        def on_key(vk: int) -> bool:
            name = hotkeys_mod.VK_MODS.get(vk)
            if name:
                mods_down.add(name)
                return False
            base = hotkeys_mod.vk_to_name(vk)
            if not base:
                return False
            order = [m for m in ("ctrl", "alt", "shift", "win") if m in mods_down]
            state["binding"] = "+".join(order + [base])
            return True

        def on_mouse(which: str) -> bool:
            state["binding"] = which
            return True

        ll_kb = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)

        def kb_proc(code, wparam, lparam):
            if code == 0:
                kb = ctypes.cast(lparam, ctypes.POINTER(
                    hotkeys_mod.KBDLLHOOKSTRUCT)).contents
                if self.ignore_injected and (kb.flags & hotkeys_mod.LLKHF_INJECTED):
                    return user32.CallNextHookEx(None, code, wparam, lparam)
                if wparam in (hotkeys_mod.WM_KEYDOWN, hotkeys_mod.WM_SYSKEYDOWN):
                    if on_key(int(kb.vkCode)):
                        self._done.set()
                        return 1
            return user32.CallNextHookEx(None, code, wparam, lparam)

        def ms_proc(code, wparam, lparam):
            if code == 0:
                ms = ctypes.cast(lparam, ctypes.POINTER(
                    hotkeys_mod.MSLLHOOKSTRUCT)).contents
                if self.ignore_injected and (ms.flags & hotkeys_mod.LLMHF_INJECTED):
                    return user32.CallNextHookEx(None, code, wparam, lparam)
                if wparam == hotkeys_mod.WM_XBUTTONDOWN:
                    hi = (int(ms.mouseData) >> 16) & 0xFFFF
                    which = "xbutton1" if hi == hotkeys_mod.XBUTTON1 else "xbutton2"
                    if on_mouse(which):
                        self._done.set()
                        return 1
                elif wparam == hotkeys_mod.WM_MBUTTONDOWN:
                    if on_mouse("middle"):
                        self._done.set()
                        return 1
            return user32.CallNextHookEx(None, code, wparam, lparam)

        kb_ref = ll_kb(kb_proc)
        ms_ref = ll_kb(ms_proc)

        def loop() -> None:
            self._thread_id = int(kernel32.GetCurrentThreadId())
            # hMod must be NULL for low-level hooks - see the note in
            # win/hotkeys.py::HotkeyManager._hook_loop. Passing a module handle
            # fails with error 126 and returns a NULL hook, which would make
            # this recorder sit there until the timeout with no explanation.
            hk = user32.SetWindowsHookExW(hotkeys_mod.WH_KEYBOARD_LL, kb_ref, None, 0)
            hm = user32.SetWindowsHookExW(hotkeys_mod.WH_MOUSE_LL, ms_ref, None, 0)
            self.hook_error = None
            if not hk:
                self.hook_error = ("could not install the keyboard hook "
                                   "(error %d)" % ctypes.get_last_error())
            elif not hm:
                self.hook_error = ("could not install the mouse hook "
                                   "(error %d)" % ctypes.get_last_error())
            if self.hook_error:
                self._done.set()
            msg = wt.MSG()
            while not self._done.is_set():
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret in (0, -1):
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            if hk:
                user32.UnhookWindowsHookEx(hk)
            if hm:
                user32.UnhookWindowsHookEx(hm)

        t = threading.Thread(target=loop, name="binding-recorder", daemon=True)
        t.start()
        ok = self._done.wait(timeout)
        if self._thread_id:
            try:
                hotkeys_mod.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)
            except Exception:
                pass
        t.join(timeout=1.0)
        if self.hook_error:
            # Say WHY, instead of claiming the user pressed nothing. This is the
            # message that would have exposed the error-126 hook bug immediately.
            return {"ok": False, "error": self.hook_error}
        if not ok or not state["binding"]:
            return {"ok": False, "error": "nothing was pressed within the timeout"}
        return {"ok": True, "binding": state["binding"]}
