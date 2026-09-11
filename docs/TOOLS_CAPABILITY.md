# Typed Windows tools + agent integration — capability matrix

Modules: `jarvis/core/tools.py`, `jarvis/core/agents.py`.
Test suite: `tests/test_tools.py`. Everything below is what THIS machine
reported, not what the design intends.

## How to run it

```bash
cd C:\Users\user\Desktop\Jarvis\jarvis
python tests/test_tools.py          # PASS/FAIL/SKIP lines + tally, exit 0 on pass
```

Under pytest it is the same file (`pytest tests/test_tools.py`); every check is a
zero-argument `test_*` function and the last one asserts the aggregate. There is
no `import pytest` anywhere, so no hard dependency. pytest is NOT installed on
this machine and installing one was out of scope, so the pytest entry point is
verified structurally (all functions are collectable zero-arg callables — a check
inside the suite) rather than by an actual `pytest` run.

**Last run: `python tests/test_tools.py` → 91 PASS, 0 FAIL, 0 SKIP, exit 0, ~10 s.**

Nothing in the suite is billable: neither `codex exec` nor `claude -p` is ever
executed. See "Agent integration" below for how the managed-task lifecycle was
still verified end to end.

## Design rules that are enforced, not just documented

- **Typed tools, not shell strings.** 16 named tools, each with a JSON-schema-ish
  parameter spec, a danger level and its own guards. Unknown tools, missing
  arguments and unknown arguments are refused before anything happens.
- **High danger ⇒ per-call approval.** `run_powershell`, `type_text`, `click` and
  `scroll` return `needs_approval=True` and do **not** execute until an explicit
  token is supplied. Tokens are single-use, expire, and are bound to one tool plus
  a hash of the exact arguments — a token approved for one script or one window
  cannot authorise another. Only `issued_by="user"` can mint one; a request from
  tool/model output raises instead.
- **Hard stop.** `set_hard_stop(True)` makes every tool refuse (even with a valid
  token), makes in-flight work report cancellation, and kills a running
  PowerShell child or an in-flight agent process.
- **Untrusted data is never permission.** `UNTRUSTED_DATA_NOTICE` states it and
  `assert_not_permission()` enforces it: text arriving from a page, email, file,
  transcript or tool result that claims to grant permission raises
  `UntrustedPermissionError` and writes an audit row with `allowed=0`.
- **Outcome inspection.** Where a readback exists the tool uses it (window really
  appeared, volume read back over COM, cursor position read back, PowerShell
  stdout captured). Where none exists the result says so instead of claiming
  success — see `CAPABILITY_CAVEATS`.
- **Everything is audited.** Refusals are recorded with `allowed=0`; text-bearing
  arguments are logged as length + hash, never contents. The suite's runs left
  audit rows for `powershell_refused`, `tool_policy_refused`, `tool_refused_locked`,
  `open_folder_refused`, `type_text_refused`, `search_refused`, `read_text_refused`,
  `open_url_refused`, `click_refused`, `untrusted_permission_refused`,
  `approval_refused` and `project_lock_stale_reclaimed` (in a 400-row window of the
  app's own database, roughly half the rows are refusals with `allowed=0`).

## Capability matrix (tools)

Vocabulary: `verified` = exercised on this machine with observed real behaviour;
`blocked` = a required capability is missing here; `untested` = implemented but
not exercised. The suite FAILS if any tool is marked `verified` without having
been exercised in that run, so the claim cannot drift away from the evidence.

| tool | status | evidence |
|---|---|---|
| `list_open_windows` | verified | real desktop enumeration; the test's own Win32 window appeared in the list |
| `find_approved_app` | verified | resolved `notepad` to a real path; `winword` refused with the allowlist reported |
| `open_app` | verified | launched Notepad for real, verified the window (packaged app → resolved through `ApplicationFrameHost`), closed only that window |
| `open_folder` | verified | real Explorer window for an approved temp folder (new `CabinetWClass` window), closed only that window; unapproved path and `..` both refused |
| `open_url` | verified | refusal gate exercised for an unapproved host, `file:`/`javascript:` schemes and an embedded credential; approved host + subdomain passed and the exact URL reached the launcher seam |
| `read_text_file` | verified | real file read byte-identically (62 bytes, 6 lines, utf-8); 300 KB file refused by the cap; NUL-containing file refused as binary; path outside approved roots refused |
| `list_directory` | verified | real entries with sizes from an approved folder; unapproved path + traversal refused |
| `search_files` | verified | found a file by name and by content (reported the line); unapproved root and `..` root refused; bounds measured (below) |
| `screenshot` | verified | captured monitor 1 for real (1920×1080) and verified the PNG size against the monitor rect (this machine has 2 monitors); monitor 99 refused with "nothing was captured" and no file written |
| `volume_set` | verified | set the master volume to 37% over COM and read it back (37.0%), original restored |
| `media_control` | verified | all four actions mapped to the correct virtual keys (0xB0–0xB3) at the key seam; unknown action refused |
| `type_text` | verified | typed 22 characters into a real Win32 `EDIT` control and read the text back with `WM_GETTEXT`; a real `ES_PASSWORD` control refused even WITH approval and stayed empty; a real terminal window refused; blocked target refused; execution counter unmoved for every refusal |
| `click` | verified | refused without approval, refused when the approved window was not in front, refused out-of-bounds coordinates; with approval the cursor moved for real and the move was verified by reading `GetCursorPos` back, pointer restored |
| `scroll` | verified | refused 0 and 500 notches; −3 notches produced WHEEL_DELTA −360 at the mouse seam |
| `run_powershell` | verified | `Remove-Item`, `Invoke-Expression`, `iex`, `cmd.exe` re-entry, `-EncodedCommand`, a credential path, `& {}` and `Start-Process` all refused; a real `Get-Date` ran under approval; an in-flight run was cancelled by the hard stop in 0.58 s with no PowerShell child left running |
| `task_status` | verified | read tasks written by `agents.start_task`; unknown id → `not_found` |

`blocked` and `untested` are empty **in the tool matrix** because every tool's
working path and at least one refusal path were exercised. The vocabulary is not
vacuous — the agent module uses all three (below), and `CAPABILITY_CAVEATS`
records the sub-paths left unverified per tool:

| tool | what is NOT verified |
|---|---|
| `open_url` | the real `os.startfile` handoff was not fired (it would open a tab in the user's browser); the gate and the exact URL passed to the seam were verified, the browser was not |
| `media_control` | transport state has no documented readback, so only key delivery is verified; the real transport key was not fired (it would interrupt the user's playback) |
| `scroll` | no readback for scroll position; only the wheel payload handed to the seam is verified |
| `click` | the move is verified by readback; the button press goes through the seam and was not fired into the live desktop |
| `volume_set` | COM worked here, so the key-injection fallback is untested |
| `screenshot` | the LOCKED-desktop refusal comes from Windows and was not exercised (the session was not locked) |
| `type_text` | `insert_text` reports DELIVERY of key events; SendInput has no completion signal, so the target's settled contents were only readable because the test owned the control (settled after 0.11 s) |
| `search_files` | content matching reads files ≤ 512 KB; larger files match by name only; hidden/system dirs are skipped |

## Measured bounds (`search_files`)

A synthetic tree of exactly 4000 files, timed for real:

- file cap: `max_files=250` → visited **250 of 4000**, `stopped_by="file cap"`, **0.032 s**
- deadline: `deadline_s=0.1`, no file cap → **0.109 s** wall clock, 769 files visited,
  `stopped_by="deadline"`

The other hard caps: 256 KB read cap, 400 directory entries, 4000 `os.walk` files /
5 s default, 200 windows listed, 64 KB PowerShell output, 8000-character script,
20 s default PowerShell timeout, 50 scroll notches, 120 s approval TTL.

## Agent integration (`jarvis/core/agents.py`)

`INTEGRATION_STATUS` in the module carries these labels; the suite asserts the
vocabulary and prints them.

| behaviour | status | note |
|---|---|---|
| CLI present + version | **verified** | `claude --version` → `2.1.239 (Claude Code)`; `codex --version` → `codex-cli 0.153.3` |
| argv templates match documented flags | **verified** | every flag in the defaults appears in that CLI's own `--help` output |
| forbidden-flag screening | **verified** | 7 dangerous combinations refused, incl. `--bare`, `--dangerously-skip-permissions`, `--api-key`, `--oss`, `-m`, `--last`, `--continue` |
| auth presence detection | **verified** | a spy on `open`/`read_text`/`read_bytes` proves ZERO credential files are opened during `detect()`; only existence + size are read (`~/.codex/auth.json` 4154 bytes, `~/.claude/.credentials.json` 25429 bytes) |
| project lock exclusion + staleness | **verified** | second task excluded with `project_locked` before launching; a dead-owner lock detected as stale, reclaimed, and audited |
| managed task lifecycle | **verified** | start → recorded running → cancel terminated the child → status `cancelled` → lock released, driven through the same code path with a substitute command |
| process runner bounds | **verified** | stdin delivery, 1.0 s timeout kill, output truncated at 1024 bytes |
| `claude` non-interactive launch | assumed | `-p` documented, never executed (billable) |
| `claude` session-id control | assumed | `--session-id <uuid>` documented for print mode |
| `claude` resume known session | assumed | `--resume <id>` documented |
| `codex exec` non-interactive launch | assumed | documented, never executed (billable) |
| `codex` resume known session | assumed | `codex exec resume <SESSION_ID>` documented |
| `codex` session-id discovery | **blocked** | `codex exec` invents its own id and exposes no documented way to read it, so `continue_task` on a Codex task returns a clear blocker. There is no `--last` fallback anywhere — `--last` and `--continue` are refused by `validate_argv`. |
| `claude` prompt quoting on Windows | assumed | `claude` installs as an npm `.cmd` shim, so cmd.exe quoting rules apply to a positional prompt; templates can deliver the prompt on stdin instead (implemented and verified with a substitute), which avoids the shim and is the recommended setting |

### Billing and auth guarantees

- Codex: the user's existing ChatGPT login is preserved. `-c/--config`, `--oss`,
  `--local-provider`, `-m/--model`, `-p/--profile` and
  `--dangerously-bypass-approvals-and-sandbox` are refused, so no command can
  silently switch provider, model, plan or sandbox.
- Claude: no flag that bypasses subscription credentials is allowed — `--bare`
  (forces `ANTHROPIC_API_KEY`), `--api-key`, `--settings`, `--mcp-config`,
  `--dangerously-skip-permissions` — and no OAuth token is extracted or read.
- Auth files are never read (existence and size only), so no token can reach a
  prompt, a log or the transcript. `run_powershell` independently refuses scripts
  that reference credential paths (`auth.json`, `.credentials`, `id_rsa`, `.env`,
  `config.toml`, …).
- Everything above is enforced by `validate_argv()` on every build, not just on
  the shipped defaults, so a mistyped config override cannot quietly change the
  user's account.

## Known limits / honest gaps

1. **The real CLIs are never run** (they cost the user money). The argv, the
   screening, the database rows, the lock, cancellation and the runner are all
   verified; whether `claude -p --session-id …` behaves exactly as the docs
   suggest is ASSUMED, not proven.
2. **`run_powershell` is an allowlist heuristic, not a sandbox.** Every statement
   must begin with a read-only cmdlet and a curated denylist is refused
   independently, but a permitted cmdlet can still read files that are not on the
   credential denylist. It is described everywhere as RESTRICTED and never as
   unrestricted shell access.
3. **Seam-verified actions.** `open_url`, `media_control`, `scroll` and the
   `click` button press mark the exact boundary to Windows and were verified at
   that boundary without hijacking the user's browser, media or mouse. Listed in
   `CAPABILITY_CAVEATS`.
4. **`insert_text` reports delivery, not the target's settled state.** There is no
   completion signal in SendInput; the suite needed a settle window (0.11 s) to
   read the text back.
5. **pytest path not executed** — pytest is not installed here and installing was
   out of scope.
6. **Test isolation.** Approved paths are injected into the in-memory config cache
   and restored at the end, so the user's `config.json` is never written (there is
   no `config.json` on this machine at all). Temp files, locks, screenshots and
   the windows the suite opened are cleaned up; the only persistent artefacts are
   audit rows and task rows in the app's own database.
