"""Offline, NON-BILLABLE contract suite for Jarvis core logic.

Covers the parts of the app that need no network and no API key:
  config, secrets, logging redaction, SQLite storage + usage accounting,
  pricing, budget/cost accounting, error mapping, the cleanup token guard,
  VAD, resampling, and transcript assembly / finalisation decisions.

Runnable two ways:
    python tests/test_core_units.py          # flat runner, prints PASS/FAIL, exit code
    pytest tests/test_core_units.py          # if pytest is available (no extra deps)

Nothing here touches the network, the OpenAI API, the user's real Credential
Manager entry, or the user's real %LOCALAPPDATA%\\Jarvis data. App data is
redirected to a throwaway temp directory and secrets use a test-only credential
target name.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# --------------------------------------------------------------------------
# Redirect ALL app data to a throwaway directory BEFORE importing jarvis.
# config.data_dir() reads LOCALAPPDATA on every call, so this is enough.
# --------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests._console import use_utf8_stdout  # noqa: E402

use_utf8_stdout()

_TMP_ROOT = Path(tempfile.mkdtemp(prefix="jarvis_offline_tests_"))
os.environ["LOCALAPPDATA"] = str(_TMP_ROOT / "appdata")
os.environ.setdefault("JARVIS_OFFLINE_TESTS", "1")

import numpy as np  # noqa: E402

from jarvis import config, logsetup, secrets  # noqa: E402
from jarvis import db as dbmod  # noqa: E402
from jarvis.db import Database  # noqa: E402
from jarvis.core import pricing, cost  # noqa: E402
from jarvis.core.asyncrun import AsyncRunner  # noqa: E402
from jarvis.engines import cleanup, errors, live  # noqa: E402
from jarvis.audio import resample, vad  # noqa: E402

# A credential target that can never collide with the real key.
TEST_CRED_TARGET = "Jarvis/__offline_test__/openai_api_key"
FAKE_KEY = "sk-test-0123456789abcdefghijklmnop"
FAKE_KEY_2 = "sk-test-zyxwvutsrqponmlkjihgfedcba"

_counter = {"n": 0}


# --------------------------------------------------------------------------
# tiny harness helpers
# --------------------------------------------------------------------------
def check(cond, msg=""):
    """Raise AssertionError unless cond is truthy. Works under pytest and flat."""
    if not cond:
        raise AssertionError(msg or "check failed")


def close_to(a, b, tol=1e-9, msg=""):
    delta = abs(float(a) - float(b))
    if delta > tol:
        raise AssertionError(msg or f"expected {b!r}, got {a!r} (tolerance {tol})")
    return True


def fresh_data_dir() -> Path:
    """Point the app at a brand new data dir and drop every cached singleton.

    Returns the directory the app actually uses (:func:`config.data_dir`), which
    is ``<LOCALAPPDATA>/Jarvis``.
    """
    _counter["n"] += 1
    base = _TMP_ROOT / f"data{_counter['n']}"
    base.mkdir(parents=True, exist_ok=True)
    os.environ["LOCALAPPDATA"] = str(base)
    config._cache = None
    dbmod._DB = None
    return config.data_dir()


def fresh_db() -> Database:
    """A Database on a fresh file (independent of the db() singleton)."""
    d = fresh_data_dir()
    return Database(path=d / "unit.db")


# ==========================================================================
# 1. config
# ==========================================================================
def test_config_defaults_exist():
    fresh_data_dir()
    cfg = config.load()
    for key in ("schema", "setup_complete", "dictation_engine", "assistant_model",
                "cleanup_style", "vad_silence_ms", "max_utterance_seconds", "drain_ms",
                "bindings", "daily_budget_usd", "monthly_budget_usd", "warn_at_ratio",
                "conversation_retention_days", "theme", "codex", "claude"):
        check(key in cfg, f"DEFAULT_CONFIG is missing the key {key!r}")
    check(cfg["schema"] == 1, f"schema default is {cfg['schema']!r}")
    check(cfg["dictation_engine"] == "live", "default dictation engine must be 'live'")
    check(isinstance(cfg["bindings"], dict) and "key_dictate_toggle" in cfg["bindings"],
          "default bindings incomplete")
    check(close_to(cfg["daily_budget_usd"], 3.00), "daily budget default changed")
    check(close_to(cfg["warn_at_ratio"], 0.8), "warn ratio default changed")
    check(isinstance(config.DEFAULT_VOCABULARY, list) and config.DEFAULT_VOCABULARY,
          "DEFAULT_VOCABULARY must be a non-empty list")
    check("WooCommerce" in config.DEFAULT_VOCABULARY, "starter vocabulary changed")
    check(config.data_dir().exists(), "data_dir() must create the directory")
    check(config.config_path().parent == config.data_dir(), "config path is not in data_dir")
    check(config.log_dir().exists() and config.audio_dir().exists(), "log/audio dirs missing")


def test_config_partial_file_merges_over_defaults():
    d = fresh_data_dir()
    (d / "config.json").write_text(json.dumps({"theme": "light"}), encoding="utf-8")
    config._cache = None
    cfg = config.load()
    check(cfg["theme"] == "light", "the saved value was lost")
    # new keys must appear from defaults
    check(cfg["daily_budget_usd"] == config.DEFAULT_CONFIG["daily_budget_usd"],
          "a partial config did not pick up new default keys")
    check(cfg["dictation_engine"] == "live", "missing default key")

    # nested dict merge: a partially saved 'bindings' must keep its siblings
    (d / "config.json").write_text(
        json.dumps({"bindings": {"key_dictate_toggle": "f9"}}), encoding="utf-8")
    config._cache = None
    cfg = config.load()
    check(cfg["bindings"]["key_dictate_toggle"] == "f9", "saved nested value lost")
    check(cfg["bindings"]["key_assistant"] == "ctrl+alt+space",
          "nested default was dropped by the merge")


def test_config_set_value_update_roundtrip():
    d = fresh_data_dir()
    config.load()
    config.set_value("theme", "light")
    check(config.get("theme") == "light", "set_value did not take effect")
    config.update({"cleanup_style": "polish", "bindings": {"key_cancel": "esc"}})
    check(config.get("cleanup_style") == "polish", "update did not take effect")
    check(config.get("bindings")["key_cancel"] == "esc", "nested update lost")
    check(config.get("bindings")["key_dictate_toggle"] == "f8",
          "update clobbered an untouched nested key")
    check(config.get("does_not_exist", "fallback") == "fallback", "get() default broken")

    # persisted to disk, not just cached
    config._cache = None
    reloaded = config.load()
    check(reloaded["theme"] == "light", "set_value was not persisted")
    check(reloaded["cleanup_style"] == "polish", "update was not persisted")
    on_disk = json.loads((d / "config.json").read_text(encoding="utf-8"))
    check(on_disk["theme"] == "light", "config.json on disk disagrees with memory")
    check(not (d / "config.json.tmp").exists(), "atomic write left a .tmp file behind")


def test_config_corrupt_file_is_preserved_and_falls_back():
    d = fresh_data_dir()
    bad = d / "config.json"
    garbage = "{ this is not valid json at all "
    bad.write_text(garbage, encoding="utf-8")
    config._cache = None
    cfg = config.load()  # must not raise
    check(cfg["theme"] == config.DEFAULT_CONFIG["theme"], "corrupt config did not fall back")
    check(cfg["daily_budget_usd"] == config.DEFAULT_CONFIG["daily_budget_usd"],
          "fallback config is not the defaults")
    corrupt = d / "config.json.corrupt"
    check(corrupt.exists(), "the corrupt config was not preserved for forensics")
    check(corrupt.read_text(encoding="utf-8") == garbage,
          "the preserved corrupt copy has the wrong contents")
    # a following save must write a clean file
    config.set_value("theme", "dark")
    check(json.loads(bad.read_text(encoding="utf-8"))["theme"] == "dark",
          "save after a corrupt load did not produce valid JSON")


# ==========================================================================
# 1b. environment overrides + platform reporting
# ==========================================================================
def test_data_dir_env_override_and_legacy_fallback():
    """JARVIS_DATA_DIR wins; the deprecated BV_DATA_DIR still works."""
    base = _TMP_ROOT / "envdata"
    base.mkdir(parents=True, exist_ok=True)
    saved = {k: os.environ.get(k) for k in
             ("JARVIS_DATA_DIR", "BV_DATA_DIR", "LOCALAPPDATA")}
    try:
        os.environ.pop("JARVIS_DATA_DIR", None)
        os.environ.pop("BV_DATA_DIR", None)
        os.environ["LOCALAPPDATA"] = str(base)
        want = base / config.APP_DIR_NAME
        check(config.data_dir() == want,
              "the default data dir must be LOCALAPPDATA/Jarvis")
        check(want.exists(), "data_dir() must create the default directory")

        legacy = base / "legacy-override"
        os.environ["BV_DATA_DIR"] = str(legacy)
        check(config.data_dir() == legacy,
              "the deprecated BV_DATA_DIR override must still be honoured")

        brand_new = base / "new-override"
        os.environ["JARVIS_DATA_DIR"] = str(brand_new)
        check(config.data_dir() == brand_new,
              "JARVIS_DATA_DIR must take precedence over BV_DATA_DIR")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_platform_support_reports_honestly():
    from jarvis import platform_support as ps

    check(ps.is_supported("win32") is True, "win32 must be reported as supported")
    for other in ("darwin", "linux", "freebsd"):
        check(ps.is_supported(other) is False,
              f"{other!r} must be reported as unsupported")
        check(bool(ps.support_reason(other)), "an unsupported platform needs a reason")
    check(ps.is_supported() == (sys.platform in ps.SUPPORTED_PLATFORMS),
          "is_supported() must reflect the running sys.platform")

    msg = ps.unsupported_message("linux")
    check("Linux" in msg, "the message must name the unsupported platform")
    check("Windows" in msg, "the message must name the supported platform")
    check("INSTALL.md" in msg, "the message must point at the README/INSTALL roadmap")


# ==========================================================================
# 2. secrets
# ==========================================================================
class _CredTarget:
    """Use a test-only Credential Manager target; never touch the real key."""

    def __enter__(self):
        self._orig = secrets.TARGET_NAME
        secrets.TARGET_NAME = TEST_CRED_TARGET
        secrets._cached = None
        try:
            secrets.clear_api_key()
        except Exception:
            pass
        return self

    def __exit__(self, *exc):
        # delete the TEST target BEFORE restoring the real name
        try:
            secrets.clear_api_key()
        except Exception:
            pass
        secrets.TARGET_NAME = self._orig
        secrets._cached = None
        return False


def test_secrets_fake_key_roundtrip():
    fresh_data_dir()
    with _CredTarget():
        check(secrets.get_api_key() is None, "expected no secret to be stored yet")
        secrets.set_api_key(FAKE_KEY)
        check(secrets.get_api_key() == FAKE_KEY, "set/get round-trip failed")
        check(secrets.has_api_key() is True, "has_api_key() is False after a set")

        fp = secrets.key_fingerprint()
        check(fp is not None, "key_fingerprint() returned None with a key stored")
        check(FAKE_KEY not in fp, "key_fingerprint leaked the key itself")
        check(fp.startswith("sk-..."), f"unexpected fingerprint format {fp!r}")
        check(len(fp) == len("sk-...") + 8, f"fingerprint should be 8 hash chars, got {fp!r}")

        secrets.clear_api_key()
        check(secrets.get_api_key() is None, "clear_api_key did not remove the key")
        check(secrets.has_api_key() is False, "has_api_key() true after clear")
        check(secrets.key_fingerprint() is None, "fingerprint still present after clear")


def test_secrets_overwrite_and_reject_non_sk():
    fresh_data_dir()
    with _CredTarget():
        secrets.set_api_key(FAKE_KEY)
        secrets.set_api_key(FAKE_KEY_2)
        check(secrets.get_api_key() == FAKE_KEY_2, "overwrite failed")

        for bad in ("", "   ", "not-a-key", "pk-test-0123456789", "Bearer sk-abc",
                    "SK-TEST-0123456789"):
            raised = False
            try:
                secrets.set_api_key(bad)
            except ValueError:
                raised = True
            check(raised, f"set_api_key({bad!r}) should raise ValueError")
        check(secrets.get_api_key() == FAKE_KEY_2,
              "a rejected key overwrote the previously stored one")


def test_secrets_dpapi_roundtrip():
    fresh_data_dir()
    blob = secrets.dpapi_protect(b"hello world")
    check(blob != b"hello world", "dpapi_protect returned the plaintext")
    check(secrets.dpapi_unprotect(blob) == b"hello world", "DPAPI round-trip failed")
    # different entropy must not decrypt
    other = secrets.dpapi_protect(b"x", entropy=b"other-entropy")
    failed = False
    try:
        secrets.dpapi_unprotect(other)
    except OSError:
        failed = True
    check(failed, "DPAPI decrypted with the wrong entropy")


# ==========================================================================
# 3. logsetup redaction
# ==========================================================================
def test_redact_scrubs_key_shaped_strings():
    out = logsetup.redact(f"connecting with {FAKE_KEY} now")
    check(FAKE_KEY not in out, f"a bare sk- key was not redacted: {out!r}")
    check(logsetup.REDACTION in out, "no redaction marker was inserted")

    out = logsetup.redact(f"Authorization: Bearer {FAKE_KEY}")
    check(FAKE_KEY not in out, f"an Authorization/Bearer key was not redacted: {out!r}")

    out = logsetup.redact("Authorization: Bearer abcdefghijklmnopqrstuvwxyz")
    check("abcdefghijklmnopqrstuvwxyz" not in out, f"a bearer token was not redacted: {out!r}")

    out = logsetup.redact(f"api_key='{FAKE_KEY}'")
    check(FAKE_KEY not in out, f"an api_key='...' value was not redacted: {out!r}")

    out = logsetup.redact('{"api-key": "' + FAKE_KEY + '"}')
    check(FAKE_KEY not in out, f"a JSON api-key value was not redacted: {out!r}")

    out = logsetup.redact(f"client secret ek_{'abcd' * 6}")
    check("ek_abcdabcdabcdabcdabcdabcd" not in out, f"an ek_ secret was not redacted: {out!r}")


def test_redact_leaves_plain_text_alone():
    for text in ("The quick brown fox jumps over the lazy dog.",
                 "transcript: hello world, this is a normal sentence.",
                 "",
                 "price is 3 dollars and 50 cents"):
        check(logsetup.redact(text) == text, f"redact() altered plain text: {text!r}")


def test_redaction_filter_scrubs_log_records():
    import logging
    filt = logsetup.RedactionFilter()
    rec = logging.LogRecord("jarvis.test", logging.WARNING, __file__, 1,
                            "key is %s", (FAKE_KEY,), None)
    check(filt.filter(rec) is True, "the filter must never drop a record")
    check(FAKE_KEY not in rec.getMessage(), "the log record still contains the key")
    check(logsetup.REDACTION in rec.getMessage(), "no marker in the scrubbed record")


# ==========================================================================
# 4. database: memory / vocab / conversation / tasks / audit
# ==========================================================================
def test_db_singleton_and_schema():
    d = fresh_data_dir()
    a = dbmod.db()
    b = dbmod.db()
    check(a is b, "db() must return a singleton")
    check(isinstance(a, Database), "db() did not return a Database")
    check(a.path.exists(), "the database file was not created")
    check(a.path.parent == d, "the singleton did not use the redirected data dir")


def test_db_memory_roundtrip_search_and_forget():
    db = fresh_db()
    mid = db.remember("the user prefers Australian English", kind="preference")
    check(isinstance(mid, int), "remember() must return an int id")
    rows = db.list_memory()
    check(any(r["text"] == "the user prefers Australian English" for r in rows),
          "remembered fact is not listed")
    got = next(r for r in rows if r["text"] == "the user prefers Australian English")
    check(got["kind"] == "preference", "kind not stored")
    check(got["source"] == "user", "source not stored")
    check(got["approved"] == 1, "approved flag not stored")

    hits = db.search_memory("Australian")
    check(len(hits) == 1 and hits[0]["text"].startswith("the user"), f"search failed: {hits}")
    check(db.search_memory("no-such-substring-xyz") == [], "search returned a false hit")

    # identical (kind, text) upserts instead of duplicating
    db.remember("the user prefers Australian English", kind="preference")
    check(sum(1 for r in db.list_memory() if r["text"] == "the user prefers Australian English") == 1,
          "remember() duplicated an identical memory row")

    db.remember("Temporary fact to delete", kind="fact")
    row = next(r for r in db.list_memory() if r["text"] == "Temporary fact to delete")
    db.forget(row["id"])
    check(not any(r["text"] == "Temporary fact to delete" for r in db.list_memory()),
          "forget() did not delete the row")

    raised = False
    try:
        db.remember("   ")
    except ValueError:
        raised = True
    check(raised, "remember('') should raise ValueError")


def test_db_memory_export_import_roundtrip():
    src = fresh_db()
    src.remember("Export me", kind="fact")
    src.add_vocab("ZohoBooksCustom")
    blob = src.export_memory()
    check(blob.get("version") == 1, "export version missing")
    check(isinstance(blob.get("memory"), list) and isinstance(blob.get("vocab"), list),
          "exported blob shape is wrong")
    check(any(m["text"] == "Export me" for m in blob["memory"]), "export omitted a memory row")

    dst = fresh_db()
    n = dst.import_memory(blob)
    check(n >= 1, f"import_memory reported {n} imported rows")
    imported = next((r for r in dst.list_memory() if r["text"] == "Export me"), None)
    check(imported is not None, "imported memory row missing")
    check(imported["source"] == "import", f"import source not recorded: {imported['source']}")
    check("ZohoBooksCustom" in dst.list_vocab(), "imported vocabulary term missing")


def test_db_vocab_crud():
    db = fresh_db()
    for term in config.DEFAULT_VOCABULARY:
        check(term in db.list_vocab(), f"starter vocabulary term {term!r} not seeded")
    db.add_vocab("ZohoBooks")
    check("ZohoBooks" in db.list_vocab(), "add_vocab failed")
    db.add_vocab("  ZohoBooks  ")
    check(db.list_vocab().count("ZohoBooks") == 1, "add_vocab stored a duplicate")
    db.add_vocab("")
    db.add_vocab("   ")
    check("" not in db.list_vocab(), "add_vocab stored an empty term")
    db.remove_vocab("ZohoBooks")
    check("ZohoBooks" not in db.list_vocab(), "remove_vocab failed")
    db.set_vocab(["alpha", "beta"])
    check(db.list_vocab() == ["alpha", "beta"], f"set_vocab wrong: {db.list_vocab()}")
    db.set_vocab([])
    check(db.list_vocab() == [], "set_vocab([]) did not clear the list")


def test_db_conversation_order_limit_and_clear():
    db = fresh_db()
    for role, text in (("user", "one"), ("assistant", "two"), ("user", "three")):
        db.add_turn(role, text, session_id="s1")
    turns = db.recent_turns(limit=10, session_id="s1")
    check([t["text"] for t in turns] == ["one", "two", "three"],
          f"turns are not in chronological order: {[t['text'] for t in turns]}")
    check([t["role"] for t in turns] == ["user", "assistant", "user"], "roles wrong")

    last_two = db.recent_turns(limit=2, session_id="s1")
    check([t["text"] for t in last_two] == ["two", "three"],
          f"limit should keep the most recent turns: {[t['text'] for t in last_two]}")

    db.add_turn("user", "other session", session_id="s2")
    check([t["text"] for t in db.recent_turns(session_id="s1")] == ["one", "two", "three"],
          "session filtering leaked another session's turns")
    check(len(db.recent_turns(limit=50)) == 4, "cross-session listing wrong")

    db.clear_conversation()
    check(db.recent_turns(limit=50) == [], "clear_conversation left rows behind")


def test_db_usage_snapshot_is_cumulative_not_summed():
    """THE critical accounting rule: (session_id, category) keeps the maximum."""
    db = fresh_db()
    sid = "sess-A"
    # three snapshots of the SAME running session: 10s, 30s, then a stale 20s
    db.record_usage("live_voice", pricing.live_seconds_to_usd(10.0), seconds=10.0, session_id=sid)
    db.record_usage("live_voice", pricing.live_seconds_to_usd(30.0), seconds=30.0, session_id=sid)
    db.record_usage("live_voice", pricing.live_seconds_to_usd(20.0), seconds=20.0, session_id=sid)

    rows = [r for r in db.usage_events(100) if r["session_id"] == sid]
    check(len(rows) == 1, f"expected exactly ONE snapshot row, got {len(rows)}")
    check(close_to(rows[0]["seconds"], 30.0),
          f"stored {rows[0]['seconds']}s, expected the maximum 30s "
          f"(a sum would be 60s, a stale last-write would be 20s)")
    check(close_to(rows[0]["usd"], pricing.live_seconds_to_usd(30.0)),
          f"stored usd {rows[0]['usd']} is not the maximum snapshot")

    today = db.usage_today()
    check(close_to(today["live_seconds"], 30.0, 1e-6),
          f"usage_today summed snapshots: {today['live_seconds']}s, expected 30s")
    check(close_to(today["total_usd"], pricing.live_seconds_to_usd(30.0), 1e-9),
          "usage_today total is wrong")

    # a later, smaller snapshot must not shrink the stored maximum
    db.record_usage("live_voice", pricing.live_seconds_to_usd(5.0), seconds=5.0, session_id=sid)
    rows = [r for r in db.usage_events(100) if r["session_id"] == sid]
    check(len(rows) == 1, "a later snapshot created a second row")
    check(close_to(rows[0]["seconds"], 30.0), "a smaller snapshot shrank the stored maximum")

    # the same session with a DIFFERENT category is an independent row
    db.record_usage("backend", 0.0004, session_id=sid)
    check(len([r for r in db.usage_events(100) if r["session_id"] == sid]) == 2,
          "different categories in one session must be separate rows")


def test_db_usage_without_session_appends_and_sums():
    db = fresh_db()
    for secs in (10.0, 30.0, 20.0):
        db.record_usage("transcribe", pricing.transcribe_seconds_to_usd(secs), seconds=secs)
    rows = [r for r in db.usage_events(100) if r["category"] == "transcribe"]
    check(len(rows) == 3, f"incremental events must be appended: got {len(rows)} rows")
    check(all(r["session_id"] is None for r in rows),
          "an event with no session must be stored with a NULL session_id")

    today = db.usage_today()
    check(close_to(today["transcribe_seconds"], 60.0, 1e-6),
          f"increments must SUM: got {today['transcribe_seconds']}s, expected 60s")
    check(close_to(today["total_usd"], pricing.transcribe_seconds_to_usd(60.0), 1e-9),
          "summed usd is wrong")
    month = db.usage_month()
    check(close_to(month["transcribe_seconds"], 60.0, 1e-6),
          "usage_month disagrees with usage_today")


def test_db_usage_category_buckets_and_finalized_default():
    db = fresh_db()
    db.record_usage("live_voice", 0.05, seconds=60.0, session_id="b1")
    db.record_usage("backend", 0.01, session_id="b2")
    db.record_usage("tool", 0.02, session_id=None)
    db.record_usage("agent", 0.03, session_id="b3")
    t = db.usage_today()
    check(close_to(t["live_seconds"], 60.0, 1e-9), "live_seconds bucket wrong")
    check(close_to(t["backend_usd"], 0.01, 1e-12), "backend_usd bucket wrong")
    check(close_to(t["tools_usd"], 0.02, 1e-12), "tools_usd bucket wrong")
    check(close_to(t["agent_usd"], 0.03, 1e-12), "agent_usd bucket wrong")
    check(close_to(t["total_usd"], 0.11, 1e-12), f"total_usd wrong: {t['total_usd']}")
    check(all(r["finalized"] == 1 for r in db.usage_events(10)),
          "events must default to finalized=1")


def test_db_mark_unfinalized_keeps_the_stored_seconds():
    db = fresh_db()
    sid = "sess-U"
    db.record_usage("live_voice", pricing.live_seconds_to_usd(60.0), seconds=60.0, session_id=sid)
    db.mark_unfinalized(sid)
    row = next(r for r in db.usage_events(10) if r["session_id"] == sid)
    check(row["finalized"] == 0, "mark_unfinalized did not clear the finalized flag")
    check(close_to(row["seconds"], 60.0), "mark_unfinalized changed the stored seconds")
    check(close_to(row["usd"], pricing.live_seconds_to_usd(60.0)),
          "mark_unfinalized changed the stored usd")
    check(close_to(db.usage_today()["live_seconds"], 60.0, 1e-6),
          "mark_unfinalized changed today's usage total")
    # only the named category is flagged
    db.record_usage("backend", 0.001, session_id=sid)
    db.mark_unfinalized(sid, "live_voice")
    other = next(r for r in db.usage_events(10)
                 if r["session_id"] == sid and r["category"] == "backend")
    check(other["finalized"] == 1, "mark_unfinalized flagged the wrong category")


def test_db_tasks_lifecycle():
    db = fresh_db()
    tid = db.start_task("codex", "s1", "C:/work", "Do the thing", meta={"k": 1})
    check(tid > 0, "start_task did not return an id")
    t = db.get_task(tid)
    check(t is not None, "get_task returned None for a live task")
    check(t["status"] == "running", f"new task status is {t['status']!r}")
    check(t["agent"] == "codex" and t["title"] == "Do the thing", "task fields wrong")
    check(json.loads(t["meta"]) == {"k": 1}, "task meta not stored")

    db.update_task(tid, status="done", meta={"k": 2})
    t = db.get_task(tid)
    check(t["status"] == "done", "update_task did not change the status")
    check(json.loads(t["meta"]) == {"k": 2}, "update_task did not change the meta")
    db.update_task(tid, status="failed")
    check(json.loads(db.get_task(tid)["meta"]) == {"k": 2},
          "update_task(status only) clobbered the meta")

    second = db.start_task("claude", "s2", "C:/w2", "Second")
    check(db.list_tasks(limit=1)[0]["id"] == second, "list_tasks is not newest-first")
    check(len(db.list_tasks(limit=10)) == 2, "list_tasks returned the wrong count")
    check(db.get_task(999999) is None, "get_task must return None for a missing id")


def test_db_audit_records_allow_and_deny():
    db = fresh_db()
    db.audit("tool.call", {"name": "read_file"}, allowed=True)
    db.audit("tool.call", {"name": "write_file"}, allowed=False)
    rows = db.recent_audit(10)
    check(len(rows) == 2, f"expected 2 audit rows, got {len(rows)}")
    check(any(r["allowed"] == 0 for r in rows), "a denied action was not recorded")
    check(any(r["allowed"] == 1 for r in rows), "an allowed action was not recorded")
    check(rows[0]["action"] == "tool.call", "audit action not stored")
    # a non-serialisable detail must never raise
    db.audit("weird", object())


def test_db_prune_conversation():
    db = fresh_db()
    db.add_turn("user", "fresh", session_id="s1")
    check(db.prune_conversation(days=30) == 0, "prune deleted a fresh turn")
    check(db.prune_conversation(days=0) == 0, "prune with days<=0 must be a no-op")
    check(len(db.recent_turns(limit=10)) == 1, "prune removed the fresh turn")


# ==========================================================================
# 5. pricing
# ==========================================================================
def test_pricing_published_voice_rates():
    check(close_to(pricing.live_seconds_to_usd(60.0), 0.05, 1e-12),
          "60s of Live must cost exactly $0.05 (per-minute rate)")
    check(close_to(pricing.live_seconds_to_usd(30.0), 0.025, 1e-12),
          "30s of Live must cost half of a minute (billed per second, not rounded up)")
    check(close_to(pricing.live_seconds_to_usd(30.0),
                   pricing.live_seconds_to_usd(60.0) / 2.0, 1e-12),
          "Live billing must be linear in seconds")
    check(close_to(pricing.live_seconds_to_usd(61.0), 61.0 / 60.0 * 0.05, 1e-12),
          "61s must not be rounded up to two billed minutes")
    check(close_to(pricing.live_seconds_to_usd(1.0), 0.05 / 60.0, 1e-12),
          "a single second must be billed as a fraction of a minute")

    check(close_to(pricing.transcribe_seconds_to_usd(60.0), 0.0045, 1e-12),
          "60s of transcription must cost exactly $0.0045")
    check(close_to(pricing.transcribe_seconds_to_usd(90.0), 0.00675, 1e-12),
          "90s of transcription is wrong")

    check(pricing.live_seconds_to_usd(-5.0) == 0.0, "negative Live seconds must clamp to 0")
    check(pricing.transcribe_seconds_to_usd(-5.0) == 0.0, "negative audio seconds must clamp to 0")
    check(pricing.live_seconds_to_usd(0.0) == 0.0, "zero seconds must cost 0")


def test_pricing_tokens_monotonic_and_unknown_model_fallback():
    model = "gpt-5.6-luna"
    a = pricing.tokens_to_usd(model, 1000, 1000)
    b = pricing.tokens_to_usd(model, 1000, 2000)
    c = pricing.tokens_to_usd(model, 1000, 3000)
    check(a < b < c, f"cost must be monotonic in output tokens: {a} {b} {c}")

    fresh = pricing.tokens_to_usd(model, 1_000_000, 0, 0)
    cached = pricing.tokens_to_usd(model, 1_000_000, 0, 1_000_000)
    check(cached < fresh, "cached input must be cheaper than fresh input")

    in_rate, _cached_rate, out_rate = pricing.TEXT_MODELS[model]
    expected = 1000 / 1e6 * in_rate + 500 / 1e6 * out_rate
    check(close_to(pricing.tokens_to_usd(model, 1000, 500), expected, 1e-12),
          "token arithmetic does not match the published rates")

    unknown = pricing.tokens_to_usd("gpt-model-that-does-not-exist", 0, 1_000_000)
    worst_out = max(r[2] for r in pricing.TEXT_MODELS.values())
    check(close_to(unknown, worst_out, 1e-12),
          f"an unknown model must fall back to the most expensive rate ({worst_out}), got {unknown}")
    check(unknown > 0, "an unknown model must never be billed as 0")

    # every configured model must be usable and monotonic too
    for name in pricing.TEXT_MODELS:
        check(pricing.tokens_to_usd(name, 10_000, 10_000) > 0, f"{name} priced at 0")
    check(pricing.cheapest_text_model() in pricing.TEXT_MODELS,
          "cheapest_text_model returned an unknown name")


def test_pricing_monthly_projection():
    import calendar
    from datetime import date
    days = calendar.monthrange(date.today().year, date.today().month)[1]
    check(close_to(pricing.monthly_projection(3.0, 15), 3.0 / 15 * days, 1e-9),
          "straight-line projection is wrong")
    today_day = date.today().day
    check(close_to(pricing.monthly_projection(3.0), 3.0 / today_day * days, 1e-9),
          "the default day_of_month must be today's day")
    check(pricing.monthly_projection(3.0, -5) == 0.0,
          "a negative day must project 0")
    check(pricing.monthly_projection(0.0, 10) == 0.0, "zero spend projects zero")


# ==========================================================================
# 6. cost / budget accounting
# ==========================================================================
def test_cost_published_example_arithmetic():
    fresh_data_dir()
    dbmod.db()

    live_120 = pricing.live_seconds_to_usd(120 * 60)
    check(close_to(live_120, 6.00, 1e-9), f"120 Live minutes = {live_120}, expected $6.00")

    trans_120 = pricing.transcribe_seconds_to_usd(120 * 60)
    check(close_to(trans_120, 0.54, 1e-9), f"120 transcription minutes = {trans_120}, expected $0.54")

    mixed = (pricing.transcribe_seconds_to_usd(100 * 60)
             + pricing.live_seconds_to_usd(20 * 60))
    check(close_to(mixed, 1.45, 1e-9),
          f"100 transcription + 20 Live minutes = {mixed}, expected $1.45")

    # the same numbers through the manager's recording path
    fresh_data_dir()
    dbmod.db()
    mgr = cost.BudgetManager()
    recorded = mgr.record_live_snapshot("ex-120", 120 * 60)
    check(close_to(recorded, 6.00, 1e-9), f"record_live_snapshot returned {recorded}")
    check(close_to(mgr.snapshot().today_usd, 6.00, 1e-9), "manager snapshot disagrees")
    check(close_to(mgr.snapshot().live_seconds_today, 7200.0, 1e-6),
          "manager live-seconds counter disagrees")


def test_cost_check_can_start_blocks_at_and_past_cap():
    fresh_data_dir()
    database = dbmod.db()
    mgr = cost.BudgetManager()
    cap = float(config.get("daily_budget_usd"))
    check(close_to(cap, 3.00, 1e-9), f"unexpected daily cap {cap}")

    # just below the cap: allowed
    database.record_usage("live_voice", pricing.live_seconds_to_usd(3540.0), seconds=3540.0,
                          session_id="below-cap")
    check(mgr.snapshot().today_usd < cap, "test setup: spend should be below the cap")
    mgr.check_can_start()  # must not raise

    # an estimate that would overshoot: blocked
    raised = False
    try:
        mgr.check_can_start(estimated_usd=1.0)
    except cost.BudgetExceeded:
        raised = True
    check(raised, "an estimate that overshoots the ceiling must raise BudgetExceeded")
    check(mgr.would_exceed(1.0) is True, "would_exceed() disagreed with check_can_start()")
    check(mgr.would_exceed(0.0001) is False, "a request that fits must not be blocked")

    # at the cap exactly: blocked for any new billable work
    fresh_data_dir()
    database = dbmod.db()
    mgr = cost.BudgetManager()
    database.record_usage("live_voice", pricing.live_seconds_to_usd(3600.0), seconds=3600.0,
                          session_id="at-cap")
    snap = mgr.snapshot()
    check(close_to(snap.today_usd, cap, 1e-9), f"spend {snap.today_usd} != cap {cap}")
    check(close_to(snap.daily_ratio, 1.0, 1e-9), f"daily ratio {snap.daily_ratio} != 1.0")

    raised = False
    message = ""
    try:
        mgr.check_can_start()
    except cost.BudgetExceeded as exc:
        raised = True
        message = str(exc)
    check(raised, "check_can_start did not raise once the daily cap was reached")
    check("ceiling" in message.lower() or "cap" in message.lower(),
          f"the budget message is not explanatory: {message!r}")
    check(mgr.would_exceed(0.0) is True, "would_exceed(0) must be True at the cap")


def test_cost_monthly_cap_blocks():
    fresh_data_dir()
    database = dbmod.db()
    # Raise today's ceiling so the DAILY check cannot fire first: this isolates
    # the monthly branch, which is the one under test.
    config.set_value("daily_budget_usd", 100.0)
    mgr = cost.BudgetManager()
    cap = float(config.get("monthly_budget_usd"))
    check(close_to(cap, 30.00, 1e-9), f"unexpected monthly cap {cap}")
    # one session snapshot large enough to blow the monthly ceiling on its own
    database.record_usage("live_voice", pricing.live_seconds_to_usd(36000.0), seconds=36000.0,
                          session_id="monthly-cap")
    snap = mgr.snapshot()
    check(snap.today_usd < snap.daily_cap,
          f"test setup: today {snap.today_usd} must stay under the daily cap {snap.daily_cap}")
    check(snap.month_usd >= cap, f"test setup: month {snap.month_usd} should reach {cap}")
    raised = False
    message = ""
    try:
        mgr.check_can_start()
    except cost.BudgetExceeded as exc:
        raised = True
        message = str(exc)
    check(raised, "check_can_start did not raise at the monthly ceiling")
    check("monthly" in message.lower() or "month" in message.lower(),
          f"expected a monthly message, got {message!r}")


def test_cost_warn_thresholds_fire_at_80_percent_once():
    fresh_data_dir()
    database = dbmod.db()
    calls: list[tuple[str, str]] = []
    mgr = cost.BudgetManager(on_warning=lambda level, msg: calls.append((level, msg)))
    cap = float(config.get("daily_budget_usd"))
    warn_ratio = float(config.get("warn_at_ratio"))

    # record straight into the DB so the manager's warned-state stays untouched
    seconds = 60.0 * warn_ratio * cap / pricing.USD_PER_MIN_LIVE
    database.record_usage("live_voice", pricing.live_seconds_to_usd(seconds), seconds=seconds,
                          session_id="warn-80")
    snap = mgr.snapshot()
    check(snap.daily_ratio >= warn_ratio - 1e-9,
          f"test setup: ratio {snap.daily_ratio} should be at/over {warn_ratio}")

    for _ in range(3):
        mgr.warn_thresholds()
    check(len(calls) == 1, f"the 80% warning fired {len(calls)} times, expected exactly 1")
    check(calls[0][0] == "warn", f"expected level 'warn', got {calls[0][0]!r}")
    check(str(int(round(warn_ratio * 100))) in calls[0][1],
          f"the warning should mention the percentage: {calls[0][1]!r}")

    # crossing 100% fires the distinct 'error' warning, also once
    database.record_usage("live_voice", pricing.live_seconds_to_usd(3600.0), seconds=3600.0,
                          session_id="warn-100")
    for _ in range(3):
        mgr.warn_thresholds()
    errors_fired = [c for c in calls if c[0] == "error"]
    check(len(errors_fired) == 1,
          f"the 100% warning fired {len(errors_fired)} times, expected exactly 1")
    check(len(calls) == 2, f"unexpected extra warnings: {calls}")


def test_cost_mark_incomplete_finalization_flags_the_row():
    fresh_data_dir()
    database = dbmod.db()
    mgr = cost.BudgetManager()
    sid = "incomplete-1"
    mgr.record_live_snapshot(sid, 45.0)
    row = next(r for r in database.usage_events(10) if r["session_id"] == sid)
    check(row["finalized"] == 1, "a cleanly reported session should be finalized")
    check(close_to(row["seconds"], 45.0), "stored seconds wrong before flagging")

    mgr.mark_incomplete_finalization(sid)
    row = next(r for r in database.usage_events(10) if r["session_id"] == sid)
    check(row["finalized"] == 0, "mark_incomplete_finalization did not clear the flag")
    check(close_to(row["seconds"], 45.0),
          "flagging a session unconfirmed must not change the stored seconds")
    check(close_to(row["usd"], pricing.live_seconds_to_usd(45.0)),
          "flagging a session unconfirmed must not change the stored usd")


def test_cost_snapshot_dict_shape_and_honesty_notice():
    fresh_data_dir()
    dbmod.db()
    snap = cost.BudgetManager().snapshot()
    d = snap.as_dict()
    check("today" in d and "month" in d, "snapshot dict is missing sections")
    check("total_usd" in d["today"] and "total_usd" in d["month"], "snapshot totals missing")
    check("estimate_notice" in d and d["estimate_notice"],
          "the snapshot must carry the 'these are local estimates' notice")
    notice = d["estimate_notice"].lower()
    check("not enforced" in notice or "separate" in notice,
          f"the notice must not claim to enforce account-level budgets: {notice!r}")
    check(isinstance(snap.projection_usd, float), "projection must be a float")


def test_cost_estimate_minutes_helper():
    check(close_to(pricing.estimate_minutes_usd(120, "live_voice"), 6.00, 1e-9),
          "estimate_minutes_usd live is wrong")
    check(close_to(pricing.estimate_minutes_usd(120, "transcribe"), 0.54, 1e-9),
          "estimate_minutes_usd transcribe is wrong")
    check(pricing.estimate_minutes_usd(10, "unknown-category") == 0.0,
          "an unknown category must not be priced")


# ==========================================================================
# 7. engines/errors
# ==========================================================================
def test_errors_http_status_mapping():
    auth_body = json.dumps({"error": {"type": "authentication_error",
                                      "message": f"Incorrect API key provided: {FAKE_KEY}"}})
    e = errors.from_http(401, auth_body)
    check(e.kind == "auth", f"401 mapped to {e.kind!r}, expected 'auth'")
    check(e.status == 401, "status not carried")
    check(e.retryable is False, "an auth failure must not be retryable")
    check(FAKE_KEY not in str(e), f"the auth message leaked a key: {e}")
    check(FAKE_KEY not in e.detail, "the auth detail leaked a key")

    e = errors.from_http(429, json.dumps({"error": {"type": "rate_limit_error",
                                                    "message": "Rate limit reached"}}))
    check(e.kind == "rate_limit", f"429 mapped to {e.kind!r}, expected 'rate_limit'")
    check(e.retryable is True, "429 must be retryable")

    e = errors.from_http(400, json.dumps({"error": {"type": "invalid_request_error",
                                                    "message": "bad field"}}))
    check(e.kind == "bad_request", f"400 mapped to {e.kind!r}, expected 'bad_request'")
    check(e.retryable is False, "a bad request must not be retryable")

    e = errors.from_http(500, json.dumps({"error": {"type": "server_error", "message": "boom"}}))
    check(e.kind == "server", f"500 mapped to {e.kind!r}, expected 'server'")
    check(e.retryable is True, "500 must be retryable")

    # status-only, empty body
    check(errors.from_http(401).kind == "auth", "401 with no body must still be 'auth'")
    check(errors.from_http(429).kind == "rate_limit", "429 with no body must be 'rate_limit'")
    check(errors.from_http(400).kind == "bad_request", "400 with no body must be 'bad_request'")
    check(errors.from_http(500).kind == "server", "500 with no body must be 'server'")
    check(errors.from_http(503).retryable is True, "503 must be retryable")
    check(errors.from_http(403).kind == "permission", "403 must map to 'permission'")
    check(errors.from_http(404).kind == "not_found", "404 must map to 'not_found'")

    # a non-JSON body must not crash
    e = errors.from_http(502, "<html>bad gateway</html>")
    check(e.kind == "server", f"an HTML body should still classify by status, got {e.kind}")


def test_errors_never_leak_key_shaped_strings():
    bodies = [
        (400, json.dumps({"error": {"message": f"invalid key {FAKE_KEY} supplied"}})),
        (400, json.dumps({"error": {"type": "invalid_request_error",
                                    "message": f"auth header Bearer {FAKE_KEY} malformed"}})),
        (400, f"raw text containing {FAKE_KEY} inline"),
        (500, json.dumps({"error": {"message": f"upstream saw api_key={FAKE_KEY}"}})),
    ]
    for status, body in bodies:
        e = errors.from_http(status, body)
        check(FAKE_KEY not in str(e), f"HTTP {status}: message leaked the key: {e}")
        check(FAKE_KEY not in e.detail, f"HTTP {status}: detail leaked the key")
        check(FAKE_KEY not in json.dumps(e.as_dict()),
              f"HTTP {status}: as_dict() leaked the key")

    # direct construction is scrubbed too
    e = errors.EngineError(f"boom {FAKE_KEY}", detail=f"detail {FAKE_KEY}")
    check(FAKE_KEY not in str(e), "EngineError(message=...) leaked a key")
    check(FAKE_KEY not in e.detail, "EngineError(detail=...) leaked a key")


def test_errors_classify_table():
    check(errors.classify(401, "", "") == ("auth", False), "classify(401)")
    check(errors.classify(429, "", "") == ("rate_limit", True), "classify(429)")
    check(errors.classify(400, "", "") == ("bad_request", False), "classify(400)")
    check(errors.classify(500, "", "") == ("server", True), "classify(500)")
    check(errors.classify(503, "", "")[1] is True, "classify(503) retryable")
    check(errors.classify(None, "invalid_api_key", "")[0] == "auth", "code mapping")
    check(errors.classify(None, "insufficient_quota", "")[0] == "quota", "quota mapping")
    check(errors.classify(None, "", "the model does not exist")[0] == "model_unavailable",
          "model-unavailable detection")
    check(errors.classify(None, "", "context length exceeded")[0] == "too_large",
          "too-large detection")
    check(errors.classify(None, "", "")[1] is True, "an unknown error is worth retrying")


def test_errors_from_exception():
    class _Resp:
        status_code = 401
        text = json.dumps({"error": {"type": "authentication_error", "message": "nope"}})

    class _SdkError(Exception):
        status_code = 401
        response = _Resp()

    e = errors.from_exception(_SdkError("boom"))
    check(e.kind == "auth", f"an SDK 401 mapped to {e.kind!r}")
    check(e.status == 401, "status not carried from the SDK exception")

    e = errors.from_exception(ConnectionError("connection reset by peer"))
    check(e.kind == "network", f"a connection error mapped to {e.kind!r}, expected 'network'")
    check(e.retryable is True, "a network error must be retryable")

    e = errors.from_exception(TimeoutError("request timed out"))
    check(e.kind == "network", f"a timeout mapped to {e.kind!r}")

    original = errors.EngineError("already an engine error", kind="quota")
    check(errors.from_exception(original) is original,
          "an EngineError must pass through unchanged")

    e = errors.from_exception(ValueError("something odd"))
    check(e.kind == "unknown", f"an unrecognised exception mapped to {e.kind!r}")
    check(isinstance(e.as_dict(), dict), "as_dict() must return a dict")


# ==========================================================================
# 8. engines/cleanup - protected tokens + the post-call verification guard
# ==========================================================================
def test_cleanup_protected_tokens_categories():
    text = (r"Set port 8080 to 50% and pay $1,234.56 at https://ex.com/a?b=1 "
            r"in C:\Users\user\note.txt or email user@example.com "
            "using iPhone APIKey and NASA ACME, but do not deploy without approval")
    toks = cleanup.protected_tokens(text)
    for expected in ["8080", "50%", "1,234.56", "https://ex.com/a?b=1",
                     r"C:\Users\user\note.txt", "user@example.com",
                     "iPhone", "APIKey", "NASA", "ACME", "not", "without"]:
        check(expected in toks, f"protected_tokens missed {expected!r}; got {toks}")
    check(toks == sorted(toks, key=len, reverse=True),
          "protected_tokens should be sorted longest-first")
    check(cleanup.protected_tokens("") == [], "protected_tokens('') must be empty")
    check(cleanup.protected_tokens(None) == [], "protected_tokens(None) must be empty")


def test_cleanup_protected_tokens_negations():
    for neg in ("not", "no", "never", "none", "without", "cannot", "don't", "won't",
                "isn't", "didn't"):
        check(neg in cleanup.protected_tokens(f"I said {neg} to that thing"),
              f"negation {neg!r} was not protected")
    check("notebook" not in cleanup.protected_tokens("my notebook is here"),
          "a word merely containing 'no' must not be treated as a negation")


class _FakeUsage:
    input_tokens = 120
    output_tokens = 24
    input_tokens_details = None


class _FakeResponse:
    def __init__(self, text):
        self.output_text = text
        self.usage = _FakeUsage()


class _FakeResponses:
    def __init__(self, text):
        self._text = text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(self._text)


class _FakeSdk:
    def __init__(self, text):
        self.responses = _FakeResponses(text)


def _offline_cleanup_engine(rewrite_text: str) -> cleanup.CleanupEngine:
    """A CleanupEngine whose model call returns `rewrite_text` - no network."""
    eng = cleanup.CleanupEngine(api_key=FAKE_KEY)
    eng._client = _FakeSdk(rewrite_text)   # _sdk() returns this verbatim
    return eng


def test_cleanup_verbatim_never_calls_the_model():
    eng = _offline_cleanup_engine("this must never be used")
    res = eng.apply("keep me exactly as spoken", style="verbatim")
    check(res.applied is False, "verbatim must not be applied as a rewrite")
    check(res.text == "keep me exactly as spoken", "verbatim altered the text")
    check(res.rejected is False, "verbatim is not a rejection")
    check(len(eng._client.responses.calls) == 0, "verbatim made a model call")

    res = eng.apply("", style="light")
    check(res.text == "" and res.applied is False, "an empty transcript must pass through")
    check(len(eng._client.responses.calls) == 0, "an empty transcript made a model call")


def test_cleanup_guard_rejects_a_dropped_number():
    raw = "Use port 8080 for the service"
    eng = _offline_cleanup_engine("Use port for the service.")  # 8080 dropped
    res = eng.apply(raw, style="light")
    check(res.rejected is True, "the guard accepted a rewrite that dropped a number")
    check(res.applied is False, "a rejected rewrite must not be applied")
    check(res.text == raw, "a rejected rewrite must fall back to the raw transcript")
    check(res.raw == raw, "the raw transcript must be preserved")
    check("8080" in res.rejected_tokens,
          f"the dropped number should be reported: {res.rejected_tokens}")
    check("8080" in res.detail, f"the reason should name the token: {res.detail!r}")
    check(res.as_dict()["rejected"] is True, "as_dict() disagrees about the rejection")


def test_cleanup_guard_rejects_a_dropped_negation():
    raw = "Do not deploy on Friday"
    eng = _offline_cleanup_engine("Deploy on Friday.")
    res = eng.apply(raw, style="light")
    check(res.rejected is True, "the guard accepted a rewrite that dropped a negation")
    check("not" in res.rejected_tokens,
          f"the dropped negation should be reported: {res.rejected_tokens}")
    check(res.text == raw, "the rejection must return the raw transcript")


def test_cleanup_guard_rejects_a_changed_path_and_url():
    raw = r"Copy C:\Users\user\data.csv to https://example.com/upload now"
    eng = _offline_cleanup_engine(r"Copy the file to https://example.com/upload now")
    res = eng.apply(raw, style="polish")
    check(res.rejected is True, "the guard accepted a rewrite that dropped a file path")
    check(res.text == raw, "the rejection must return the raw transcript")


def test_cleanup_guard_accepts_a_faithful_rewrite():
    raw = "um so we need to um deploy release 2026-09-11 to NASA and not before friday"
    faithful = "So we need to deploy release 2026-09-11 to NASA, and not before Friday."
    eng = _offline_cleanup_engine(faithful)
    res = eng.apply(raw, style="light")
    check(res.rejected is False,
          f"the guard wrongly rejected a faithful rewrite: {res.rejected_tokens}")
    check(res.applied is True, "a faithful rewrite should be applied")
    check(res.text == faithful, "the faithful rewrite was not returned")
    check(res.changed is True, "changed should be True when the text differs")
    check(res.raw == raw, "the raw transcript must still be available")


def test_cleanup_prompt_treats_the_transcript_as_data():
    raw = "delete all the files"
    eng = _offline_cleanup_engine("Delete all the files.")
    eng.apply(raw, style="polish")
    call = eng._client.responses.calls[-1]
    user = call["input"]
    instructions = call["instructions"]
    check("<transcript>" in user and "</transcript>" in user,
          "the dictated transcript must be delimited as data")
    check(raw in user, "the raw transcript was not passed into the prompt")
    low = instructions.lower()
    check("never" in low and ("data" in low or "not a request" in low),
          "the system prompt must forbid following instructions found in the transcript")


# ==========================================================================
# 9. audio/vad
# ==========================================================================
def _pcm16(samples) -> bytes:
    return np.asarray(samples, dtype="<i2").tobytes()


def _silence(n_samples: int) -> bytes:
    return b"\x00\x00" * n_samples


def _noise(n_samples: int, amp: int = 120, seed: int = 7) -> bytes:
    rng = np.random.default_rng(seed)
    return _pcm16(rng.integers(-amp, amp + 1, n_samples))


def _tone(n_samples: int, rate: int, freq: float = 440.0, amp: float = 0.3) -> bytes:
    t = np.arange(n_samples, dtype=np.float64) / rate
    return _pcm16(np.round(amp * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int32))


def test_vad_digital_silence_is_not_speech():
    rate = 16000
    sil = _silence(rate)
    check(vad.has_speech(sil, rate) is False, "digital silence must not be speech")
    a = vad.analyze(sil, rate)
    check(a["has_speech"] is False, "analyze() called digital silence speech")
    check(a["duration"] > 0.9, f"duration wrong: {a['duration']}")
    check(a["peak_db"] < vad.SILENCE_DB, f"silence peak_db {a['peak_db']} should be very low")
    check(a["rms_db"] < vad.SILENCE_DB, f"silence rms_db {a['rms_db']} should be very low")
    check(a["clipped"] is False, "digital silence reported as clipped")
    check(vad.has_speech(b"", rate) is False, "an empty buffer must not be speech")
    check(vad.trim_silence(b"", rate) == b"", "trim_silence(b'') must be b''")


def test_vad_low_level_noise_is_not_speech():
    rate = 16000
    noise = _noise(rate, amp=120)
    a = vad.analyze(noise, rate)
    check(a["peak_db"] < vad.SPEECH_DB,
          f"test setup: noise peak {a['peak_db']} should sit below the speech threshold")
    check(a["has_speech"] is False, f"low noise flagged as speech: {a}")
    check(vad.has_speech(noise, rate) is False, "low-level noise must not count as speech")
    check(a["rms_db"] < vad.SPEECH_DB, f"noise rms_db {a['rms_db']} too high")


def test_vad_tone_burst_is_speech():
    rate = 16000
    tone = _tone(rate, rate, amp=0.3)
    a = vad.analyze(tone, rate)
    check(a["peak_db"] > vad.SPEECH_DB, f"tone peak {a['peak_db']} should exceed the threshold")
    check(a["has_speech"] is True, f"a tone burst was not detected: {a}")
    check(vad.has_speech(tone, rate) is True, "has_speech() missed a clear tone")
    check(a["speech_ratio"] > 0.9, f"speech_ratio for a continuous tone: {a['speech_ratio']}")
    check(close_to(a["duration"], 1.0, 0.01), f"duration {a['duration']} != 1.0s")
    check(a["clipped"] is False, "a 0.3 amplitude tone must not report clipping")


def test_vad_analyze_reports_all_documented_fields():
    rate = 16000
    a = vad.analyze(_tone(int(rate * 0.5), rate), rate)
    for field in ("duration", "peak_db", "rms_db", "speech_ratio", "has_speech", "clipped"):
        check(field in a, f"analyze() is missing the {field!r} field")
    check(a["peak_db"] >= a["rms_db"], "peak_db must be >= rms_db")
    check(0.0 <= a["speech_ratio"] <= 1.0, f"speech_ratio out of range: {a['speech_ratio']}")
    db = vad.frame_db(_tone(int(rate * 0.5), rate), rate)
    check(db.size >= 1, "frame_db returned nothing for a real buffer")
    check(np.all(np.isfinite(db)), "frame_db produced non-finite values")


def test_vad_trim_silence_reduces_but_keeps_speech():
    rate = 16000
    pad = _silence(int(rate * 0.8))
    tone = _tone(int(rate * 0.5), rate, amp=0.3)
    padded = pad + tone + pad
    trimmed = vad.trim_silence(padded, rate)
    check(len(trimmed) > 0, "trim_silence returned empty even though speech was present")
    check(len(trimmed) < len(padded),
          f"trim_silence did not shorten the buffer ({len(trimmed)} vs {len(padded)} bytes)")
    check(len(trimmed) % 2 == 0, "trim_silence returned an odd byte length (misaligned PCM)")
    dur = len(trimmed) / 2.0 / rate
    check(0.5 <= dur < len(padded) / 2.0 / rate,
          f"trimmed duration {dur:.3f}s should keep the speech but drop silence")
    # pure silence trims away entirely
    check(vad.trim_silence(_silence(rate), rate) == b"", "all-silence input should trim to nothing")


# ==========================================================================
# 10. audio/resample
# ==========================================================================
def _peak_freq(pcm: bytes, rate: int) -> float:
    x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float64)
    x = x - x.mean()
    w = np.hanning(x.size) if x.size > 8 else np.ones(x.size)
    spec = np.abs(np.fft.rfft(x * w))
    k = int(np.argmax(spec[1:])) + 1
    return k * rate / x.size


def _corr(a: bytes, b: bytes) -> float:
    x = np.frombuffer(a[: len(a) // 2 * 2], dtype="<i2").astype(np.float64)
    y = np.frombuffer(b[: len(b) // 2 * 2], dtype="<i2").astype(np.float64)
    n = min(x.size, y.size)
    if n == 0:
        return 0.0
    return float(np.corrcoef(x[:n], y[:n])[0, 1])


def test_resample_offline_length_ratio():
    src_rate, dst_rate = 22050, 24000
    src = _tone(src_rate, src_rate)          # 1 second
    out = resample.resample_offline(src, src_rate, dst_rate)
    ratio = (len(out) / 2) / (len(src) / 2)
    expected = dst_rate / src_rate
    check(abs(ratio - expected) < 0.01 * expected,
          f"length ratio {ratio:.4f} is not within 1% of {expected:.4f}")
    check(len(out) % 2 == 0, "resampled output must be whole PCM16 samples")
    check(resample.resample_offline(src, src_rate, src_rate) is src,
          "a matching rate must pass the buffer through unchanged")
    check(resample.resample_offline(b"", 22050, 24000) == b"", "empty input must stay empty")


def test_resample_offline_roundtrip_correlation():
    src_rate, mid_rate = 22050, 16000
    src = _tone(src_rate, src_rate)
    down = resample.resample_offline(src, src_rate, mid_rate)
    back = resample.resample_offline(down, mid_rate, src_rate)
    corr = _corr(src, back)
    check(corr > 0.7, f"down/up round-trip correlation is only {corr:.3f}")

    src24 = _tone(24000, 24000)
    back24 = resample.resample_offline(
        resample.resample_offline(src24, 24000, 16000), 16000, 24000)
    corr24 = _corr(src24, back24)
    check(corr24 > 0.7, f"24k down/up round-trip correlation is only {corr24:.3f}")


def test_resample_offline_preserves_pitch():
    """The whole point of rate conversion: the tone must stay at 440 Hz."""
    for src_rate, dst_rate in ((22050, 24000), (24000, 16000), (16000, 24000), (48000, 24000)):
        out = resample.resample_offline(_tone(src_rate, src_rate), src_rate, dst_rate)
        got = _peak_freq(out, dst_rate)
        check(abs(got - 440.0) <= 0.02 * 440.0,
              f"{src_rate}->{dst_rate} changed the pitch: 440 Hz became {got:.1f} Hz")


def test_streaming_resampler_passthrough():
    r = resample.StreamingResampler(24000, 24000)
    check(r.passthrough is True, "matching rates must report passthrough")
    check(r.process(b"\x01\x02\x03") == b"\x01\x02\x03",
          "passthrough must return the bytes untouched")
    check(r.process(b"") == b"", "empty input must return empty")


def test_streaming_resampler_sample_count():
    src_rate, dst_rate = 24000, 16000
    n = src_rate            # one second
    data = _tone(n, src_rate)
    out = resample.StreamingResampler(src_rate, dst_rate).process(data)
    expected = n * dst_rate / src_rate
    check(abs(len(out) / 2 - expected) <= 0.01 * expected,
          f"one-shot output {len(out) // 2} samples, expected about {expected:.0f}")
    check(len(out) % 2 == 0, "streaming output must be whole samples")


def test_streaming_resampler_carries_an_odd_trailing_byte():
    """Odd-length chunks must not drop a byte and lose 16-bit alignment."""
    src_rate, dst_rate = 24000, 16000
    data = _tone(24000, src_rate)

    even_res = resample.StreamingResampler(src_rate, dst_rate)
    even = b"".join(even_res.process(data[i:i + 2048])
                    for i in range(0, len(data), 2048))

    odd_res = resample.StreamingResampler(src_rate, dst_rate)
    odd = b"".join(odd_res.process(data[i:i + 2047])
                   for i in range(0, len(data), 2047))

    check(len(even) == len(odd),
          f"odd-length chunks produced {len(odd) // 2} samples vs {len(even) // 2} "
          f"- bytes were dropped instead of carried")
    corr = _corr(even, odd)
    check(corr > 0.99,
          f"odd-length chunks misaligned the stream (correlation {corr:.3f}); "
          f"the trailing byte must be carried into the next call")

    # direct: a single odd byte is held, then re-attached on the next call
    r = resample.StreamingResampler(24000, 16000)
    r.process(b"\x00" * 3001)               # odd length
    check(r._carry == b"\x00"[-1:], f"the trailing byte was not held: {r._carry!r}")
    check(len(r._carry) == 1, "exactly one trailing byte must be held")


# ==========================================================================
# 11. engines/live - transcript assembly and finalisation decisions
# ==========================================================================
def test_transcript_assembler_empty():
    a = live.TranscriptAssembler()
    check(a.text() == "", "an empty assembler must return ''")
    check(a.covered_ms == 0.0, "an empty assembler must cover 0 ms")
    check(len(a) == 0, "an empty assembler must have no fragments")
    check(a.last_arrival == 0.0, "an empty assembler has no arrival time")


def test_transcript_assembler_orders_out_of_order_fragments():
    a = live.TranscriptAssembler()
    a.add(" world", 1200, 1800)      # arrives first, belongs in the middle
    a.add("hello", 0, 600)
    a.add(", ", 600, 1200)
    check(a.text() == "hello,  world",
          f"fragments were not returned in start_ms order: {a.text()!r}")
    check(close_to(a.covered_ms, 1800.0), f"covered_ms {a.covered_ms} != max end_ms 1800")
    check(len(a) == 3, f"expected 3 fragments, got {len(a)}")
    check(a.last_arrival > 0, "an arrival timestamp must be recorded")


def test_transcript_assembler_ignores_empty_and_none():
    a = live.TranscriptAssembler()
    a.add("real", 0, 500)
    a.add("", 500, 900)
    check(len(a) == 1, "an empty delta fragment must be ignored")
    check(close_to(a.covered_ms, 500.0), "an ignored fragment must not move covered_ms")

    b = live.TranscriptAssembler()
    b.add("x", None, None)
    check(len(b) == 1, "a fragment with None intervals must still be recorded")
    check(b.covered_ms == 0.0, "None intervals must become 0 ms")


class _OfflineSession(live.LiveSession):
    """A LiveSession that never opens a socket (so the decision logic is testable)."""

    async def connect(self, open_timeout: float = 20.0) -> None:
        self.started = True


def _run_dictation(sent_ms: float, covered_ms: float, text: str,
                   fragments: bool = True, speech_detected=None) -> dict:
    rate = live.LiveConfig().rate
    session = _OfflineSession(live.LiveConfig(dictation=True), api_key=FAKE_KEY)
    if fragments:
        session.input_transcript.add(text, 0.0, float(covered_ms))
        # pretend the fragment arrived a while ago so drain() does not extend
        session.input_transcript.received_at = [time.monotonic() - 10.0] * len(
            session.input_transcript.received_at)
    nbytes = int(sent_ms / 1000.0 * rate) * 2

    async def source():
        yield b"\x00\x00" * (nbytes // 2)

    return asyncio.run(session.run_dictation(source(), drain_ms=5.0,
                                             close_timeout=0.05,
                                             speech_detected=speech_detected))


def test_live_dictation_reports_a_lagging_transcript_as_incomplete():
    r = _run_dictation(5000, 1000, "hello there", speech_detected=True)
    check(r["ok"] is True, "the result should be ok")
    check(close_to(r["audio_ms_sent"], 5000.0, 1.0),
          f"audio_ms_sent is {r['audio_ms_sent']}, expected ~5000")
    check(close_to(r["transcript_covered_ms"], 1000.0, 1.0),
          f"transcript_covered_ms is {r['transcript_covered_ms']}, expected ~1000")
    check(r["incomplete_capture"] is True,
          f"a transcript lagging far behind the audio must be flagged incomplete: {r}")
    check(r["requires_confirmation"] is True, "an incomplete capture must need confirmation")
    check(any("covers" in n for n in r["notes"]),
          f"the shortfall should be explained in notes: {r['notes']}")


def test_live_dictation_accepts_a_caught_up_transcript():
    r = _run_dictation(5000, 4900, "hello there", speech_detected=True)
    check(r["incomplete_capture"] is False,
          f"a transcript that caught up must not be flagged incomplete: {r}")
    check(r["requires_confirmation"] is False, "nothing needs confirming here")
    check(r["likely_fabricated"] is False, "speech was detected, so nothing is fabricated")
    check(close_to(r["transcript_covered_ms"], 4900.0, 1.0), "coverage was not reported")


def test_live_dictation_flags_audio_with_no_transcript():
    r = _run_dictation(5000, 0, "", fragments=False, speech_detected=True)
    check(r["incomplete_capture"] is True,
          "audio that produced no transcript must be flagged incomplete")
    check(any("no transcript" in n for n in r["notes"]),
          f"the missing transcript should be explained: {r['notes']}")
    check(r["requires_confirmation"] is True, "an empty capture must need confirmation")


def test_live_dictation_flags_text_over_local_silence():
    r = _run_dictation(5000, 4900, "the user.", speech_detected=False)
    check(r["likely_fabricated"] is True,
          "text returned over local silence must be flagged as possibly invented")
    check(r["requires_confirmation"] is True, "fabrication risk must need confirmation")
    check(any("invented" in n or "no speech" in n for n in r["notes"]),
          f"the fabrication risk should be explained: {r['notes']}")
    check(r["text"] == "the user.", "the suspect text must still be returned for review")


# ==========================================================================
# 12. core/asyncrun
# ==========================================================================
def test_asyncrunner_runs_coroutines_off_thread():
    runner = AsyncRunner(name="offline-test")
    try:
        async def add(a, b):
            return a + b

        check(runner.run(add(2, 3)) == 5, "AsyncRunner.run returned the wrong value")
        fut = runner.submit(add(10, 20))
        check(fut.result(timeout=5) == 30, "AsyncRunner.submit returned the wrong value")
        check(not runner.loop.is_closed(), "the shared loop should be open")
    finally:
        runner.stop()


# --------------------------------------------------------------------------
# flat runner (pytest just collects the test_* functions above)
# --------------------------------------------------------------------------
def _collect_tests():
    return [(name, obj) for name, obj in list(globals().items())
            if name.startswith("test_") and callable(obj)]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    filters = [a for a in argv if not a.startswith("-")]
    tests = _collect_tests()
    if filters:
        tests = [(n, f) for n, f in tests if any(flt in n for flt in filters)]

    print("=" * 78)
    print("Jarvis - offline core suite (no network, no billing)")
    print(f"data dir : {os.environ['LOCALAPPDATA']}")
    print(f"tests    : {len(tests)}")
    print("=" * 78)

    passed = 0
    failures = []
    started = time.perf_counter()
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failures.append((name, exc))
            print(f"FAIL  {name}")
            print(f"      {type(exc).__name__}: {exc}")
        else:
            passed += 1
            print(f"PASS  {name}")
    elapsed = time.perf_counter() - started

    print("-" * 78)
    print(f"TOTAL {len(tests)}   PASSED {passed}   FAILED {len(failures)}   "
          f"({elapsed:.2f}s)")
    if failures:
        print("\nFAILURE SUMMARY")
        for name, exc in failures:
            print(f"  {name}: {type(exc).__name__}: {exc}")

    shutil.rmtree(_TMP_ROOT, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
