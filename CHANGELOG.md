# Changelog

All notable changes to Jarvis are documented here. This project follows
[Semantic Versioning](https://semver.org/) and
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0] — 2026-09-11

The first public release.

### Added

- **Dictation that types into any Windows app.** Hold a mouse side button (or tap
  `F8`), speak, release — the words are inserted at the caret in whatever has
  focus. Never presses Enter. Never types into a password field.
- **A voice assistant with real barge-in.** `Ctrl+Alt+Space` opens a GPT-Live
  conversation you can talk over. It can search the web and act on the machine
  through an approval-gated tool layer.
- **The floating pill.** A per-pixel-alpha, non-activating overlay that shows what
  the app heard and what it is doing, without ever stealing focus. Six states,
  animated, ~5 ms of CPU per frame.
- **Two dictation engines, and the choice is yours:**
  - *Economy* — `gpt-transcribe`, **$0.0045/min** (about 27 hours per dollar).
  - *Live* — `gpt-live-1`, **$0.05/min**, faster to feel and better at handling
    conversation.
- **Local wake words** ("Hey Jarvis", "Hey GPT") via `sherpa-onnx`. Runs entirely
  on your machine; no audio leaves the device while the app is listening for the
  wake phrase.
- **Spoken commands** — "open Chrome", "search for …", "type …" — matched locally
  by exact leading verb, so ordinary dictation is never mistaken for a command.
- **Your vocabulary.** Teach it names, jargon and product terms; they are passed
  as hints to the transcriber and measurably improve accuracy.
- **Memory and history**, stored locally in SQLite.
- **Budgets and spend metering** with local daily/monthly ceilings.
- **Fifteen languages** in the setup and settings UI, with a language switcher.
- **A twelve-step onboarding wizard** that checks your microphone, speakers,
  shortcuts and key before you rely on it.

### Security

- The API key is stored only in **Windows Credential Manager**, wrapped with
  DPAPI. It is never written to a config file, a database, a log, a command line
  or the UI bundle. A redaction filter scrubs key-shaped strings from every log
  record.
- Destructive tool verbs are deliberately absent. Every action that could change
  your machine passes an explicit, native, default-***No*** confirmation that
  shows the exact arguments.
- No network listener: the settings window and the tray app communicate only
  through a shared data directory, so there is no localhost port to attack.

### Known limitations

These are honest, and they are the top of the roadmap:

- **Windows only.** The core is built on Win32 (global hooks, `SendInput`, layered
  windows, DPAPI, WTS session notifications). macOS and Linux are not supported
  yet; running there fails with a clear message rather than a traceback.
- **Accuracy on your own voice is not benchmarked.** The numbers in the docs come
  from synthetic speech plus a built-in calibration screen; measure it yourself.
- **Live dictation records, then replays.** It is not yet true streaming during
  capture, so release-to-result includes the length of what you said. Economy
  returns sooner.
- **No signed installer.** `package.cmd` builds a PyInstaller bundle; there is no
  code-signed `.exe` yet, so Windows SmartScreen will warn.
