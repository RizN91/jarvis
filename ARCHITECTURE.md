# Architecture

A map of how Jarvis is put together, and the handful of decisions that are
load-bearing. Read this before changing the threading or the audio path.

## The shape of it

```
                    ┌─────────────────────────── tray process ──────────────────────────┐
   mouse/kbd ──────▶│ hook thread ──▶ hotkey-dispatch thread ──▶ dictation state machine │
   (global hooks)   │                       │                          │                │
                    │                       │                    audio capture          │
                    │                       ▼                          │                │
                    │              asyncio thread ────────▶ OpenAI (Live / Transcribe)  │
                    │                       │                          │                │
                    │                       ▼                          ▼                │
                    │              overlay thread (the pill)      text insertion         │
                    │                                                                    │
                    │   session thread (lock/unlock/suspend)   main thread (tray icon)   │
                    └────────────────────────────────────────────────────────────────────┘
                                              │ config.json + data.db (shared, no IPC)
                    ┌─────────────────────────┴──────────────────────────────────────────┐
                    │ settings process — pywebview + WebView2, talks to Python via        │
                    │ window.pywebview.api only. Exit it and WebView2 goes with it.       │
                    └────────────────────────────────────────────────────────────────────┘
```

**There is no web server and no localhost port.** The tray and the settings
window are separate processes that share `config.json` and a SQLite database in
`%LOCALAPPDATA%\Jarvis`. The tray watches the config file's mtime and reloads on
change. This is deliberate: it removes an entire class of local attack surface,
and it keeps WebView2 (~440 MB of process tree) out of the always-on process.
The tray idles at **2 processes, ~53 MB, ~0.3 % of one core**.

## Threads, and why each exists

| Thread | Job | Notes |
| --- | --- | --- |
| main | tray icon message loop, nothing else | keeps the tray responsive |
| `hook` | low-level keyboard/mouse hooks | must return fast or Windows drops the hook |
| `hotkey-dispatch` | turns hook events into dictation actions | slow work never runs inside the hook |
| `overlay` | renders the pill | ~5 ms/frame, idles when hidden |
| `session` | WTS lock/unlock/suspend notifications | message-only window |
| `asyncio` | all cloud I/O | one loop, owned by `core/asyncrun.py` |

Audio callbacks are real-time and do nothing but copy bytes into a queue.

## Module map

```
jarvis/
  __main__.py      entry point, single-instance mutex, platform gate
  app.py           wiring: tray + overlay + hooks + engines + assistant session
  config.py        JSON settings under %LOCALAPPDATA%\Jarvis
  secrets.py       Windows Credential Manager (DPAPI). The ONLY place a key lives.
  db.py            SQLite: history, vocabulary, usage, tasks, audit
  logsetup.py      rotating logs + the mandatory secret redaction filter
  platform_support.py  is this OS supported, and why not
  audio/           capture, playback, resample, vad, wake (sherpa-onnx KWS)
  engines/         live (gpt-live-1 over raw WebSocket), transcribe, cleanup, errors
  win/             target detection, insertion, hotkeys, overlay, session, tray
  core/            asyncrun, cost/budget, pricing, dictation state machine,
                   commands (spoken), delegation (assistant tools), tools
  ui/              window.py (WebView2 host), bridge.py (the JS API), web/ (the UI)
```

## Decisions you should not casually undo

**Live voice speaks the WebSocket protocol directly.** The `openai` SDK version
we pin has no `client.live`, so `engines/live.py` talks to
`wss://api.openai.com/v1/live/sessions` itself. The contract is verified against
the official docs and recorded in `docs/OPENAI-API-NOTES.md`.

**Usage snapshots are cumulative — take the max, never sum.** Summing
`session.usage.updated` multiplies the bill. Final usage comes from
`session.closed`; if that never arrives, spend is marked *unconfirmed* rather
than guessed. The app's ceilings are its own; it cannot read your OpenAI account
budget, and it says so.

**Never press Enter. No exceptions, no configuration.** A dictation tool that
can press Enter can send a half-finished message. Line breaks are inserted as
CR-only for controls that drop LF; `VK_RETURN` is never synthesised.

**Silence is refused before upload.** Near-silent audio plus strong vocabulary
hints once produced a confident, fabricated transcript. `audio/vad.py` classifies
the buffer first and refuses it locally, for free.

**Clipboard restoration uses delayed rendering.** Restoring the clipboard too
early makes Chromium paste the *old* contents. The guard becomes the clipboard
owner and serves `WM_RENDERFORMAT`, which gives a definitive "the target has
consumed it" signal instead of a sleep-and-hope.

**Low-level hooks must be installed with `hMod = NULL`.** Passing a module handle
makes `SetWindowsHookExW` fail with error 126 and return a NULL hook — the app
then looks perfectly healthy while *every* shortcut is dead. There is a
regression test for this.

**Every window procedure declares its `argtypes`.** Without them ctypes marshals
`LPARAM` as a 32-bit int, throws `OverflowError` on every message, and ctypes
swallows it as "Exception ignored". A clean log proves nothing; the test suite
reads stderr for it.

## Performance

Measured on the reference machine (16 logical CPUs):

| Thing | Cost |
| --- | --- |
| Tray idle | 2 processes, ~53 MB RSS, ~0.3 % of one core |
| Pill render | ~5 ms/frame, and only while visible |
| Settings window | ~600 MB total, ~440 MB of it WebView2 — **only while open** |
| `gpt-transcribe` | $0.0045 per minute of audio |
| `gpt-live-1` | $0.05 per minute of session |

The pill's mask and border geometry are cached (`_MASK_CACHE`) because that work
was 51 % of every frame and only changes while the island is animating. Sphere and
glow renders are cached per colour.

## Testing

`tests/` holds ~490 assertions across 13 free suites plus three that spend real
money. Nothing is mocked away that matters: the insertion tests type into a real
Win32 edit control and a real WebView2 field; the overlay test opens a real
layered window and photographs the desktop; the UI tests boot a real WebView2 and
assert the twelve wizard steps fit without overflow.

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to run them.
