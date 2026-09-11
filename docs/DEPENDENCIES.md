# DEPENDENCIES.md — Jarvis dependency & licence inventory

**Generated:** 2026-09-11
**Method:** `python -m pip list --format=freeze` for versions, then
`importlib.metadata` (+ the package's own `METADATA` / `LICENSE` file in
`site-packages`) for licences. **Nothing below is from memory**; where a licence could
not be established from the installed artifact it says **NOT DETERMINED** instead of
guessing.

**Interpreter / prefix (probed on this machine):**
- Python `3.11.16 (main, Sep 1 2026, 14:15:24) [MSC v.1944 64 bit (AMD64)]`
- `sys.prefix = C:\Users\user\AppData\Local\hermes\hermes-agent\venv`
- `python -m pip list --format=freeze` returned **147** installed distributions.

---

## 1. Direct dependencies

Versions are the exact installed values from `pip list --format=freeze`. "Licence" is
what the installed distribution metadata reports; the "Source" column says where that
was read from.

| Package | Installed | Used for in this app | Licence | Source of licence |
| --- | --- | --- | --- | --- |
| `openai` | **2.24.0** | File transcription (`gpt-transcribe`) via `client.audio.transcriptions`. **Has no `client.live`** — see VERIFIED_API.md §4.3. | **Apache-2.0** | `METADATA` `License` + classifier `License :: OSI Approved :: Apache Software License` |
| `websockets` | **15.0.1** | The GPT-Live primary WebSocket (`wss://api.openai.com/v1/live/sessions`) spoken directly (`engines/live.py`). | **BSD-3-Clause** | `METADATA` `License` + classifier; ships `LICENSE` |
| `httpx` | **0.28.1** | Transport for `openai`. **Not imported by app code** (see §4). | **BSD-3-Clause** | `METADATA` `License` + classifier |
| `sounddevice` | **0.5.5** | Microphone capture and audio playback; PortAudio callback threads (`audio/capture.py`, `audio/playback.py`, `audio/wake.py`). | **MIT** | `METADATA` `License-Expression: MIT`; ships `LICENSE` (© Matthias Geier) |
| `numpy` | **2.4.3** | All audio DSP: resampling, VAD energy math, ring buffers. Used in 13 source files. | **BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0** | `METADATA` `License-Expression` (multi-licence; full text in `numpy/LICENSE.txt`) |
| `pywin32` | **311** | Win32 API layer: window/target inspection, text insertion, hotkeys, session/desktop control. | **PSF** (Python Software Foundation License) | `METADATA` `License: PSF` + classifier `License :: OSI Approved :: Python Software Foundation License` |
| `comtypes` | **1.4.16** | Installed, but **not referenced anywhere in app code** (grep found zero occurrences) — see §4. | **MIT** | `METADATA` `License-Expression: MIT`; ships `LICENSE.txt` |
| `pillow` | **12.3.0** | Rendering the floating status pill / overlay frames and tray icons (`PIL` in 3 source files). | **MIT-CMU** (aka the PIL Software License) | `METADATA` `License-Expression: MIT-CMU`; ships `LICENSE` |
| `pystray` | **0.19.5** | System-tray icon and menu (`win/tray.py`). | **LGPLv3** | `METADATA` `License: LGPLv3` + classifier `License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)`; ships `COPYING`, `COPYING.LGPL` |
| `pywebview` | **6.2.1** | The WebView2 settings window and its Python↔JS bridge (`ui/window.py`). | **BSD-3-Clause** | `METADATA` `License` (full BSD 3-clause text) + classifier |
| `sherpa-onnx` | **1.13.4** | **Optional** offline wake-word detection (KWS) via ONNX. Model picks int8 encoder + int8 joiner + fp32 decoder (`audio/wake.py`). | **Apache-2.0** | `METADATA` `License: "Apache licensed, as found in the LICENSE file"`; ships `LICENSE` |
| `sherpa-onnx-core` | **1.13.4** | Native runtime backing `sherpa-onnx` (separate installed wheel). | **Apache-2.0** | `METADATA` `License: Apache-2.0` |
| `pythonnet` | **3.1.0** | Needed by pywebview on Windows to host WebView2 via the CLR. | **MIT** | `METADATA` `License-Expression: MIT`; ships `LICENSE` |
| `clr_loader` | **0.3.1** | pythonnet's CLR loader (dependency of pythonnet). | **MIT** | `License-Expression` was absent; read from the shipped file `clr_loader-0.3.1.dist-info/licenses/LICENSE` → "MIT License" |
| `onnxruntime` | **1.27.0** | Inference runtime pulled in by `sherpa-onnx` for the KWS model. | **MIT** | `METADATA` `License: "MIT License"` + classifier |

Direct-dependency count: **15** packages (13 app-facing + `onnxruntime` and
`httpx`, which are present as the runtimes of `sherpa-onnx` and `openai`
respectively).

---

## 2. Required vs optional

**Required for the app's core dictation function:**

- `openai` — the Economy path (`gpt-transcribe`).
- `websockets` — the Live path (`gpt-live-1`).
- `sounddevice` — microphone capture + playback.
- `numpy` — audio math throughout.
- `pywin32` — all Windows integration (targets, insertion, hotkeys).
- `pillow` — overlay/pill rendering.
- `pywebview` — the settings window (with `pythonnet` + `clr_loader` on Windows).
- `httpx` — `openai`'s HTTP transport.

**Optional / feature-gated:**

- `sherpa-onnx` (**+ `sherpa-onnx-core` + `onnxruntime`**) — **wake word only.**
  `config.py` ships `"wake_enabled": False` and `"push_to_talk_only": False`, and
  `audio/wake.py` treats a missing model/package as "wake unavailable"). See
  `docs/WAKE_WORD_NOTES.md`: "The app can therefore stay push-to-talk-only; nothing else
  in the app needs to change."
- `pystray` — tray icon. Imported lazily and failure-tolerant
  (`win/tray.py::start` logs "pystray is not installed; the tray is unavailable" and
  returns `False`); the app runs without it.

**Not required (present, but unused by app code):**

- `comtypes` — installed but never imported by `jarvis/` or `tests/`.

---

## 3. Vendored / non-Python runtime components

- **PortAudio is bundled inside the `sounddevice` wheel.** Confirmed on disk:
  `.../site-packages/_sounddevice_data/portaudio-binaries/libportaudio64bit.dll`
  (and `libportaudio64bit-asio.dll`), alongside that bundle's `README.md`, which
  describes them as "pre-compiled dynamic libraries for PortAudio". No separate
  PortAudio install is needed. **PortAudio's own licence is NOT DETERMINED from the
  wheel** — no `LICENSE`/`COPYING` file ships next to the bundled DLL, so this file does
  not assert one. (`sounddevice` itself, the Python wrapper, is MIT.)
- **WebView2 is a Microsoft runtime dependency.** pywebview hosts the settings UI in
  Microsoft's **WebView2** (the same Chromium engine, referred to in
  `tests/test_chromium_field.py` and `jarvis/ui/__init__.py`). It is a Microsoft
  component, not a Python package, and is not governed by any licence in this
  inventory.
- **The sherpa-onnx KWS *model* has separate terms from the sherpa-onnx *library*.**
  The library is Apache-2.0; the downloaded wake model
  `sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01` declares
  `license: Apache License 2.0` in its own README front-matter (no LICENSE file ships),
  and its training audio (GigaSpeech XL) carries a **research/education-only
  terms-of-access caveat**. **Read `docs/WAKE_MODEL_LICENCE.md`** before any commercial
  use — the model is *not* covered by this dependency table.

---

## 4. Explicit findings where reality differed from expectation

- **`httpx` is not a direct import.** It is installed as `openai`'s HTTP transport; grep
  of `jarvis/` and `tests/` found **zero** `httpx` references. It belongs in the
  inventory because it is pinned at install time, but it is effectively transitive.
- **`comtypes` is declared as a direct dependency but is not used.** No file in the repo
  references `comtypes` at all. It is present in the environment (MIT) but is dead
  weight for this app; treat it as removable unless another component pulls it in.
- **`pystray` is LGPLv3, not permissive.** Every other Python dependency here is
  Apache-2.0 / BSD / MIT / PSF. LGPLv3 is a copyleft-with-linking-exception licence;
  it does not block private use, but it is a different obligation category and is called
  out so it is not overlooked if the app is ever redistributed. (It is also only
  *optional* at runtime.)
- **`numpy` is a multi-licence bundle** (`BSD-3-Clause AND 0BSD AND MIT AND Zlib AND
  CC0-1.0`), not a single licence — recorded exactly as the metadata reports it.

---

## 5. Administrator rights

**No direct dependency requires administrator rights to install for the current user.**
Evidence: every package above is installed under the per-user prefix
`C:\Users\user\AppData\Local\hermes\hermes-agent\venv\Lib\site-packages`, and
PortAudio and WebView2 are bundled with / provided to the user environment rather than
installed machine-wide. A standard `pip install --user` (or an in-user venv) into that
prefix does not need elevation. All of this app's installs were performed without
administrator rights.

> The one place the app *touches* elevation is a **runtime refusal**, not an install
> requirement: `win/insert.py` refuses to type into an elevated (UIPI) target window —
> "that window runs with administrator rights, so Windows blocks …"
> (`win/insert.py:763`, `win/target.py`). That is a Windows security boundary the app
> respects; it does not make the app elevated.
