# Installing Jarvis

Jarvis is a Windows tray application. There is **no build step and no web
server** — it is a plain Python package that runs as `python -m jarvis`.

---

## Platform support

| Platform | Status |
| --- | --- |
| **Windows 10 / 11, 64-bit** | **Fully supported.** This is the only platform the app is built and tested for. |
| **macOS** | **Not supported yet.** `python -m jarvis` exits with a clear message instead of a traceback. |
| **Linux** | **Not supported yet.** Same. |

The core is genuinely Windows-specific, not merely untested elsewhere: global
input hooks (`SetWindowsHookEx`), synthetic text insertion (`SendInput`),
transparent always-on-top overlay windows (`UpdateLayeredWindow`), per-user
secret storage (Windows Credential Manager + DPAPI), WTS session notifications
and the pystray/WebView2 tray + settings host are all Win32-only here.

A port needs a platform layer implementing each of those (an X11/Wayland or
macOS equivalent for the hooks, insertion and overlay, plus a Keychain-based
secret store). The settings web UI is portable in principle. `setup.sh` on
macOS/Linux deliberately **does not pretend to install**: it checks for a
Python 3.11+ interpreter, prints this position, and exits non-zero. The runtime
check lives in `jarvis/platform_support.py`.

---

## 1. Prerequisites

| Requirement | Notes |
| --- | --- |
| **Windows 10 or 11, 64-bit** | 32-bit Python is not supported (WebView2 and sherpa-onnx ship 64-bit binaries). |
| **CPython 3.11 or newer, 64-bit** | 3.11.x is what this build was validated against (`Python 3.11.16`). Get it from <https://www.python.org/downloads/> and tick **“Add python.exe to PATH”** in the installer. |
| **Microsoft Edge WebView2 Runtime** | Used for the Settings / setup-wizard window. **Ships with Windows 11.** On Windows 10 it is usually present via Edge; if not, install the Evergreen Bootstrapper from <https://developer.microsoft.com/microsoft-edge/webview2/>. |
| **A working microphone** | WASAPI capture via `sounddevice`/PortAudio. |
| **An OpenAI API key** | Required for both dictation engines. The key is stored in Windows Credential Manager, never in a file. |
| *Optional:* Microsoft Visual C++ 2015-2022 Redistributable | Present on virtually all Windows installs; needed by some native wheels. |

No administrator rights are required for any step below. Nothing installs to
`Program Files`, and no global Python environment is modified.

---

## 2. Running from source

### 2.1 One-time setup

```bat
cd C:\path\to\jarvis-voice
setup.cmd
```

`setup.cmd` creates a per-user virtual environment at `.venv\` and installs the
exact pinned versions from `requirements.lock.txt`. It is **idempotent** — run
it as often as you like. It never elevates, never touches your global Python,
never changes a PowerShell execution policy, and never changes a Defender
setting.

If you prefer to do it by hand:

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
```

### 2.2 Launch

```bat
run.cmd
```

`run.cmd` cd's to its own folder, prefers `.venv\Scripts\pythonw.exe` if it
exists, otherwise falls back to `pythonw` on `PATH`, checks that the package
and its entry point import, and then starts the app **without a console
window**.

For troubleshooting, use the console variant — same checks, but the process
stays in the foreground and every log line and traceback is visible:

```bat
run-console.cmd
```

### 2.3 Equivalent manual commands

```bat
.venv\Scripts\python.exe -m jarvis          :: tray app, console visible
.venv\Scripts\pythonw.exe -m jarvis         :: tray app, no console
```

### 2.4 First run

The app opens the setup wizard the first time (Settings → **Setup**), which
walks through: API key entry, microphone selection, dictation engine choice
(**Live** is the default), hotkey recording, and the daily/monthly budget. The
wizard writes `config.json` under `%LOCALAPPDATA%\Jarvis`.

---

## 3. Building the portable release

```bat
package.cmd
```

* It builds a **windowed (no console), one-folder** release from
  `installer\jarvis.spec` into `dist\Jarvis\`.
* The entry point is `dist\Jarvis\Jarvis.exe`. Ship the **whole**
  `Jarvis` folder — the exe needs `_internal\` beside it.
* The user of a packaged build does **not** need Python, but **does** need the
  WebView2 runtime (present on Windows 11).

`package.cmd` deliberately **does not install PyInstaller**. If PyInstaller is
missing it prints the exact command and exits with a non-zero status without
pretending anything was built:

```bat
.venv\Scripts\python.exe -m pip install pyinstaller
package.cmd
```

Under the hood it is just:

```bat
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean installer\jarvis.spec
```

> **Asset path contract.** The spec bundles `jarvis/ui/web/` to
> `jarvis/ui/web` inside the bundle. That is exactly where
> `jarvis.ui.window.web_root()` looks under `sys._MEIPASS`. If you change
> one side, change the other, or the settings window will fail to load.

---

## 4. Where the API key lives

**Windows Credential Manager — never a file, never a log, never a config
value.**

* Credential target name: `Jarvis/openai_api_key`
* The key is additionally wrapped with DPAPI (`CryptProtectData`) before it is
  handed to `CredWriteW`, so even a raw credential-blob dump is not directly
  readable, and reads get an integrity check.
* Inspect it yourself (Windows Credentials):
  `Control Panel → User Accounts → Credential Manager → Windows Credentials`,
  or from a shell: `cmdkey /list:Jarvis/openai_api_key`
* Delete it: `cmdkey /delete:Jarvis/openai_api_key`

If — and only if — Credential Manager is unavailable, the app falls back to a
DPAPI-protected file at `%LOCALAPPDATA%\Jarvis\secret.dpapi` with a
restricted ACL, and records that degraded mode in the log. Uninstall removes
both.

Nothing in the app ever writes the key to `config.json`, the SQLite database,
the log file, the UI bundle, or the command line. The log filter
(`jarvis/logsetup.py`) redacts `sk-…`, `ek_…`, `Bearer …`, and
`authorization:`/`api_key:` shaped strings on **every** record before it
reaches disk.

---

## 5. What lives in `%LOCALAPPDATA%\Jarvis`

`JARVIS_DATA_DIR` overrides the location (the older `BV_DATA_DIR` still works
as a deprecated fallback) — this is what the test suite uses to run against a
throwaway directory.

| Path | Contents |
| --- | --- |
| `config.json` | All settings: dictation engine, assistant model, hotkeys, wake-word options, budgets, vocabulary, UI theme. Written atomically. A corrupt file is renamed to `config.json.corrupt` rather than silently discarded. |
| `jarvis.db` | SQLite database: dictation history, vocabulary, spend meter/usage ledger, assistant tasks. |
| `logs\jarvis.log` | Rotating log (2 MB × 3 backups). Secrets are redacted before writing. |
| `models\` | Downloaded wake-word models, e.g. `models\kws\sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01\`. |
| `audio\` | Scratch space for the current utterance only. Purged on start; not retained unless `store_raw_audio` is enabled. |
| `secret.dpapi` | Only present in the Credential-Manager-unavailable fallback case. |

Raw audio is **not** stored by default (`store_raw_audio: false`,
`retain_audio_files: false`) and telemetry is **off**.

---

## 6. Uninstalling

Preview exactly what would be removed, deleting nothing:

```bat
uninstall.cmd /dryrun
```

Then, to actually remove it:

```bat
uninstall.cmd
```

It asks you to type `YES` (capitals) before deleting anything; anything else
aborts. It removes only:

1. `.venv\` — the local virtual environment.
2. `%LOCALAPPDATA%\Jarvis` — config, logs, SQLite DB, models, audio
   scratch, DPAPI fallback.
3. The Windows Credential Manager entry
   `Jarvis/openai_api_key`:

   ```bat
   cmdkey /delete:Jarvis/openai_api_key
   ```

It does **not** touch your Python installation, does not need administrator
rights, and does not delete the source folder. If you enabled “start at login”,
also delete
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Jarvis.lnk`.

---

## 7. Troubleshooting

### “No microphone detected”

* Windows → Settings → Privacy → Microphone: allow desktop apps to access the
  microphone, then restart the app.
* Windows → Settings → System → Sound: confirm an input device is enabled and
  is not disabled in Device Manager.
* Open the app's Settings → Audio and pick an explicit input device instead of
  “System default”.
* Check the device list the app sees:

  ```bat
  .venv\Scripts\python.exe -c "import sounddevice as sd; print(sd.query_devices())"
  ```

* If `sounddevice` raises `PortAudioError`, another application has exclusive
  control of the device — close it (Teams/Zoom/OBS) and retry.

### The settings window is blank, or “WebView2 missing”

The Settings/setup window is a WebView2 host. On Windows 11 the runtime is
already present. On Windows 10, install the **Evergreen WebView2 Runtime**
bootstrapper from
<https://developer.microsoft.com/microsoft-edge/webview2/>. Verify with
`reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" /v pv`
(a `pv` value means it is installed).

### The app will not start at all

Run the console launcher and read the output — this is always the first step:

```bat
run-console.cmd
```

`run.cmd`/`run-console.cmd` tell you which of three things failed: no Python
interpreter, the `jarvis` package cannot be imported, or the
`jarvis.__main__` entry point is missing. If dependencies are the problem,
re-run `setup.cmd` (safe to repeat), then check the package compiles:

```bat
.venv\Scripts\python.exe -m compileall -q jarvis
```

### Where is the log, and is it safe to share?

`%LOCALAPPDATA%\Jarvis\logs\jarvis.log` (rotated to
`jarvis.log.1`, `.2`, `.3`). It is safe to share: every record passes
through the redaction filter, so `sk-…` keys, `ek_…` ephemeral secrets,
`Bearer` tokens, and `authorization:`/`api_key:` values are replaced with
`[REDACTED]` before being written. That filter is a hard requirement — do not
weaken it.

Control Panel → System → Advanced → Environment Variables has nothing to do
with Jarvis; the app reads `LOCALAPPDATA` and nothing else.

### Tests

```bat
.venv\Scripts\python.exe tests\test_scripts.py     :: free, packaging smoke test
```

**Billable** (they call the OpenAI API and cost real money) —
`tests\test_live_api.py`, `tests\test_transcribe_api.py`,
`tests\test_mic_loopback.py`. Every other test is free. See `AGENTS.md`.
