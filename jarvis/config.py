"""Persistent configuration for Jarvis.

Stored as JSON under %LOCALAPPDATA%\\Jarvis\\config.json.
Secrets are NEVER stored here; see jarvis/secrets.py.

All defaults are deliberate product decisions that mirror the build spec:
Live-first, brief replies, Australian English vocabulary, conservative budgets.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Optional

# On-disk identity of the per-user data folder. This is deliberately NOT the
# display name: it is the folder under %LOCALAPPDATA% that holds config.json,
# the SQLite database and the logs.
APP_DIR_NAME = "Jarvis"

# Data-directory environment override. The current name is preferred; the
# historical ``BV_DATA_DIR`` is still accepted as a deprecated fallback because
# the test suite and docs were written against it.
ENV_DATA_DIR = "JARVIS_DATA_DIR"
ENV_DATA_DIR_LEGACY = "BV_DATA_DIR"

# Database filename inside the data dir.
DB_FILENAME = "jarvis.db"

# ---------------------------------------------------------------- paths


def _local_appdata_base() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"))


def data_dir() -> Path:
    """Per-user writable application data directory.

    ``JARVIS_DATA_DIR`` overrides it (the deprecated ``BV_DATA_DIR`` still
    works) so tests and the smoke test can run against a throwaway directory
    and never touch the real profile's config or database.
    """
    override: Optional[str] = (os.environ.get(ENV_DATA_DIR)
                               or os.environ.get(ENV_DATA_DIR_LEGACY))
    if override:
        p = Path(override)
    else:
        p = _local_appdata_base() / APP_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def project_dir() -> Path:
    """Directory of the installed/running application (read-only assets)."""
    return Path(__file__).resolve().parent.parent


def log_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def audio_dir() -> Path:
    """Scratch space for short-lived dictation audio. Purged on start."""
    p = data_dir() / "audio"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "jarvis.db"


def config_path() -> Path:
    return data_dir() / "config.json"


# ---------------------------------------------------------------- schema

# Australian English starter vocabulary, per spec section 6.
# Edit this in Settings > Vocabulary (it seeds the dictation keyword hints).
DEFAULT_VOCABULARY: list[str] = [
    "Codex",
    "Claude",
    "Supabase",
    "TypeScript",
    "npm",
    "WooCommerce",
    "Zoho",
    "Kubernetes",
    "Postgres",
    "FastAPI",
    "Docker",
    "GraphQL",
]

DEFAULT_CONFIG: dict[str, Any] = {
    "schema": 1,
    "setup_complete": False,

    # ---- product mode -------------------------------------------------
    # "live"     -> gpt-live-1 Live session for dictation (default preset)
    # "economy"  -> record locally, then gpt-transcribe file/committed path
    "dictation_engine": "live",
    "assistant_model": "gpt-live-1",

    # Optional post-processing styles: verbatim | light | polish
    "cleanup_style": "verbatim",
    "cleanup_enabled": False,
    # Verified cheap text model (see docs/VERIFIED_API.md).
    "cleanup_model": "gpt-5.6-luna",

    # ---- audio --------------------------------------------------------
    "input_device": None,          # sounddevice index or None = system default
    "output_device": None,
    "input_gain": 1.0,
    "vad_silence_ms": 900,         # hands-free auto-stop
    "max_utterance_seconds": 180,  # hard stop; prevents runaway sessions
    "drain_ms": 1600,              # bounded finalisation wait (Live transcripts)

    # ---- shortcuts ----------------------------------------------------
    # Bindings are stored canonically; the wizard records them.
    "bindings": {
        # mouse: one of "xbutton1" / "xbutton2" / "middle" / "none"
        "mouse_dictate": "xbutton2",
        "mouse_dictate_hold": True,
        "key_dictate_toggle": "f8",
        "key_assistant": "ctrl+alt+space",
        "key_emergency_stop": "ctrl+alt+pause",
        "key_cancel": "esc",
    },
    "esc_cancels_when_idle": False,  # never swallow Esc globally

    # ---- wake word ----------------------------------------------------
    "wake_enabled": False,
    "wake_provider": "sherpa-onnx",
    "wake_phrases": ["hey jarvis", "hey gpt"],
    "wake_threshold": 0.22,
    "wake_model_dir": None,        # resolved at runtime if None
    "push_to_talk_only": False,    # works with wake word unfinished

    # The assistant's spoken voice. Must be a documented id from
    # engines.live.VOICES (or a custom voice approved on the user's own account).
    # The Live docs are explicit that this CANNOT change on a running session,
    # so a change here applies to the next one.
    "voice": "marin",

    # ---- spoken commands ----------------------------------------------
    # Recognise "open <app>", "type <text>", "search for <query>" and friends
    # in a DICTATED utterance and run them locally instead of typing them out.
    # See core/commands.py; every command still goes through core/tools.py, so
    # approval and the hard stop apply exactly as they do to the assistant.
    "voice_commands_enabled": True,
    "voice_command_confirm": True,   # show the match in the pill before acting

    # ---- history ------------------------------------------------------
    "history_limit": 500,            # rows kept for the History tab

    # ---- assistant / tools --------------------------------------------
    "assistant_mode": "client",    # client delegation (app owns tools)
    "backend_model": "gpt-5.6-luna",
    "backend_model_escalated": "gpt-5.6-terra",
    "allow_escalation": True,
    "tools_enabled": True,
    "approved_paths": [],          # user-approved directories
    "approved_apps": {},           # friendly name -> executable path
    "approved_urls": ["https://chatgpt.com", "https://github.com"],
    "screenshot_monitor": 1,

    # ---- budgets ------------------------------------------------------
    "daily_budget_usd": 3.00,
    "monthly_budget_usd": 30.00,
    "test_budget_usd": 0.50,
    "warn_at_ratio": 0.8,
    "max_task_seconds": 600,
    "max_task_tool_calls": 40,

    # ---- session idle behaviour ---------------------------------------
    "idle_timeout_seconds": 45,    # close a billed Live session when idle
    "keep_listening_override": False,

    # ---- privacy / retention ------------------------------------------
    "store_raw_audio": False,
    "retain_audio_files": False,
    "telemetry": False,
    "contextual_dictation": False,  # opt-in: app name + selected text as hints
    "conversation_retention_days": 30,

    # ---- ui -----------------------------------------------------------
    "theme": "dark",               # dark | light
    "reduced_motion": False,
    "start_at_login": False,
    "overlay_position": "top-center",
    "overlay_offset_y": 12,

    # ---- integrations -------------------------------------------------
    "codex": {"enabled": True, "billing_mode": "subscription", "workdir": None},
    "claude": {"enabled": True, "workdir": None},
}

_LOCK = threading.RLock()
_cache: dict[str, Any] | None = None


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict[str, Any]:
    """Load config, merging over defaults so new keys always exist."""
    global _cache
    with _LOCK:
        if _cache is not None:
            return _cache
        raw: dict[str, Any] = {}
        p = config_path()
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("config must be a JSON object")
            except Exception:
                # Corrupt config: keep a copy for forensics, fall back to defaults.
                try:
                    p.replace(p.with_suffix(".json.corrupt"))
                except Exception:
                    pass
                raw = {}
        _cache = _deep_merge(DEFAULT_CONFIG, raw)
        return _cache


def reload() -> dict[str, Any]:
    """Drop the cache and re-read config.json from disk.

    THIS IS LOAD-BEARING, not a convenience. The settings UI runs in a SEPARATE
    PROCESS (see app.py: no localhost IPC by design), so the only way the tray
    process can learn about a settings change is by re-reading the file.
    `load()` caches on purpose — `get()` is called on hot paths such as the
    dictation drain loop — so the cache has to be dropped explicitly.

    Without this, `app._watch_config` noticed the changed mtime, called `load()`,
    got the stale cache back and "reapplied" the old values.
    """
    global _cache
    with _LOCK:                      # RLock: load() re-enters it
        _cache = None
        return load()


def save(cfg: dict[str, Any] | None = None) -> None:
    """Atomically persist config."""
    global _cache
    with _LOCK:
        if cfg is not None:
            _cache = _deep_merge(DEFAULT_CONFIG, cfg)
        assert _cache is not None
        p = config_path()
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_cache, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default)


def set_value(key: str, value: Any) -> dict[str, Any]:
    # reload() first: two processes write this file (tray + settings window), so
    # merging over a stale cache would silently revert the other one's keys.
    with _LOCK:
        cfg = reload()
        cfg[key] = value
        save(cfg)
        return cfg


def update(patch: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        cfg = _deep_merge(reload(), patch)
        save(cfg)
        return cfg


def frozen_dataclass_dict() -> dict[str, Any]:
    return copy.deepcopy(load())


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle (asset paths differ)."""
    return bool(getattr(sys, "frozen", False))
