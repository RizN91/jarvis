"""Tray icon and menu.

Menu required by the build spec section 3:
    Pause microphone, Disable wake word, Start dictation, Start assistant,
    Settings, Quit.

The tray is a real Shell_NotifyIcon via pystray. The icon is generated at
runtime (the same orb emblem the pill uses) so there is no binary asset to
version, and it reflects the current state colour.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from PIL import Image, ImageDraw

from ..logsetup import get as _log

log = _log("tray")

STATE_COLOURS = {
    "sleeping": (110, 124, 156),
    "dictating": (77, 124, 255),
    "listening": (77, 124, 255),
    "thinking": (120, 96, 240),
    "working": (25, 211, 197),
    "approval": (251, 191, 36),
    "error": (248, 113, 113),
    "muted": (120, 130, 150),
}


def make_icon(state: str = "sleeping", size: int = 64) -> Image.Image:
    """Generate the tray emblem: a glowing orb with a small waveform."""
    colour = STATE_COLOURS.get(state, STATE_COLOURS["sleeping"])
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = 3
    # soft halo
    for i in range(6, 0, -1):
        a = int(14 * (1 - i / 7.0)) + 3
        r = size // 2 - pad - i
        if r > 2:
            d.ellipse([size // 2 - r, size // 2 - r, size // 2 + r, size // 2 + r],
                      outline=colour + (a,))
    d.ellipse([pad, pad, size - pad - 1, size - pad - 1],
              fill=(13, 18, 34, 255), outline=colour + (220,), width=2)
    inner = size // 2 - pad - 6
    d.ellipse([size // 2 - inner, size // 2 - inner,
               size // 2 + inner, size // 2 + inner],
              fill=(20, 30, 60, 255), outline=colour + (90,), width=1)
    # waveform
    bars = 5
    step = max(3, (inner * 2) // (bars + 1))
    for i in range(bars):
        dist = abs(i - (bars - 1) / 2) / ((bars - 1) / 2)
        h = int(inner * (1.0 - 0.5 * dist))
        x = size // 2 - (bars - 1) * step // 2 + i * step
        d.rounded_rectangle([x - 1, size // 2 - h // 2, x + 1, size // 2 + h // 2],
                            radius=1, fill=(255, 255, 255, 235))
    return img


class Tray:
    """Thin wrapper so the app does not have to know about pystray."""

    def __init__(self,
                 on_start_dictation: Callable[[], None],
                 on_toggle_assistant: Callable[[], None],
                 on_toggle_pause: Callable[[], None],
                 on_toggle_wake: Callable[[], None],
                 on_settings: Callable[[], None],
                 on_emergency_stop: Callable[[], None],
                 on_quit: Callable[[], None],
                 tooltip: str = "Jarvis"):
        self.on_start_dictation = on_start_dictation
        self.on_toggle_assistant = on_toggle_assistant
        self.on_toggle_pause = on_toggle_pause
        self.on_toggle_wake = on_toggle_wake
        self.on_settings = on_settings
        self.on_emergency_stop = on_emergency_stop
        self.on_quit = on_quit
        self.tooltip = tooltip

        self._icon = None
        self._thread: Optional[threading.Thread] = None
        self.paused = False
        self.wake_enabled = False

    # ---------------------------------------------------------------- build
    def _menu(self):
        import pystray
        return pystray.Menu(
            pystray.MenuItem(
                lambda item: "Resume microphone" if self.paused else "Pause microphone",
                lambda: self.on_toggle_pause(), default=False),
            pystray.MenuItem(
                "Disable wake word" if self.wake_enabled else "Enable wake word",
                lambda: self.on_toggle_wake()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Start dictation", lambda: self.on_start_dictation()),
            pystray.MenuItem("Start assistant", lambda: self.on_toggle_assistant()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Stop everything", lambda: self.on_emergency_stop()),
            pystray.MenuItem("Settings…", lambda: self.on_settings()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: self.on_quit()),
        )

    def start(self) -> bool:
        try:
            import pystray
        except ImportError:
            log.error("pystray is not installed; the tray is unavailable")
            return False
        try:
            self._icon = pystray.Icon("Jarvis", make_icon("sleeping"),
                                      self.tooltip, self._menu())
        except Exception as exc:
            log.error("tray creation failed: %s", exc)
            return False
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        try:
            self._icon.run()
        except Exception as exc:
            log.error("tray loop failed: %s", exc)

    def set_state(self, state: str) -> None:
        if not self._icon:
            return
        try:
            self._icon.icon = make_icon(state)
            label = state.capitalize()
            if self.paused:
                label += " (muted)"
            self._icon.title = f"{self.tooltip} — {label}"
            self._icon.update_menu()
        except Exception:
            pass

    def notify(self, title: str, message: str) -> None:
        if not self._icon:
            return
        try:
            self._icon.notify(message, title)
        except Exception:
            # notify() needs a WinRT/balloon backend; failing is not fatal.
            pass

    def stop(self) -> None:
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
