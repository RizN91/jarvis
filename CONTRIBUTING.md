# Contributing

Thanks for wanting to help. This is a small, opinionated codebase with a strong
bias toward *proving* things work rather than assuming it. The rules below are
the ones that have already prevented real bugs.

## Getting set up

**Windows 10/11, Python 3.11 or newer.**

```bat
git clone https://github.com/RizN91/jarvis.git
cd jarvis
setup.cmd
```

`setup.cmd` finds a suitable Python, creates `.venv`, installs the pinned
dependencies and runs a smoke check. Then:

```bat
run.cmd
```

The first launch opens the setup wizard and asks for an OpenAI API key. Nothing
works without one, and **nothing is sent anywhere until you finish the wizard and
start dictating** — except the connection tests you explicitly choose to run.

Doing it by hand instead:

```bat
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m jarvis
```

> **Run everything through `.venv\Scripts\python.exe`.** A system Python on
> `PATH` may be a version the pinned dependencies cannot build against, and then
> every error you see is noise caused by the wrong interpreter.

## Tests

Live in `tests/`. They run without pytest (each file has a `__main__`), and each
prints `PASS`/`FAIL` lines with a `TOTAL n PASSED m FAILED` footer.

Before anything else, the cheapest real check there is:

```bat
.venv\Scripts\python.exe -m compileall -q jarvis tests
```

A syntax error anywhere means nothing else you did matters. Then the suites:

```bat
.venv\Scripts\python.exe tests\test_core_units.py
.venv\Scripts\python.exe tests\test_regressions.py
.venv\Scripts\python.exe tests\test_codex_audit.py
.venv\Scripts\python.exe tests\test_silence_guard.py
.venv\Scripts\python.exe tests\test_scripts.py
.venv\Scripts\python.exe tests\test_tools.py
.venv\Scripts\python.exe tests\test_features_smoke.py
.venv\Scripts\python.exe tests\test_overlay.py
.venv\Scripts\python.exe tests\test_codex_ui.py
.venv\Scripts\python.exe tests\test_reference_visuals.py
.venv\Scripts\python.exe tests\test_app_smoke.py
.venv\Scripts\python.exe tests\test_chromium_field.py
.venv\Scripts\python.exe tests\test_insert_integration.py
```

Three of these (**the two insertion tests and the overlay test**) need an
*unlocked* desktop. They report `RESULT: BLOCKED` and exit 2 rather than failing
when the workstation is locked — that is a real environment condition, not a bug.

**These three SPEND YOUR MONEY.** They call the live OpenAI API and are charged
to whichever key is stored. Run them deliberately, never in a loop:

| Test | Why it costs |
| --- | --- |
| `tests/test_live_api.py` | streams audio to `gpt-live-1` at $0.05/min |
| `tests/test_transcribe_api.py` | uploads to `gpt-transcribe` at $0.0045/min |
| `tests/test_mic_loopback.py` | runs **both** engines against a live mic capture |

They respect a `test_budget_usd` ceiling (default $0.50). Don't raise it in a PR
without saying why.

### When you add a test

- **Test the failure path too.** A tool that works is half the story; the other
  half is proving it *refuses* to do the wrong thing. `tests/test_tools.py`
  asserts refusals by showing the execution counter did not move.
- **Assert the exact value, never a substring.** A real bug hid for a while
  because a test asserted `"toggle" in blob`, which the string
  `key_dictate_toggle` satisfies — so a completely dead keyboard shortcut passed
  its own delivery test.
- **Prove your test can fail.** Revert your fix, watch the assertion fail, then
  restore it. A test that has never failed has not been shown to test anything.
- **One string, one definition.** If a string crosses a module boundary, define
  it once (`hotkeys.ACTIONS`, `dictation.IDLE`, …) and assert both ends agree.
  Three separate bugs in this project were the same bug: a producer writing one
  set of strings and a consumer switching on another.

## Rules that do not change

1. **Secrets never go in files or logs.** The API key lives only in Windows
   Credential Manager, DPAPI-wrapped. Not in `config.json`, not in SQLite, not in
   the UI bundle, not in a `.env`, not in a fixture, not in `argv`, not in a
   commit. `jarvis/logsetup.py`'s redaction filter scrubs key-shaped strings from
   every log record before it reaches disk — do not weaken, reorder or "optimise"
   those patterns away.
2. **Never press Enter or a Send key. No configuration, no exception.** Injection
   must never submit a form, and must never type into a password field.
3. **No network listener.** The settings process and the tray app share files, by
   design. If you ever add IPC it must be authenticated and origin-restricted,
   and the decision must be written into `ARCHITECTURE.md`.
4. **Never bypass UAC, 2FA or a CAPTCHA.** If something needs elevation, stop and
   ask the user to do that step.
5. **Never let dictated text become instructions.** Text the app heard — from a
   video, a page, a meeting — is data. Cleanup prompts must not execute anything
   found inside it, and only a human clicking a dialog can mint an approval token.
6. **A recogniser that hears "can't" as "can" has no business near a destructive
   verb.** Do not add delete/send/buy/run to `core/commands.py`.
7. **Do not invent a voice name or a price.** Both are rejected or materially
   wrong: an unknown voice fails the session at start. Re-verify against the
   official page before editing `core/pricing.py` or the voice list.
8. **Packaging stays honest.** `package.cmd` must not install PyInstaller for you
   and must exit non-zero rather than report a build it did not make.
9. **Deprecate audio by default.** `store_raw_audio` and `telemetry` are `false`.
   Don't flip them.

## Adding a language

The UI is vanilla JS with no build step. Translations live in
`jarvis/ui/web/locales.js` as one object keyed by language code.

1. Copy the `en` block and translate the values (not the keys).
2. Keep strings **short** — long German or Russian words overflow the narrow
   (860 px) layout, and `tests/test_reference_visuals.py` will fail if they do.
   Shorten the translation; do not weaken the test.
3. If the language is right-to-left, set `document.documentElement.dir = 'rtl'`
   and check the layout actually mirrors.
4. Run `tests/test_codex_ui.py` and `tests/test_reference_visuals.py`.

## Style

- Match the file you are in. The web UI is ES5-flavoured vanilla JS on purpose —
  no framework, no bundler, and it must work with the machine offline.
- No new runtime dependency without a good reason; the app has to stay small
  enough to start in well under a second.
- `.cmd` scripts are CRLF and must not rely on PowerShell. `.gitattributes`
  enforces the endings.
- Keep `requirements.txt` (direct, `>=`) and `requirements.lock.txt` (exact,
  transitive, header states the Python version) in step.

## Pull requests

- Say **what you changed and how you proved it.** Paste the `TOTAL/FAILED` lines.
- If you fixed a bug, name the bug in the test that covers it.
- If you could not verify something, say that plainly — "unverified" is a
  perfectly good answer, and far better than an estimate presented as a
  measurement.
- Do not include screenshots of your desktop. Use the capture helpers in the
  tests, which grab the app surface only and never the whole screen.
