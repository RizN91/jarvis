"""Jarvis — Windows dictation + GPT-Live assistant.

A local, single-process Windows tray application.
Package layout:
    config      persistent settings (JSON, atomic writes)
    secrets     Windows Credential Manager storage (never plaintext)
    db          local SQLite: memory, vocabulary, usage, tasks
    logsetup    rotating logs with secret redaction
    audio/      capture, playback, local wake word
    engines/    gpt-live-1 (Live), gpt-transcribe (Economy), text cleanup
    win/        global hooks, layered overlay, text insertion, tray
    core/       state machine, cost accounting, typed computer tools
    ui/         setup wizard + settings (WebView2 via pywebview JS bridge)
"""

__version__ = "1.0.0"
# APP_NAME is the DISPLAY name. APP_ID is the on-disk identity (the
# %LOCALAPPDATA% folder, the Credential Manager target and the single-instance
# mutex) and must NOT be made to follow a display-name change: it is what
# the user's data and stored key are keyed on.
APP_NAME = "Jarvis"
APP_ID = "Jarvis"
