# Security policy

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Use GitHub's private reporting: go to the
[Security tab](https://github.com/RizN91/jarvis/security) →
**Report a vulnerability**. That opens a private thread only the maintainers can
see. If that is unavailable, open a normal issue that says only *"security issue,
please contact me"* and no details, and we'll move it somewhere private.

Please include: what you did, what happened, what you expected, and the version
(`jarvis --version`). A proof of concept helps enormously.

We aim to acknowledge within a few days and to ship a fix or a clear explanation
as fast as is honest. We will credit you in the release notes unless you'd rather
we didn't.

## What is in scope

- The Python app and its dependencies as used here.
- The API-key handling path (`jarvis/secrets.py`).
- The tool/approval layer (`jarvis/core/tools.py`, `jarvis/core/delegation.py`)
  and anything that could run an action **without** an explicit human approval.
- Text insertion (`jarvis/win/insert.py`) — especially anything that could press
  Enter, submit a form, or type into a password field.
- The settings UI (`jarvis/ui/web/`) — XSS, arbitrary code execution, or anything
  that could reach outside the app.

## Especially interesting to us

These are the properties the design depends on. A break in any of them is a
serious bug, not a nitpick:

1. **A secret escaping the redaction filter** into a log, an error message, a
   crash dump or the UI.
2. **An action running without an approval click.** Approval tokens are single
   use, expire, and are bound to both the tool *and* a hash of its arguments;
   only a real user interaction may mint one.
3. **Dictated text being treated as instructions.** Anything the app heard —
   from a page, a video, a meeting — is data. A cleanup prompt must never execute
   something found inside it.
4. **Enter or a submit key being synthesised**, in any code path.
5. **Typing into a password field**, or into an elevated window.
6. **Audio or a transcript leaving the machine** while the app is only listening
   for a wake word.

## What is not a vulnerability

- **You need your own API key.** The app is useless without one; that is the
  design, not a flaw.
- **The cost ceilings are Jarvis's own counters.** They cannot read your OpenAI
  account budget. This is documented, not a bypass.
- **Windows SmartScreen warnings.** The build is not code-signed yet.
- Anything requiring an attacker to already have code execution or your unlocked
  session on the machine.
- "It can type into my bank's website." You asked it to type. It never presses
  Enter.

## Supported versions

The latest release only. This is a young project; fixes land on `main` and go out
in the next release.

## Our own commitments

- No telemetry, no analytics, no phoning home. The only network traffic is the
  OpenAI API calls you trigger.
- No raw audio written to disk.
- No network listener — the settings window and the tray app communicate through
  files in `%LOCALAPPDATA%\Jarvis`, so there is no port to expose.
- No secrets in the repository. `.gitignore` blocks the paths a key would
  plausibly land in, and the log redaction filter is a hard rule in
  `CONTRIBUTING.md`.
