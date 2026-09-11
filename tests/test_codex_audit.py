"""Offline regression evidence for Codex's 2026-09-11 fixes. No API calls."""
import asyncio
import ctypes
import os
import sys
import tempfile
import threading
import time
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TEMP = tempfile.TemporaryDirectory(prefix="jarvis_codex_")
os.environ["BV_DATA_DIR"] = TEMP.name
from jarvis import config, secrets
from jarvis.app import Application, AssistantSession
from jarvis.core.asyncrun import AsyncRunner
from jarvis.core.cost import BudgetManager, BudgetExceeded
from jarvis.core.dictation import DictationController, DictationResult, CAPTURING
from jarvis.core.delegation import DelegatedTools, function_schemas
from jarvis.db import Database
from jarvis.engines import cleanup, live
from jarvis.audio.playback import Speaker
from jarvis.win import overlay


class AuditTests(unittest.TestCase):
    def setUp(self):
        config._cache = dict(config.DEFAULT_CONFIG)

    def test_live_silence_never_constructs_a_connection(self):
        ctl = DictationController()
        with patch.object(live, "LiveSession") as factory:
            result = ctl._run_live(b"\0\0" * 24000, 1.0, False)
        self.assertFalse(result.ok)
        factory.assert_not_called()

    def test_uncertain_transcript_cannot_run_a_command(self):
        ctl = DictationController()
        result = DictationResult(True, text="open notepad", requires_confirmation=True)
        with patch("jarvis.core.tools.call") as call:
            self.assertFalse(ctl._run_spoken_command(result))
        call.assert_not_called()

    def test_cleanup_requires_exact_token_counts(self):
        for raw, changed in [("pay 12", "pay 123"), ("no changes", "now changes"),
                             ("12 plus 12", "12"), ("don\u2019t do it", "do it")]:
            self.assertNotEqual(Counter(cleanup.protected_tokens(raw)),
                                Counter(cleanup.protected_tokens(changed)))

    def test_cleanup_rejects_changed_number_in_actual_engine(self):
        engine = cleanup.CleanupEngine()
        engine._client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kw:
            SimpleNamespace(output_text="pay 123", usage=None)))
        result = engine.apply("pay 12", style="light")
        self.assertTrue(result.rejected)
        self.assertEqual(result.text, "pay 12")

    def test_monthly_headroom_is_checked(self):
        budget = BudgetManager()
        snap = SimpleNamespace(daily_cap=10, today_usd=0, monthly_cap=1, month_usd=.99)
        with patch.object(budget, "_snapshot", return_value=snap):
            with self.assertRaises(BudgetExceeded):
                budget.check_can_start(.02)

    def test_deleted_vocabulary_stays_deleted_after_restart(self):
        path = Path(TEMP.name) / "vocab.db"
        db = Database(path)
        db.set_vocab(["Only my term"])
        db.close()
        db = Database(path)
        self.assertEqual(db.list_vocab(), ["Only my term"])
        db.close()

    def test_secret_rotation_invalidates_process_cache(self):
        with patch.object(secrets, "_cred_read", side_effect=[b"first", b"second"]), \
             patch.object(secrets, "dpapi_unprotect", side_effect=lambda raw: raw):
            self.assertEqual(secrets.get_api_key(), "first")
            self.assertEqual(secrets.get_api_key(), "second")
        secrets._cached = None

    def test_pause_closes_assistant(self):
        app = Application()
        app.assistant = Mock()
        session = app.assistant
        with patch.object(app.overlay, "set_state"), patch.object(app, "_maybe_hide"):
            app.toggle_pause()
        session.close.assert_called_once_with("microphone paused")
        self.assertIsNone(app.assistant)

    def test_new_capture_waits_for_previous_processing(self):
        ctl = DictationController()
        ctl._processing = True
        self.assertFalse(ctl.begin())

    def test_shortcut_release_returns_without_waiting_for_transcription(self):
        app = Application()
        entered, release = threading.Event(), threading.Event()
        app.dictation.end = lambda: (entered.set(), release.wait(2))
        start = time.monotonic()
        app._on_hotkey(SimpleNamespace(action="dictate_release"))
        self.assertLess(time.monotonic() - start, .2)
        self.assertTrue(entered.wait(.5))
        release.set()

    def test_async_timeout_cancels_work(self):
        runner = AsyncRunner()
        cancelled = threading.Event()
        async def work():
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()
        with self.assertRaises(TimeoutError):
            runner.run(work(), .04)
        self.assertTrue(cancelled.wait(1))
        runner.stop()

    def test_speaker_resamples_native_fallback(self):
        bad = Mock()
        bad.start.side_effect = RuntimeError("rate refused")
        good = Mock()
        sd = SimpleNamespace(OutputStream=Mock(side_effect=[bad, good]),
                             query_devices=lambda *a: {"default_samplerate": 48000})
        with patch("jarvis.audio.playback._sd", return_value=sd):
            speaker = Speaker(rate=24000)
            speaker.start()
            speaker.play(b"\1\0" * 2400)
            self.assertAlmostEqual(speaker.queued_bytes / 2 / 48000, .1, delta=.001)
            bad.close.assert_called_once()
            speaker.stop()

    def test_final_session_close_releases_transport(self):
        async def run():
            session = live.LiveSession(live.LiveConfig())
            class Socket:
                closed = False
                async def close(self): self.closed = True
            sock = Socket()
            session._ws = sock
            session.closed = session.usage_final = True
            report = await session.close()
            self.assertTrue(sock.closed)
            self.assertTrue(report.complete)
        asyncio.run(run())

    def test_tools_are_advertised_without_screenshot_upload_claim(self):
        names = {s["name"] for s in function_schemas()}
        self.assertIn("open_app", names)
        self.assertIn("read_text_file", names)
        self.assertNotIn("screenshot", names)

    def test_delegation_returns_all_results_before_continuing(self):
        async def run():
            sent = []
            async def send(event): sent.append(event)
            owner = SimpleNamespace(cfg=config.DEFAULT_CONFIG, _stop=threading.Event(),
                app=SimpleNamespace(budget=Mock()), session=SimpleNamespace(_send=send))
            bridge = DelegatedTools(owner)
            bridge._run_call = lambda call: {"ok": True, "detail": "test fake only"}
            calls = [{"call_id": "one"}, {"call_id": "two"}, {"call_id": "one"}]
            await bridge._execute(calls)
            self.assertEqual([e["type"] for e in sent],
                ["response.item.create", "response.item.create", "response.create"])
            self.assertNotIn("delegation_id", sent[-1])
            self.assertNotIn("response", sent[-1])
        asyncio.run(run())

    def test_denial_does_not_execute_tools(self):
        owner = SimpleNamespace(cfg=config.DEFAULT_CONFIG, _stop=threading.Event(),
            app=SimpleNamespace(budget=Mock(), overlay=Mock()))
        bridge = DelegatedTools(owner)
        with patch.object(ctypes.windll.user32, "MessageBoxW", return_value=7), \
             patch("jarvis.core.tools.call") as call:
            result = bridge._run_call({"name": "task_status", "arguments": "{}"})
        self.assertFalse(result["ok"])
        call.assert_not_called()

    def test_native_overlay_button_message_is_dispatched(self):
        app = Application()
        got = threading.Event()
        app.overlay.on_action = lambda *args: got.set()
        app.overlay.reduced_motion = True
        state = overlay.PillState(state="approval", transcript="test", actions=["Copy"])
        app.overlay._advance(state, 1 / 60)
        app.overlay.show(state)
        rect = app.overlay._action_rects[0]
        x, y = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
        overlay.user32.PostMessageW(app.overlay._hwnd, 0x0201, 1, (y << 16) | x)
        timer = threading.Timer(.5, app._stop.set)
        timer.start()
        try:
            app._main_loop()
            self.assertTrue(got.is_set(), "real HWND message never reached Copy handler")
        finally:
            app.overlay.stop()


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(AuditTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"TOTAL {result.testsRun} PASSED {result.testsRun-len(result.failures)-len(result.errors)} "
          f"FAILED {len(result.failures)+len(result.errors)}")
    from jarvis.db import db
    db().close()
    import logging
    logging.shutdown()
    TEMP.cleanup()
    sys.exit(not result.wasSuccessful())
