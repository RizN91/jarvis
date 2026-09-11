<div align="center">

<img src="docs/images/hero.webp" alt="Jarvis — a floating glass pill that shows what you said and what it's doing" width="820">

# Jarvis

**Talk to your PC. It listens, answers, and types for you.**

Hold a button, speak, release — your words appear at the cursor in whatever app
is focused. Or open a voice conversation that can actually *do* things:
search the web, open your apps, run tasks on your machine.

No subscription. Bring your own OpenAI key. **From $0.0045 per minute.**

[![License: MIT](https://img.shields.io/badge/License-MIT-3b82f6.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3b82f6.svg)](pyproject.toml)
[![Platform: Windows 10/11](https://img.shields.io/badge/platform-Windows%2010%2F11-3b82f6.svg)](#platform-support)
[![Tests: 480+ passing](https://img.shields.io/badge/tests-489%20passing-22c55e.svg)](CONTRIBUTING.md)
[![Languages: 15](https://img.shields.io/badge/languages-15-8b5cf6.svg)](#speaks-your-language)

**Read this in:** English · [Español](README.es.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Português (BR)](README.pt-BR.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md)

</div>

---

## Watch it work

[![Jarvis — the floating pill, and real dictation landing in Notepad](docs/media/jarvis-preview.webp)](docs/media/jarvis-demo.mp4)

*A real session, not a mockup.* The pill and the text arriving in Notepad are the
shipped code running — the clip is generated from the app itself, so it cannot
quietly drift from what you download.

**[▶ Watch the 81-second demo with sound](docs/media/jarvis-demo.mp4)** · 1080p, 11 MB

---

## Why

You think faster than you type. You also repeat yourself all day — the same
email shape, the same commit message, the same summarise-this-document request.

Dictation tools solve half of that and are either a subscription, or a toy, or
they silently press Enter and send your half-finished message.

Jarvis does both halves, costs pennies, and stays out of the way. It has no
subscription, no account, no telemetry, and no server of its own.

<table>
<tr>
<td width="50%">

**Say it, and it's typed**
> *"Hi Sarah — following up on the invoice from Tuesday, could you confirm the
> PO number so I can get this processed today?"*

…lands in your email, your editor, your ticket, wherever the cursor is.

</td>
<td width="50%">

**Or ask for something**
> *"Jarvis, what's the latest on the Vue 3.6 release?"*

…it searches, then answers out loud, in a conversation you can talk over.

</td>
</tr>
</table>

## Screenshots

<div align="center">

<img src="docs/images/pill-states.webp" alt="The Jarvis pill in all six states: idle, dictating, working, listening, speaking, error" width="900">

<sub>The pill in all six states — idle, dictating, working, listening, speaking, error.
It never steals focus, and it idles when nothing is happening.</sub>

<br><br>

<img src="docs/images/settings-dark.webp" alt="Jarvis setup wizard, dark theme" width="880">

<sub>Twelve-step onboarding: it checks your microphone, speakers, shortcuts and key
before you rely on it.</sub>

<br><br>

<img src="docs/images/settings-light.webp" alt="Jarvis settings, light theme" width="880">

<sub>Every screen has a light theme, and the whole UI is translated — see below.</sub>

</div>

## Install

**Windows 10 or 11.** You need Python 3.11+ and an OpenAI API key.

One line in PowerShell is the whole install:

```powershell
irm https://raw.githubusercontent.com/RizN91/jarvis/main/install.ps1 | iex
```

It finds your Python, clones Jarvis into `%LOCALAPPDATA%\Jarvis\app`, builds the
virtual environment, installs the pinned dependencies, runs a smoke check, and
adds a Start Menu shortcut.

It is a per-user install: it never elevates, never installs Python for you, and
never changes your execution policy, your Defender settings or your global
Python. `-DryRun` prints exactly what it would do and changes nothing;
`-Uninstall` removes it again, and will not touch your data folder without a
typed confirmation.

**Or by hand** — the same three commands the script runs:

```bat
git clone https://github.com/RizN91/jarvis.git
cd jarvis
setup.cmd
```

`setup.cmd` finds a suitable Python, creates a virtual environment, installs the
pinned dependencies, and runs a smoke check. Then:

```bat
run.cmd
```

The first launch opens the setup wizard. It walks you through your microphone,
speakers, shortcuts, wake word and budget, and asks for your API key — which is
stored in **Windows Credential Manager**, never in a file.

Nothing is sent anywhere until you finish the wizard and start dictating, apart
from the connection tests you explicitly choose to run.

Prefer to do it by hand?

```bat
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m jarvis
```

## What it costs

You bring your own OpenAI key, so you pay OpenAI directly — no markup, no
subscription, no seat licence. Two engines, and the choice is yours:

| Engine | Model | Cost | Feels like |
| --- | --- | --- | --- |
| **Economy** *(default for dictation)* | `gpt-transcribe` | **$0.0045 / min** | about **27 hours per dollar**; the most accurate for verbatim text |
| **Live** | `gpt-live-1` | **$0.05 / min** | about 20 minutes per dollar; conversational, handles barge-in and turns |

For scale: **twenty minutes of dictation a day, five days a week, costs about
45 cents a month** on Economy. The assistant is the expensive part, and only
while you are actually talking to it.

Jarvis also meters its own spend and stops at the daily and monthly ceilings you
set. Those ceilings are Jarvis's own counters — it cannot read your OpenAI account
budget, and it never claims to.

## Features

**Dictation**
- Types into **any** app — editors, browsers, chat, terminals, ticketing systems.
- **Never presses Enter**, so it cannot send a half-written message.
- Refuses password fields; holds text for review in terminals rather than
  auto-typing into a shell.
- Your own **vocabulary** — names, jargon, product names — passed to the
  transcriber as hints, which measurably improves accuracy.
- Undo that will not eat your own typing.

**Voice assistant**
- `Ctrl+Alt+Space` opens a conversation with real barge-in — talk over it and it
  stops.
- It can search the web and act on your machine through a tool layer where every
  action needs **your explicit Yes**, with the exact arguments shown.
- Local coding agents (Codex, Claude Code) can be handed long jobs, and Jarvis
  reports which one it used.

**Always**
- **Local wake words** — "Hey Jarvis", "Hey GPT". Runs on your machine
  (`sherpa-onnx`); **no audio leaves the device while it is listening** for the
  wake phrase.
- **The pill.** A per-pixel-alpha floating overlay that shows what it heard and
  what it is doing, and is genuinely click-through and non-activating.
- **Privacy by construction** — no telemetry, no cloud database, no analytics, no
  account. Raw audio is not stored.
- Light and dark themes. Reduced-motion support.

## Speaks your language

The entire setup and settings UI ships in **15 languages**, with a switcher in
Settings:

`English` · `Español` · `Français` · `Deutsch` · `Italiano` · `Nederlands` ·
`Polski` · `Português (BR)` · `Русский` · `Türkçe` · `العربية` (RTL) ·
`हिन्दी` · `中文 (简体)` · `日本語` · `한국어`

Your language is detected from your system on first run. Missing a language?
It is one file and a pull request — see [CONTRIBUTING.md](CONTRIBUTING.md).

## Platform support

**Windows 10 and 11 are fully supported.** The core is built on Win32 — global
input hooks, `SendInput` text insertion, layered windows for the pill, DPAPI for
the key, WTS for lock/suspend detection. That is what makes it feel native, and
it is also why it is not yet portable.

**macOS and Linux are not supported yet.** Running there exits with a clear
message rather than a traceback. A port is on the roadmap; it is a real project,
not a flag.

## Roadmap

What is coming next, roughly in order:

- [ ] **More models, not just OpenAI** — Claude and Gemini as the assistant's
      brain, and local models via Ollama for a fully offline mode.
- [ ] **Better voices** and a voice picker, including your own voice profile.
- [ ] **Themes and skins** — more than light/dark, and a pill that matches your
      desktop.
- [ ] **True streaming dictation** — today Live records then replays, so
      release-to-result includes the length of what you said.
- [ ] **macOS port** (Accessibility API + Quartz) and **Linux** (X11).
- [ ] **Plugins** — let people add their own spoken commands and tools.
- [ ] **A signed installer**, so SmartScreen stops warning.

Ideas, votes and complaints are welcome in
[Issues](https://github.com/RizN91/jarvis/issues) — the roadmap follows what
people actually ask for.

## How it works

A short version; the real detail is in [ARCHITECTURE.md](ARCHITECTURE.md).

- A tiny tray process owns global hooks, audio and the pill. It idles at
  **2 processes, ~53 MB and ~0.3 % of one core**.
- The settings window is a **separate** process, so WebView2 (~440 MB) exists
  only while you have it open.
- Speech goes straight to OpenAI over a WebSocket. There is **no Jarvis server
  and no localhost port** — nothing to attack, nothing to go down.
- Your data (history, vocabulary, spend) is SQLite on your own disk. Nothing is
  synced anywhere.

## Security and privacy

- Your API key is stored **only** in Windows Credential Manager, DPAPI-wrapped,
  and never written to a config file, database, log, command line or the UI.
- A redaction filter scrubs key-shaped strings from every log record.
- **No telemetry. No analytics. No account.**
- Raw audio is not written to disk.
- Startup at login is opt-in and reversible; uninstall removes everything.

See [SECURITY.md](SECURITY.md) for how to report a vulnerability.

## Contributing

Bug reports, translations, themes and new tool verbs are all welcome — start
with [CONTRIBUTING.md](CONTRIBUTING.md). It explains the test suites, how to add
a language, and the handful of rules that have already prevented real bugs (no
secrets in logs, never synthesise Enter, never let dictated text become
instructions).

The test suite is ~486 assertions across 13 free suites, and it types into real
applications rather than mocks. There is a lot to build if you want to help.

## License

[MIT](LICENSE) — do what you like with it.

Dependency and model licences are listed in
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md), including one honest caveat
about the wake-word model's terms that is worth reading before any commercial
use.

Jarvis is not affiliated with, endorsed by, or sponsored by OpenAI. You supply
your own API key and are responsible for your usage.

---

<div align="center">

**If Jarvis saves you time, a ⭐ helps other people find it.**

</div>
