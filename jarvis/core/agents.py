"""Drive the installed Codex and Claude clients as MANAGED tasks.

Both CLIs are installed on this machine (verified with `claude --version` ->
"2.1.239 (Claude Code)" and `codex --version` -> "codex-cli 0.153.3"). This
module starts them as tracked tasks, keeps their session ids so a task can be
continued deliberately, and holds a per-project lock so two agents cannot edit
the same directory at once.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
1. IT NEVER READS A CREDENTIAL FILE. It checks whether the auth file exists and
   what size it is (metadata only), because the tray needs to tell the user
   "you are signed in" or "sign in first". It never opens, parses, logs or
   forwards the contents. See _auth_presence().
2. IT NEVER CHANGES THE USER'S BILLING OR AUTH MODE. For Codex the user's
   existing ChatGPT login must be preserved, so no flag that switches provider
   or account may be used (-c/--config overrides, --oss, model overrides). For
   Claude, subscription credentials must not be bypassed, so --bare (which
   forces ANTHROPIC_API_KEY and skips keychain/OAuth) and any API-key plumbing
   are forbidden, and no OAuth token is ever extracted. Every command is
   screened by validate_argv(), which refuses those flags outright.
3. IT NEVER RESUMES A SESSION BLINDLY. There is no "most recent session"
   shortcut anywhere: `codex exec resume --last` and `claude --continue` are on
   the forbidden list. continue_task() resumes only the session id recorded on
   the task row the caller selected.
4. IT DOES NOT FAKE CAPABILITIES. Where the real interface is missing, the
   function returns a blocker naming it. The clearest example: `codex exec`
   invents its own session id and offers no documented way to learn it, so a
   Codex task cannot be continued by id from here. That is reported as a
   blocker, not worked around by falling back to "--last".

VERIFIED vs ASSUMED
-------------------
Every command line is built from an EXPLICIT argv template (see
DEFAULT_ARGV_TEMPLATES, overridable per agent under the config key
"agent_argv_templates"). INTEGRATION_STATUS below states, in this module rather
than only in prose, which parts of the integration were actually exercised on
this machine and which are taken from the CLIs' own --help output without ever
running a billed task. Nothing marked ASSUMED is presented as working.

Why the agent itself is never run from the test suite: `codex exec` and
`claude -p` send a prompt to the user's account. That is billable and it is the
user's money, so the suite drives the whole managed-session machinery with a
harmless substitute command through the same code path (see the runner tests)
and never spends a cent.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .. import config
from ..db import db
from ..logsetup import get as _log
from . import tools as _tools

log = _log("agents")

VERIFIED = "verified"
ASSUMED = "assumed"
BLOCKED = "blocked"

# Which part of the integration is proven on this machine, right now. Quoted in
# the final report and in docs/TOOLS_CAPABILITY.md, and asserted by the tests.
INTEGRATION_STATUS: dict[str, str] = {
    "cli_present_and_version":
        VERIFIED,      # `claude --version` and `codex --version` were run
    "argv_templates_match_documented_flags":
        VERIFIED,      # every flag in the defaults appears in that CLI's --help
    "forbidden_flag_screening":
        VERIFIED,      # validate_argv rejects billing/auth-changing flags
    "auth_presence_detection":
        VERIFIED,      # existence + size only, proven not to read contents
    "project_lock_exclusion_and_staleness":
        VERIFIED,      # a second task is excluded; a dead-owner lock is reclaimed
    "managed_task_lifecycle":
        VERIFIED,      # start -> record -> cancel/terminate -> status -> unlock,
                       # exercised with a substitute command through the same path
    "process_runner_timeout_and_output_cap":
        VERIFIED,
    "claude_noninteractive_launch":
        ASSUMED,       # `claude -p ...` documented, never executed (billable)
    "claude_session_id_control":
        ASSUMED,       # `--session-id <uuid>` documented for --print mode
    "claude_resume_known_session":
        ASSUMED,       # `--resume <id>` documented
    "codex_exec_noninteractive_launch":
        ASSUMED,       # `codex exec` documented, never executed (billable)
    "codex_resume_known_session":
        ASSUMED,       # `codex exec resume <SESSION_ID>` documented
    "codex_session_id_discovery":
        BLOCKED,       # `codex exec` generates its own id; no documented way to
                       # read it, and we will not parse undocumented output as if
                       # it were a contract. Continue-by-id is unavailable for
                       # Codex until the CLI exposes it.
    "claude_prompt_quoting_on_windows":
        ASSUMED,       # claude installs as an npm .cmd shim, so cmd.exe quoting
                       # rules apply to a positional prompt. Templates may deliver
                       # the prompt on stdin instead (implemented, verified with a
                       # substitute), which avoids the shim entirely.
}

# Credential artefacts. EXISTENCE AND SIZE ONLY - never opened.
AUTH_FILES: dict[str, list[str]] = {
    "codex": ["~/.codex/auth.json", "~/.codex/config.toml"],
    "claude": ["~/.claude/.credentials.json", "~/.claude.json"],
}

# Flags that would silently change how the user is authenticated or billed, or
# that would resume a session the user did not pick. Refused by validate_argv.
FORBIDDEN_FLAGS: dict[str, dict[str, str]] = {
    "*": {
        "--last": "would resume the most recent session instead of the one the "
                  "user selected",
        "--continue": "would resume the most recent conversation blindly",
    },
    "codex": {
        "--oss": "would switch Codex to an open-source provider instead of the "
                 "user's ChatGPT login",
        "--local-provider": "would switch Codex's provider",
        "-m": "a model override can change which plan/credits are billed",
        "--model": "a model override can change which plan/credits are billed",
        "-p": "a profile override can change the auth/billing profile",
        "--profile": "a profile override can change the auth/billing profile",
        "-c": "a -c config override could change the model, provider or auth "
              "settings that decide what is billed",
        "--config": "a config override could change the model, provider or auth "
                    "settings that decide what is billed",
        "--dangerously-bypass-approvals-and-sandbox":
            "would run agent commands with no sandbox and no confirmations",
    },
    "claude": {
        "--bare": "sets CLAUDE_CODE_SIMPLE and forces ANTHROPIC_API_KEY, "
                  "bypassing the user's subscription credentials",
        "--dangerously-skip-permissions": "would bypass all permission checks",
        "--allow-dangerously-skip-permissions":
            "would make bypassing permission checks selectable",
        "--api-key": "would authenticate with a key instead of the user's login",
        "--settings": "could inject auth/apiKeyHelper settings",
        "--mcp-config": "could load an unexpected MCP server",
        "-c": "would continue the most recent conversation blindly",
        "--continue": "would continue the most recent conversation blindly",
    },
}

# The explicit, configurable command templates. {workdir}, {prompt} and
# {session_id} are substituted; nothing else is interpolated, and no shell is
# involved because these are always executed as an argv LIST.
DEFAULT_ARGV_TEMPLATES: dict[str, dict[str, dict[str, Any]]] = {
    "codex": {
        "new": {
            "argv": ["codex", "exec", "--skip-git-repo-check", "-C", "{workdir}",
                     "{prompt}"],
            "stdin": None,
            "json_events": False,
        },
        "resume": {
            "argv": ["codex", "exec", "resume", "{session_id}", "{prompt}"],
            "stdin": None,
            "json_events": False,
        },
    },
    "claude": {
        "new": {
            "argv": ["claude", "-p", "--output-format", "json", "--session-id",
                     "{session_id}", "{prompt}"],
            "stdin": None,
            "json_events": False,
        },
        "resume": {
            "argv": ["claude", "-p", "--output-format", "json", "--resume",
                     "{session_id}", "{prompt}"],
            "stdin": None,
            "json_events": False,
        },
    },
}

AGENT_EXES = {"codex": "codex", "claude": "claude"}

MAX_OUTPUT_BYTES = 200 * 1024
DEFAULT_TIMEOUT_S = 120.0
LOCK_STALE_SECONDS = 3600.0

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
kernel32.GetExitCodeProcess.restype = wt.BOOL


def _pid_alive(pid: int) -> bool:
    """Is this pid still running? (No psutil dependency, no signal tricks.

    os.kill(pid, 0) is NOT used: on Windows a zero signal means TerminateProcess,
    so a liveness check would kill the process it asked about.
    """
    if not pid:
        return False
    h = kernel32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
                             False, int(pid))
    if not h:
        return False
    try:
        code = wt.DWORD(0)
        if not kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return int(code.value) == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(h)


# ----------------------------------------------------------------- detection


def _auth_presence(agent: str) -> tuple[str, str]:
    """Is there a credential file? EXISTENCE AND SIZE ONLY.

    This function opens nothing. It calls Path.exists() and Path.stat().st_size,
    which are metadata calls: the app learns "a credential file is present and
    is 4154 bytes" and never learns its contents. That is the whole point - the
    tray can say "signed in" without this process ever holding a token.
    """
    found: list[str] = []
    for pattern in AUTH_FILES.get(agent, []):
        p = Path(pattern).expanduser()
        try:
            if p.exists():
                found.append(f"{pattern} ({p.stat().st_size} bytes, not read)")
        except OSError:
            continue
    if found:
        return "credentials_present", "; ".join(found)
    return "none_found", ("no credential file found for " + agent +
                          " (sign in with the CLI)")


def _cli_version(exe: str, timeout: float = 30.0) -> tuple[Optional[str], str]:
    """Run `--version`. Free, local, and the only CLI call the app makes blindly."""
    path = shutil.which(exe)
    if not path:
        return None, f"{exe} is not on PATH"
    try:
        proc = subprocess.run([path, "--version"], capture_output=True,
                              text=True, timeout=timeout, shell=False)
    except Exception as exc:
        return None, f"{exe} --version failed: {exc}"
    out = (proc.stdout or proc.stderr or "").strip().splitlines()
    version = out[0].strip() if out else ""
    return (version or None), f"{exe} --version -> rc {proc.returncode}"


def detect(agent: str) -> dict[str, Any]:
    """What is installed and usable for `agent` (codex | claude).

    Reads NO credential file contents: see _auth_presence(). Billing mode is
    reported from configuration, because it is the user's setting - this app
    never probes the account to discover a plan.
    """
    key = str(agent or "").strip().lower()
    if key not in AGENT_EXES:
        return {"agent": key, "installed": False, "error": "unknown_agent",
                "known": sorted(AGENT_EXES)}
    cfg = config.get(key) or {}
    exe = AGENT_EXES[key]
    path = shutil.which(exe)
    version, version_note = (None, f"{exe} is not on PATH") if not path \
        else _cli_version(exe)
    auth_state, auth_note = _auth_presence(key)
    workdir = (cfg.get("workdir") if isinstance(cfg, dict) else None) \
        or str(config.project_dir())
    billing = (cfg.get("billing_mode") if isinstance(cfg, dict) else None) or (
        "subscription" if key == "codex" else
        "subscription (assumed: Claude Code signs in with a plan; this app does "
        "not probe the account)")
    discovery = INTEGRATION_STATUS["codex_session_id_discovery"]
    resumable = (discovery != BLOCKED) if key == "codex" else True
    return {
        "agent": key,
        "installed": bool(path),
        "path": path,
        "version": version,
        "version_evidence": version_note if version else None,
        "authenticated": auth_state,
        "auth_evidence": auth_note,
        "auth_files_read": False,
        "billing_mode": billing,
        "billing_mode_source": "config (not probed)",
        "workdir": workdir,
        "enabled": bool(cfg.get("enabled", True)) if isinstance(cfg, dict) else True,
        "session_id_controlled_by_app": key == "claude",
        "resumable_by_id": resumable,
        "resume_note": (
            "the app supplies --session-id, so the task's own id is known and "
            "can be resumed deliberately" if key == "claude" else
            "codex exec creates its own session id and exposes no documented way "
            "to read it, so resume-by-id is BLOCKED for Codex (no --last fallback)"
        ),
        "integration_status": dict(INTEGRATION_STATUS),
    }


def detect_all() -> dict[str, Any]:
    return {a: detect(a) for a in AGENT_EXES}


# ------------------------------------------------------- command construction


def _sub(template: str, values: dict[str, str]) -> str:
    out = str(template)
    for key, value in values.items():
        out = out.replace("{" + key + "}", value)
    return out


def templates_from_config(agent: str) -> dict[str, dict[str, Any]]:
    """Templates for an agent: the config override if present, else the defaults."""
    override = (config.get("agent_argv_templates") or {})
    merged = {k: dict(v) for k, v in DEFAULT_ARGV_TEMPLATES.get(agent, {}).items()}
    for kind, tmpl in (override.get(agent) or {}).items():
        if isinstance(tmpl, (list, dict)):
            merged[kind] = dict(tmpl) if isinstance(tmpl, dict) else {"argv": tmpl}
    return merged


def build_argv(agent: str, kind: str, prompt: str, workdir: str,
               session_id: str = "", template: Optional[dict[str, Any]] = None
               ) -> dict[str, Any]:
    """Build the exact argv (a LIST) and optional stdin text for one call.

    Pure: no process is started, so the UI can show the user precisely what will
    run before anything happens.
    """
    templates = {kind: dict(template)} if template else templates_from_config(agent)
    tmpl = templates.get(kind)
    if tmpl is None:
        raise KeyError(f"{agent} has no '{kind}' template")
    values = {"workdir": str(workdir or ""), "prompt": str(prompt or ""),
              "session_id": str(session_id or "")}
    argv = [_sub(str(a), values) for a in (tmpl.get("argv") or [])]
    stdin_tmpl = tmpl.get("stdin")
    stdin_text = _sub(str(stdin_tmpl), values) if stdin_tmpl else None
    return {"argv": argv, "stdin": stdin_text, "kind": kind, "agent": agent,
            "json_events": bool(tmpl.get("json_events", False))}


def validate_argv(agent: str, argv: list[str]) -> Optional[str]:
    """Refuse any flag that changes auth/billing or resumes blindly.

    Checked on every build, not just on the defaults, so a mistyped config
    override cannot quietly switch the user's account or resume the wrong
    session.
    """
    rules = dict(FORBIDDEN_FLAGS.get("*", {}))
    rules.update(FORBIDDEN_FLAGS.get(agent, {}))
    for arg in argv:
        flag = str(arg)
        if flag in rules:
            return (f"refused: {flag} is not allowed — {rules[flag]}")
        for name, why in rules.items():
            if flag.startswith(name + "="):
                return (f"refused: {name}= is not allowed — {why}")
    return None


# ------------------------------------------------------------ process runner


@dataclass
class RunOutcome:
    started: bool
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    elapsed_s: float = 0.0
    timed_out: bool = False
    cancelled: bool = False
    pid: int = 0
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "started": self.started, "exit_code": self.exit_code,
            "stdout": self.stdout, "stderr": self.stderr,
            "truncated": self.truncated, "elapsed_s": round(self.elapsed_s, 3),
            "timed_out": self.timed_out, "cancelled": self.cancelled,
            "pid": self.pid, "error": self.error,
        }


_PROCS: dict[int, subprocess.Popen] = {}
_PROC_LOCK = threading.RLock()
# Task ids cancelled by the user while their process was still running. Needed
# because _run_process() cannot tell WHY the child exited: without this, a
# cancelled task's own completion path would overwrite 'cancelled' with
# 'failed' when the killed process reported a non-zero exit code.
_CANCELLED: set[int] = set()


def _mark_cancelled(task_id: int) -> None:
    with _PROC_LOCK:
        _CANCELLED.add(int(task_id))


def _consume_cancelled(task_id: int) -> bool:
    with _PROC_LOCK:
        if int(task_id) in _CANCELLED:
            _CANCELLED.discard(int(task_id))
            return True
    return False


def _register_proc(task_id: int, proc: subprocess.Popen) -> None:
    with _PROC_LOCK:
        _PROCS[int(task_id)] = proc


def _unregister_proc(task_id: int) -> None:
    with _PROC_LOCK:
        _PROCS.pop(int(task_id), None)


def live_processes() -> list[int]:
    with _PROC_LOCK:
        return [tid for tid, p in _PROCS.items() if p.poll() is None]


def _run_process(argv: list[str], cwd: Optional[str] = None,
                 timeout_s: float = DEFAULT_TIMEOUT_S,
                 stdin_text: Optional[str] = None, task_id: int = 0,
                 cancel: Optional[threading.Event] = None,
                 max_output_bytes: int = MAX_OUTPUT_BYTES) -> RunOutcome:
    """Run a command as an argv LIST (never shell=True) with real bounds.

    Reads the child's output on a thread so a chatty agent cannot fill memory,
    enforces a wall-clock timeout, and stops early when the task is cancelled or
    the app's hard stop is set. The child is terminated, not left orphaned.
    """
    if not argv:
        return RunOutcome(False, error="no command was given")
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            [str(a) for a in argv], cwd=cwd or None, shell=False,
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        return RunOutcome(False, error=f"could not start {argv[0]}: {exc}")
    if task_id:
        _register_proc(task_id, proc)

    out_chunks: list[bytes] = []
    err_chunks: list[bytes] = []
    state = {"out": 0, "err": 0, "truncated": False}

    def _pump(stream, chunks: list[bytes], key: str) -> None:
        try:
            while True:
                block = stream.read(65536)
                if not block:
                    break
                if state[key] < max_output_bytes:
                    room = max_output_bytes - state[key]
                    chunks.append(block[:room])
                    state[key] += min(len(block), room)
                    if len(block) > room:
                        state["truncated"] = True
                else:
                    state["truncated"] = True  # keep draining, stop storing
        except Exception:
            pass

    threads = [threading.Thread(target=_pump, args=(proc.stdout, out_chunks, "out"),
                                name="agent-out", daemon=True),
               threading.Thread(target=_pump, args=(proc.stderr, err_chunks, "err"),
                                name="agent-err", daemon=True)]
    for t in threads:
        t.start()

    if stdin_text is not None and proc.stdin is not None:
        try:
            proc.stdin.write(str(stdin_text).encode("utf-8"))
            proc.stdin.close()
        except Exception:
            pass

    outcome = RunOutcome(True, pid=proc.pid)
    deadline = time.monotonic() + max(0.5, float(timeout_s))
    while proc.poll() is None:
        if _tools.is_hard_stopped():
            outcome.cancelled = True
            proc.kill()
            break
        if cancel is not None and cancel.is_set():
            outcome.cancelled = True
            proc.kill()
            break
        if time.monotonic() > deadline:
            outcome.timed_out = True
            proc.kill()
            break
        time.sleep(0.05)
    try:
        proc.wait(timeout=5)
    except Exception:
        pass
    for t in threads:
        t.join(timeout=2.0)
    for stream in (proc.stdout, proc.stderr):
        try:
            if stream is not None:
                stream.close()
        except Exception:
            pass
    if task_id:
        _unregister_proc(task_id)

    for enc in ("utf-8", "cp1252"):
        try:
            outcome.stdout = b"".join(out_chunks).decode(enc)
            outcome.stderr = b"".join(err_chunks).decode(enc)
            break
        except Exception:
            continue
    else:
        outcome.stdout = b"".join(out_chunks).decode("utf-8", errors="replace")
        outcome.stderr = b"".join(err_chunks).decode("utf-8", errors="replace")
    outcome.exit_code = proc.returncode
    outcome.truncated = state["truncated"]
    outcome.elapsed_s = time.monotonic() - started
    return outcome


# ----------------------------------------------------------- project locking

_SESSION_RE = re.compile(r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                         r"[0-9a-f]{4}-[0-9a-f]{12})\b", re.IGNORECASE)


def parse_session_id_from_output(text: str) -> Optional[str]:
    """Best-effort session-id extraction from CLI output. ASSUMED, not a contract.

    Intended for a template that sets "json_events": true and an agent that
    prints its id. The output shape is undocumented, so a result from here is
    only ever *offered* for the user to confirm - never used to silently resume
    anything, and never used for Codex, whose discovery is BLOCKED.
    """
    if not text:
        return None
    for line in str(text).splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                blob = json.loads(line)
            except Exception:
                blob = None
            if isinstance(blob, dict):
                for key in ("session_id", "sessionId", "conversation_id",
                            "thread_id", "id"):
                    value = blob.get(key)
                    if isinstance(value, str) and _SESSION_RE.fullmatch(value.strip()):
                        return value.strip()
        m = _SESSION_RE.search(line)
        if m:
            return m.group(1)
    return None


def locks_dir() -> Path:
    p = config.data_dir() / "locks"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _lock_file(workdir: str) -> Path:
    import hashlib
    key = os.path.normcase(os.path.abspath(str(workdir)))
    return locks_dir() / (hashlib.sha1(key.encode("utf-8")).hexdigest()[:20] + ".lock")


def project_lock_owner(workdir: str) -> Optional[dict[str, Any]]:
    """Read the lock record (never throws). None when the project is free."""
    path = _lock_file(workdir)
    try:
        if not path.exists():
            return None
        rec = json.loads(path.read_text(encoding="utf-8"))
        rec["stale"] = _lock_is_stale(rec)
        return rec
    except Exception:
        return None


def _lock_is_stale(rec: dict[str, Any]) -> bool:
    pid = int(rec.get("pid") or 0)
    age = time.time() - float(rec.get("ts") or 0)
    if age > LOCK_STALE_SECONDS:
        return True
    owner = int(rec.get("owner_pid") or pid)
    return bool(owner) and not _pid_alive(owner)


def acquire_project_lock(workdir: str, task_id: int, agent: str = "",
                         owner_pid: Optional[int] = None) -> tuple[bool, str]:
    """Take the per-project lock, or explain who holds it.

    A lock whose owner process is dead (or which is older than
    LOCK_STALE_SECONDS) is reclaimed, so a crash cannot wedge a project forever.
    Creation is atomic (O_EXCL), so two agents starting at the same instant
    cannot both win.
    """
    path = _lock_file(workdir)
    existing = None
    try:
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        existing = None
    if existing is not None and not _lock_is_stale(existing):
        if int(existing.get("task_id") or 0) == int(task_id):
            return True, "already held by this task"
        return False, (f"project is locked by task {existing.get('task_id')} "
                       f"({existing.get('agent') or 'agent'}, pid "
                       f"{existing.get('pid')}, started {existing.get('started')})")
    if existing is not None:
        db().audit("project_lock_stale_reclaimed",
                   {"workdir": str(workdir), "old_owner": existing.get("task_id"),
                    "old_pid": existing.get("pid")}, allowed=True)
        try:
            path.unlink()
        except Exception:
            pass

    payload = {
        "task_id": int(task_id), "agent": agent, "workdir": str(workdir),
        "pid": os.getpid(), "owner_pid": int(owner_pid or os.getpid()),
        "ts": time.time(), "started": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False, f"another task took the lock for {workdir} first"
    except Exception as exc:
        return False, f"the lock file could not be created: {exc}"
    try:
        os.write(fd, json.dumps(payload).encode("utf-8"))
    finally:
        os.close(fd)
    return True, "locked"


def release_project_lock(workdir: str, task_id: int) -> bool:
    """Release the lock only if this task owns it."""
    path = _lock_file(workdir)
    try:
        if not path.exists():
            return False
        rec = json.loads(path.read_text(encoding="utf-8"))
        if int(rec.get("task_id") or 0) != int(task_id):
            return False
        path.unlink()
        return True
    except Exception:
        return False


# ------------------------------------------------------- managed task sessions


def _resolve_workdir(agent: str, workdir: Optional[str]) -> tuple[Optional[str], str]:
    if workdir:
        p = Path(str(workdir)).expanduser()
    else:
        cfg = config.get(agent) or {}
        p = Path(str(cfg.get("workdir") or config.project_dir())).expanduser()
    if not p.exists():
        return None, f"{p} does not exist"
    if not p.is_dir():
        return None, f"{p} is not a folder"
    return str(p), "ok"


def start_task(agent: str, prompt: str, workdir: Optional[str] = None,
               title: Optional[str] = None, dry_run: bool = False,
               timeout_s: Optional[float] = None,
               argv_template: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Start a managed agent task and record it in the local database.

    `dry_run=True` performs every step except launching the CLI (it records the
    exact argv it would run). The tray uses this to show the user what a task
    will do, and because it never launches, it is also how the test suite proves
    the lifecycle without billing the user.

    The project lock is taken BEFORE the agent starts, so two agents can never
    edit the same directory at once.
    """
    key = str(agent or "").strip().lower()
    if key not in AGENT_EXES:
        return {"ok": False, "error": "unknown_agent", "agent": key}
    info = detect(key)
    if not info.get("installed"):
        return {"ok": False, "error": "not_installed", "agent": key,
                "detail": f"{AGENT_EXES[key]} was not found on PATH"}
    if not info.get("enabled"):
        return {"ok": False, "error": "disabled",
                "detail": f"{key} is switched off in Settings"}

    wd, why = _resolve_workdir(key, workdir)
    if wd is None:
        return {"ok": False, "error": "bad_workdir", "detail": why}

    session_id = str(uuid.uuid4()) if key == "claude" else ""
    if info.get("resumable_by_id") is False:
        session_note = ("this task cannot be continued by id: codex exec does not "
                        "accept or expose a session id (no --last fallback)")
    else:
        session_note = "session id is known and can be resumed deliberately"

    build = build_argv(key, "new", prompt, wd, session_id, argv_template)
    problem = validate_argv(key, build["argv"])
    if problem:
        db().audit("agent_argv_refused", {"agent": key, "why": problem},
                   allowed=False)
        return {"ok": False, "error": "forbidden_flag", "detail": problem,
                "argv": build["argv"]}

    estimated_title = (title or str(prompt or "").strip()[:80] or f"{key} task")
    task_id = db().start_task(
        key, session_id, wd, estimated_title,
        meta={"argv": build["argv"], "dry_run": dry_run, "kind": "new",
              "session_note": session_note,
              "prompt_chars": len(str(prompt or "")),
              "prompt_sha8": hashlib.sha256(
                  str(prompt or "").encode("utf-8")).hexdigest()[:8],
              "billing_mode": info.get("billing_mode")},
    )

    acquired, lock_why = acquire_project_lock(wd, task_id, agent=key)
    if not acquired:
        db().update_task(task_id, status="blocked",
                         meta={"lock": lock_why, "argv": build["argv"]})
        return {"ok": False, "error": "project_locked", "task_id": task_id,
                "detail": lock_why}
    db().update_task(task_id, status="locked" if not dry_run else "planned",
                     meta={"lock": "acquired", "argv": build["argv"],
                           "dry_run": dry_run, "session_id": session_id,
                           "session_note": session_note})

    if dry_run:
        # A plan is not a running task, so it must not hold the project lock.
        # We took it only to prove the project was free, and give it straight
        # back so a planned task cannot block a real one.
        released = release_project_lock(wd, task_id)
        db().audit("agent_task_planned",
                   {"agent": key, "task_id": task_id, "argc": len(build["argv"]),
                    "workdir": wd, "session_id": session_id,
                    "lock_released": released}, allowed=True)
        return {"ok": True, "task_id": task_id, "agent": key, "dry_run": True,
                "argv": build["argv"], "stdin": build["stdin"], "workdir": wd,
                "session_id": session_id, "resumable": bool(session_id) or
                bool(info.get("resumable_by_id")),
                "session_note": session_note, "lock_released": released,
                "detail": "planned only — nothing was launched"}

    cap = float(timeout_s or config.get("max_task_seconds", 600))
    db().update_task(task_id, status="running")
    db().audit("agent_task_started",
               {"agent": key, "task_id": task_id, "workdir": wd,
                "argc": len(build["argv"])}, allowed=True)
    outcome = _run_process(build["argv"], cwd=wd, timeout_s=cap,
                           stdin_text=build["stdin"], task_id=task_id)
    released = release_project_lock(wd, task_id)
    cancelled_by_user = _consume_cancelled(task_id)
    final = ("cancelled" if (outcome.cancelled or cancelled_by_user) else
             "timeout" if outcome.timed_out else
             "done" if outcome.exit_code == 0 else "failed")
    db().update_task(task_id, status=final, meta={
        "argv": build["argv"], "exit_code": outcome.exit_code,
        "elapsed_s": round(outcome.elapsed_s, 2),
        "truncated": outcome.truncated, "lock_released": released,
        "stdout_tail": outcome.stdout[-4000:], "stderr_tail": outcome.stderr[-4000:],
        "session_id": session_id, "session_note": session_note,
    })
    db().audit(f"agent_task_{final}",
               {"agent": key, "task_id": task_id, "exit_code": outcome.exit_code,
                "elapsed_s": round(outcome.elapsed_s, 2)}, allowed=outcome.exit_code == 0)
    return {"ok": outcome.exit_code == 0, "task_id": task_id, "agent": key,
            "status": final, "workdir": wd, "session_id": session_id,
            "resumable": bool(session_id), "level_outcome": outcome.as_dict()}


def status(task_id: int) -> dict[str, Any]:
    """Everything known about one managed task, including whether it is alive."""
    task = db().get_task(int(task_id))
    if not task:
        return {"ok": False, "error": "not_found", "task_id": int(task_id)}
    with _PROC_LOCK:
        proc = _PROCS.get(int(task_id))
    running = bool(proc is not None and proc.poll() is None)
    lock = project_lock_owner(task.get("workdir") or "")
    return {
        "ok": True, "task_id": int(task_id), "task": task,
        "process_alive": running, "pid": (proc.pid if proc is not None else None),
        "lock": lock,
        "lock_owned_by_this_task": bool(lock and
                                        int(lock.get("task_id") or 0) == int(task_id)),
        "resumable_by_id": bool(task.get("session_id")),
    }


def continue_task(task_id: int, prompt: str, dry_run: bool = False,
                  timeout_s: Optional[float] = None) -> dict[str, Any]:
    """Continue ONE KNOWN task, by the session id recorded on its row.

    There is deliberately no fallback to "the most recent session": if the id
    is missing, this returns a blocker rather than resuming something the user
    did not select. For Codex that is the normal outcome (see
    INTEGRATION_STATUS['codex_session_id_discovery'] == BLOCKED).
    """
    task = db().get_task(int(task_id))
    if not task:
        return {"ok": False, "error": "not_found", "task_id": int(task_id)}
    agent = str(task.get("agent") or "")
    session_id = str(task.get("session_id") or "").strip()
    if agent not in AGENT_EXES:
        return {"ok": False, "error": "unknown_agent", "detail": agent}
    if not session_id:
        return {
            "ok": False, "error": "no_known_session", "task_id": int(task_id),
            "detail": (f"{agent} did not give this app a session id for task "
                       f"{task_id}, so it cannot be continued by id. This app "
                       f"will not fall back to the most recent session — start a "
                       f"new task or pass the id explicitly once you have it."),
        }
    wd, why = _resolve_workdir(agent, task.get("workdir"))
    if wd is None:
        return {"ok": False, "error": "bad_workdir", "detail": why}

    build = build_argv(agent, "resume", prompt, wd, session_id)
    problem = validate_argv(agent, build["argv"])
    if problem:
        db().audit("agent_argv_refused", {"agent": agent, "why": problem},
                   allowed=False)
        return {"ok": False, "error": "forbidden_flag", "detail": problem}

    acquired, lock_why = acquire_project_lock(wd, int(task_id), agent=agent)
    if not acquired:
        return {"ok": False, "error": "project_locked", "detail": lock_why}

    if dry_run:
        release_project_lock(wd, int(task_id))
        db().audit("agent_task_continue_planned",
                   {"agent": agent, "task_id": int(task_id),
                    "session_id": session_id}, allowed=True)
        return {"ok": True, "task_id": int(task_id), "agent": agent,
                "dry_run": True, "argv": build["argv"], "workdir": wd,
                "session_id": session_id,
                "detail": "planned only — nothing was launched"}

    cap = float(timeout_s or config.get("max_task_seconds", 600))
    db().update_task(int(task_id), status="running",
                     meta={"kind": "resume", "session_id": session_id})
    outcome = _run_process(build["argv"], cwd=wd, timeout_s=cap,
                           stdin_text=build["stdin"], task_id=int(task_id))
    released = release_project_lock(wd, int(task_id))
    cancelled_by_user = _consume_cancelled(int(task_id))
    final = ("cancelled" if (outcome.cancelled or cancelled_by_user) else
             "timeout" if outcome.timed_out else
             "done" if outcome.exit_code == 0 else "failed")
    db().update_task(int(task_id), status=final, meta={
        "kind": "resume", "session_id": session_id,
        "exit_code": outcome.exit_code, "lock_released": released,
        "stdout_tail": outcome.stdout[-4000:], "stderr_tail": outcome.stderr[-4000:],
    })
    return {"ok": outcome.exit_code == 0, "task_id": int(task_id), "agent": agent,
            "status": final, "session_id": session_id,
            "level_outcome": outcome.as_dict()}


def cancel_task(task_id: int, reason: str = "user cancelled") -> dict[str, Any]:
    """Stop a managed task: terminate its process and free its project lock."""
    tid = int(task_id)
    task = db().get_task(tid)
    if not task:
        return {"ok": False, "error": "not_found", "task_id": tid}
    # Mark BEFORE terminating: the task's own completion path consults this so a
    # killed child's non-zero exit code cannot downgrade 'cancelled' to 'failed'.
    _mark_cancelled(tid)
    with _PROC_LOCK:
        proc = _PROCS.get(tid)
    terminated = False
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            terminated = True
        except Exception as exc:
            log.warning("could not terminate task %s: %s", tid, exc)
    released = release_project_lock(task.get("workdir") or "", tid)
    db().update_task(tid, status="cancelled",
                     meta={"cancel_reason": reason, "terminated": terminated,
                           "lock_released": released})
    db().audit("agent_task_cancelled",
               {"task_id": tid, "agent": task.get("agent"),
                "terminated": terminated, "lock_released": released,
                "reason": reason}, allowed=True)
    return {"ok": True, "task_id": tid, "terminated": terminated,
            "lock_released": released, "status": "cancelled", "reason": reason}


def open_project(task_id: int) -> dict[str, Any]:
    """Open a task's working directory in Explorer (approved folders only)."""
    task = db().get_task(int(task_id))
    if not task:
        return {"ok": False, "error": "not_found", "task_id": int(task_id)}
    wd = str(task.get("workdir") or "")
    if not wd:
        return {"ok": False, "error": "no_workdir", "task_id": int(task_id)}
    result = _tools.call("open_folder", {"path": wd})
    return {"ok": result.ok, "task_id": int(task_id), "workdir": wd,
            "detail": result.detail, "error": result.error, "data": result.data}
