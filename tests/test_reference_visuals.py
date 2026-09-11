"""Reference-sized real WebView2 captures and native-renderer fixtures. No billing."""
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests import test_codex_ui as ui


def capture_preview(window, name):
    # Import .NET after pywebview has initialized it. Capture the WebView surface,
    # never the desktop: unrelated apps must not appear in test artifacts.
    from System import Action
    from System.IO import FileStream, FileMode, FileAccess
    from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
    stream = FileStream(str(ui.OUT / name), FileMode.Create, FileAccess.Write)
    jobs = []

    def begin():
        jobs.append(window.native.browser.webview.CoreWebView2.CapturePreviewAsync(
            CoreWebView2CapturePreviewImageFormat.Png, stream))

    try:
        window.native.Invoke(Action(begin))
        jobs[0].GetAwaiter().GetResult()
    finally:
        stream.Dispose()


def capture(window, name):
    capture_preview(window, name)
    if name == "setup-dark.png":
        ui.check("all twelve sidebar steps fit reference-height window", window.evaluate_js(
            "document.querySelector('#navList').scrollHeight <= document.querySelector('#navList').clientHeight + 1"))
        ui.check("welcome uses 104 fine waveform bars", window.evaluate_js(
            "document.querySelectorAll('.mic-strip .bar').length === 104"))
        ui.check("local generated hero is configured", window.evaluate_js(
            "getComputedStyle(document.querySelector('.hero'),'::before').backgroundImage.includes('jarvis-hero.png')"))
    if name == "setup-light.png":
        saved = ui.api.set_config({"vocab": ["the user reference test", "Jarvis"]})
        ui.check("vocabulary bridge writes real SQLite terms", saved.get("ok") and
                 set(ui.api.get_state()["vocab"]) == {"the user reference test", "Jarvis"})
        bad = ui.api.set_config({"vocab": [42]})
        ui.check("invalid vocabulary is rejected without deleting terms", not bad.get("ok") and
                 len(ui.api.get_state()["vocab"]) == 2)
        ui.api.set_config({"vocab": []})
        ui.check("empty vocabulary persists through the bridge", ui.api.get_state()["vocab"] == [])
        window.resize(860, 780)
        time.sleep(.3)
        capture_preview(window, "setup-narrow-light.png")
        for step in range(1, 13):
            ui.check(f"narrow step {step} actually renders", window.evaluate_js(
                f"document.querySelector('.wiz-count').textContent === 'Step {step} of 12'"))
            ui.check(f"narrow step {step} no horizontal overflow", window.evaluate_js(
                "document.querySelector('#view').scrollWidth <= document.querySelector('#view').clientWidth + 1"))
            if step < 12:
                window.evaluate_js("document.querySelector('[data-act=wiz-next]').click()")
                time.sleep(.15)


def native_states():
    from PIL import Image
    from jarvis.win import overlay as ov
    pill = ov.Overlay()
    states = [
        ov.PillState(state="sleeping"),
        ov.PillState(state="dictating", transcript="open my supplier invoices documents", esc_hint="ESC to stop"),
        ov.PillState(state="working", task="Opening Supplier Invoices", progress=.45,
                     detail="Processing your request", esc_hint="ESC to stop"),
        ov.PillState(state="listening", transcript="summarise the latest file",
                     detail="Controlling your PC", session_seconds=42, spend_usd=.0183),
        ov.PillState(state="listening", speaking=True, transcript="here is what I found in that folder",
                     detail="Generating response"),
        ov.PillState(state="error", detail="Something went wrong", esc_hint="ESC to dismiss"),
    ]
    sheet = Image.new("RGB", (pill.width, pill.height * len(states)), (9, 16, 26))
    for i, st in enumerate(states):
        for _ in range(150):
            pill._advance(st, 1 / 60)
        pill._level_smooth = .6
        frame = pill._compose(st, .6, 1.2)
        ui.check(f"native reference state {i+1} renders", frame.getbbox() is not None)
        if i:
            ui.check(f"native state {i+1} keeps orb centered", abs(pill._left - pill._right) < .5)
        sheet.paste(frame, (0, i * pill.height), frame)
    sheet.save(ui.OUT / "pill-reference-states.png")


if __name__ == "__main__":
    ui.host = ui.SettingsHost(api=ui.api, width=1340, height=944)
    ui.api.host = ui.host
    ui.capture = capture
    ui.host.start(ui.run)
    native_states()
    report = {"checks": len(ui.checks), "passed": sum(ui.checks),
              "failed": len(ui.checks)-sum(ui.checks), "billing_usd": 0,
              "capture": "CoreWebView2.CapturePreviewAsync + native Pillow renderer",
              "scope": "Visual fixtures, wizard navigation, responsive overflow, isolated vocabulary bridge"}
    (ui.OUT / "reference-checks.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)
    from jarvis.db import db
    db().close()
    logging.shutdown()
    ui.PROFILE.cleanup()
    sys.exit(0 if all(ui.checks) else 1)
