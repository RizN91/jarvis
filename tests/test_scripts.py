#!/usr/bin/env python
"""Non-destructive smoke test for Jarvis's packaging and launch scripts.

What this proves, without mutating anything:

  * every expected file exists and is non-empty
  * the package compiles  (``python -m compileall -q jarvis`` exits 0)
  * ``uninstall.cmd /dryrun`` lists what WOULD be removed and deletes nothing
    (proved with a marker file left in %LOCALAPPDATA%\\Jarvis)
  * run.cmd / run-console.cmd / setup.cmd / package.cmd reference the package
    correctly and use %~dp0-relative paths
  * no script contains an obviously unsafe construct (no
    Set-ExecutionPolicy, no Defender changes, no rm/del against a fixed
    system path)
  * the PyInstaller spec is valid Python, is a windowed one-folder build, and
    bundles jarvis/ui/web where jarvis.ui.window.web_root() looks
  * requirements.lock.txt is fully pinned and frozen against a stated Python
    version

Run it either way:

    python tests/test_scripts.py
    python -m pytest tests/test_scripts.py

It has no pytest dependency and never runs the real uninstaller, setup.cmd or
package.cmd.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests._console import use_utf8_stdout  # noqa: E402

use_utf8_stdout()

CMD_SCRIPTS = ["run.cmd", "run-console.cmd", "setup.cmd", "package.cmd",
               "uninstall.cmd"]

# The one-command installers. install.ps1 is the Windows path; install.sh is
# its honest POSIX counterpart.
PS_SCRIPTS = ["install.ps1"]
SH_SCRIPTS = ["install.sh"]

REQUIRED_FILES = CMD_SCRIPTS + PS_SCRIPTS + SH_SCRIPTS + [
    "requirements.txt",
    "requirements.lock.txt",
    "pyproject.toml",
    "MANIFEST.in",
    "setup.sh",
    "installer/jarvis.spec",
    "docs/INSTALL.md",
    "README.md",
    "CONTRIBUTING.md",
    "ARCHITECTURE.md",
    "SECURITY.md",
]

# Direct dependencies the package genuinely needs (see requirements.txt).
DIRECT_DEPS = [
    "openai", "websockets", "httpx", "sounddevice", "numpy", "pywin32",
    "comtypes", "pillow", "pystray", "pywebview", "sherpa-onnx",
    "pythonnet", "sentencepiece",
]

# Substrings that must never appear in a shipped helper script.
BANNED_SUBSTRINGS = [
    "set-executionpolicy",
    "get-executionpolicy",
    "set-mppreference",
    "add-mppreference",
    "set-mppreference",
    "mpcmdrun",
    "defender",
    "powershell",
    "pwsh",
    "diskpart",
    "format c:",
    "takeown /f c:\\",
]

# A recursive delete is only ever acceptable against a %-variable or a
# %~dp0-relative path - never against a fixed system location.
DESTRUCTIVE_RE = re.compile(r"\b(rd|rmdir|del|del\s+/[a-z]+)\b", re.IGNORECASE)
SYSTEM_ROOT_RE = re.compile(
    r"(c:\\|%systemroot%|%windir%|%systemdrive%|%programfiles%|%programdata%)",
    re.IGNORECASE,
)

CHECKS: list[tuple[str, callable]] = []


def check(name: str):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


# --------------------------------------------------------------------- helpers

def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout,
        errors="replace",
    )


def local_app_data() -> Path | None:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "Jarvis" if base else None


def powershell() -> str | None:
    """Path to Windows PowerShell, or None when it is not available."""
    for name in ("powershell", "powershell.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


# Byte values, spelled without escapes so a CRLF file cannot mangle them.
CR = 13
LF = 10
CRLF = bytes([CR, LF])

# ---------------------------------------------------------------------- checks

@check("every expected file exists and is non-empty")
def check_files_exist():
    missing, empty = [], []
    for rel in REQUIRED_FILES:
        p = ROOT / rel
        if not p.is_file():
            missing.append(rel)
        elif p.stat().st_size == 0:
            empty.append(rel)
    if missing or empty:
        return False, f"missing={missing} empty={empty}"
    sizes = {rel: (ROOT / rel).stat().st_size for rel in REQUIRED_FILES}
    return True, f"{len(REQUIRED_FILES)} files, {sum(sizes.values())} bytes total"


@check("the package compiles: python -m compileall -q jarvis")
def check_compileall():
    proc = run([sys.executable, "-m", "compileall", "-q", "jarvis"])
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        return False, f"exit={proc.returncode} {out.strip()[:400]}"
    pkg = ROOT / "jarvis"
    n_py = len(list(pkg.rglob("*.py")))
    return True, f"exit=0, {n_py} source files compiled"


@check("uninstall.cmd /dryrun lists what WOULD be removed and deletes nothing")
def check_uninstall_dryrun():
    if os.name != "nt" or not os.environ.get("LOCALAPPDATA"):
        return True, "SKIPPED (needs Windows and LOCALAPPDATA)"

    data = local_app_data()
    assert data is not None
    data.mkdir(parents=True, exist_ok=True)
    marker = data / "_smoke_test_marker.txt"
    marker.write_text("if this file is gone, uninstall.cmd /dryrun was "
                      "destructive\n", encoding="utf-8")

    problems: list[str] = []
    try:
        proc = run(["cmd", "/c", str(ROOT / "uninstall.cmd"), "/dryrun"],
                   timeout=60)
        out = (proc.stdout or "") + (proc.stderr or "")
        low = out.lower()

        if proc.returncode != 0:
            problems.append(f"exit code {proc.returncode}")
        if not marker.exists():
            problems.append("THE MARKER FILE WAS DELETED - dryrun was destructive")
        if not data.is_dir():
            problems.append("the user data directory was removed")

        for needle in ["would remove", ".venv", "jarvis",
                       "jarvis/openai_api_key", "cmdkey /delete"]:
            if needle not in low:
                problems.append(f"output never mentions {needle!r}")
        if "nothing has been deleted yet" in low:
            problems.append("dryrun fell through to the interactive prompt")
        if "yes" in low and "type yes" in low:
            problems.append("dryrun asked for confirmation")
    finally:
        try:
            marker.unlink()
        except OSError:
            pass

    if problems:
        return False, "; ".join(problems)
    return True, ("exit=0, deletion list printed, marker survived, data dir "
                  "intact")


@check("run.cmd launches with pythonw and resolves its own folder")
def check_run_cmd():
    text = read(ROOT / "run.cmd")
    problems = []
    if "%~dp0" not in text:
        problems.append("does not cd to its own directory with %~dp0")
    if "jarvis" not in text:
        problems.append("never mentions the jarvis package")
    if "pythonw" not in text:
        problems.append("does not use pythonw (would show a console)")
    if "-m jarvis" not in text:
        problems.append("does not run 'python -m jarvis'")
    if ".venv" not in text:
        problems.append("does not prefer the local .venv")
    if "import jarvis" not in text:
        problems.append("no import check / clear error path")
    if problems:
        return False, "; ".join(problems)
    return True, "%~dp0 + .venv + pythonw -m jarvis + import guard"


@check("run-console.cmd is the visible-console variant")
def check_run_console():
    text = read(ROOT / "run-console.cmd")
    problems = []
    if "%~dp0" not in text or ".venv" not in text:
        problems.append("missing %~dp0 / .venv resolution")
    if "-m jarvis" not in text:
        problems.append("does not run 'python -m jarvis'")
    if "pythonw" in text:
        problems.append("uses pythonw, so logs would be invisible")
    if "import jarvis" not in text:
        problems.append("no import check / clear error path")
    if problems:
        return False, "; ".join(problems)
    return True, "console python + %~dp0 + .venv + import guard"


@check("setup.cmd creates .venv from the lockfile, per-user and idempotently")
def check_setup_cmd():
    text = read(ROOT / "setup.cmd")
    problems = []
    for needle, why in [
        ("jarvis", "never mentions the package"),
        ("%~dp0", "no %~dp0-relative paths"),
        ("requirements.lock.txt", "does not install the pinned lockfile"),
        ("-m venv", "does not create a venv"),
        (".venv", "no .venv target"),
        ("if exist \"%VENV%\\Scripts\\python.exe\"", "no idempotency guard"),
        ("version_info[:2] >= (3,11)", "does not require Python 3.11+"),
    ]:
        if needle not in text:
            problems.append(why)
    if "pip install --user" in text or "python -m pip install pyinstaller" in text:
        problems.append("could mutate the global environment")
    if problems:
        return False, "; ".join(problems)
    return True, ("per-user .venv, lockfile install, reuses an existing venv, "
                  "exact Python 3.11 check")


@check("package.cmd fails honestly when PyInstaller is absent")
def check_package_cmd():
    text = read(ROOT / "package.cmd")
    problems = []
    for needle, why in [
        ("jarvis.spec", "does not build from the spec"),
        ("%~dp0", "no %~dp0-relative paths"),
        ("PyInstaller", "never mentions PyInstaller"),
        ("pip install pyinstaller", "no install instructions on the failure path"),
        ("exit /b 1", "does not exit non-zero when it cannot build"),
        ("if errorlevel 1 goto :no_pyinstaller", "no explicit absence check"),
    ]:
        if needle not in text:
            problems.append(why)
    if "pip install" in text and "pip install pyinstaller" not in text:
        problems.append("installs something other than the documented tool")
    if re.search(r"^\s*(pip|python[^\r\n]*)\s+install\s+pyinstaller\s*$", text,
                 re.IGNORECASE | re.MULTILINE):
        problems.append("actually installs PyInstaller instead of telling you to")
    if problems:
        return False, "; ".join(problems)
    return True, "spec-driven, checks first, prints the pip command, exit 1"


@check("uninstall.cmd is confirm-gated and lists the credential removal")
def check_uninstall_cmd():
    text = read(ROOT / "uninstall.cmd")
    low = text.lower()
    problems = []
    for needle, why in [
        ("cmdkey /delete:jarvis/openai_api_key",
         "no exact cmdkey /delete syntax for the credential"),
        ("localappdata", "does not target %LOCALAPPDATA%\\Jarvis"),
        (".venv", "does not target the local virtual environment"),
        ("/dryrun", "no /dryrun mode"),
        ("set /p \"confirm=", "no interactive confirmation prompt"),
        ("==\"yes\"", "does not require an explicit YES"),
    ]:
        if needle not in low:
            problems.append(why)
    if problems:
        return False, "; ".join(problems)
    return True, ("dryrun mode, YES-gated, removes .venv + %LOCALAPPDATA%"
                  "\\Jarvis + cmdkey credential")


@check("no script contains an obviously unsafe construct")
def check_no_unsafe_constructs():
    problems = []
    for rel in CMD_SCRIPTS:
        p = ROOT / rel
        for lineno, raw in enumerate(read(p).splitlines(), 1):
            line = raw.strip()
            comment = line.lower().startswith("rem")
            stripped = line.lstrip("rem ").strip()
            low = stripped.lower()
            for bad in BANNED_SUBSTRINGS:
                if bad in low and not comment:
                    problems.append(f"{rel}:{lineno} contains {bad!r}")
            m = DESTRUCTIVE_RE.search(low)
            if m and SYSTEM_ROOT_RE.search(low):
                problems.append(
                    f"{rel}:{lineno} recursively deletes a fixed system path: "
                    f"{stripped}")
    if problems:
        return False, "; ".join(problems)
    return True, (f"{len(CMD_SCRIPTS)} scripts scanned: no execution-policy, "
                  f"Defender, PowerShell or system-root delete")


@check("all .cmd files use CRLF line endings")
def check_crlf():
    bad = []
    for rel in CMD_SCRIPTS:
        raw = (ROOT / rel).read_bytes()
        if b"\r\n" not in raw:
            bad.append(f"{rel} (no CRLF at all)")
            continue
        lone = re.findall(rb"(?<!\r)\n", raw)
        if lone:
            bad.append(f"{rel} ({len(lone)} bare LF)")
    if bad:
        return False, "; ".join(bad)
    return True, f"all 5 scripts are CRLF-only and BOM-free-ish"


@check("the PyInstaller spec is valid, windowed, one-folder, and bundles the UI")
def check_spec():
    path = ROOT / "installer" / "jarvis.spec"
    text = read(path)
    try:
        ast.parse(text, filename=str(path))     # must be valid Python
    except SyntaxError as exc:
        return False, f"spec is not valid Python: {exc}"
    problems = []
    for needle, why in [
        ('"jarvis/ui/web"', "does not bundle the UI at the path web_root() reads"),
        ("jarvis", "does not reference the package"),
        ("__main__.py", "does not use the -m entry point as the build target"),
        ("console=False", "not a windowed build"),
        ("COLLECT(", "not a one-folder build"),
        ("exclude_binaries=True", "not a one-folder build"),
        ("web_root()", "does not document the web_root() contract"),
        ("sys._MEIPASS", "does not explain the _MEIPASS lookup"),
    ]:
        if needle not in text:
            problems.append(why)
    if "console=True" in text:
        problems.append("console=True appears in the spec")
    if problems:
        return False, "; ".join(problems)
    return True, ("valid Python, one-folder, console=False, bundles "
                  "jarvis/ui/web to match web_root()")


@check("requirements.txt lists every direct dependency")
def check_requirements():
    text = read(ROOT / "requirements.txt").lower()
    missing = [d for d in DIRECT_DEPS if d not in text]
    if missing:
        return False, f"not listed: {missing}"
    n = len([l for l in text.splitlines()
             if l.strip() and not l.strip().startswith("#")])
    return True, f"{n} direct dependencies listed ({len(DIRECT_DEPS)} required)"


@check("requirements.lock.txt is fully pinned and names its Python version")
def check_lockfile():
    text = read(ROOT / "requirements.lock.txt")
    lines = text.splitlines()
    comments = [l for l in lines if l.strip().startswith("#")]

    if not any(re.search(r"3\.11", c) for c in comments):
        return False, "no CPython 3.11 version in the header comment"
    if not any("pip freeze" in c for c in comments):
        return False, "does not state how it was generated"

    pins, bad = [], []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line or line.endswith("*"):
            bad.append(line)
            continue
        pins.append(line)
    if bad:
        return False, f"unpinned / malformed lines: {bad}"
    if len(pins) < 20:
        return False, f"only {len(pins)} pins - the transitive closure is missing"
    for line in pins:
        name = line.split("==")[0]
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", name):
            return False, f"odd package name on line {line!r}"
    if "hermes" in text.lower():
        return False, "the lockfile includes the host's own tooling"
    if any(re.search(r"^%s==" % re.escape(d), text, re.IGNORECASE | re.MULTILINE)
           for d in DIRECT_DEPS) is False:
        return False, "no direct dependency is pinned at all"
    return True, f"{len(pins)} exact pins, frozen against CPython 3.11, header intact"


@check("documentation files cover the required operator topics")
def check_docs():
    problems = []
    install = read(ROOT / "docs" / "INSTALL.md")
    for needle in ["Windows 11", "Python 3.11", "WebView2", "Credential Manager",
                   "Jarvis/openai_api_key", "%LOCALAPPDATA%\\Jarvis",
                   "cmdkey /delete:Jarvis/openai_api_key", "run-console.cmd",
                   "jarvis.log", "[REDACTED]", "uninstall.cmd /dryrun",
                   "microphone"]:
        if needle not in install:
            problems.append(f"INSTALL.md lacks {needle!r}")

    readme = read(ROOT / "README.md")
    for needle in ["$0.05", "$0.0045", "gpt-transcribe",
                   "Windows Credential Manager", "not supported"]:
        if needle.lower() not in readme.lower():
            problems.append(f"README.md lacks {needle!r}")

    # The contributor guide is the PUBLIC version of the internal agent notes, so
    # it has to carry the same warnings: the cheap first check, which suites cost
    # real money, and the log redaction rule. (AGENTS.md is a maintainer-only file
    # and is deliberately not published, so it must not be required here — a fresh
    # clone would otherwise fail this check.)
    guide = read(ROOT / "CONTRIBUTING.md")
    for needle in ["compileall", "test_live_api.py", "test_transcribe_api.py",
                   "test_mic_loopback.py", "spend your money", "redact"]:
        if needle.lower() not in guide.lower():
            problems.append(f"CONTRIBUTING.md lacks {needle!r}")

    if problems:
        return False, "; ".join(problems)
    return True, "INSTALL.md, README.md and CONTRIBUTING.md cover all required topics"


@check("pyproject.toml packages the app and ships the settings web assets")
def check_pyproject():
    text = read(ROOT / "pyproject.toml")
    problems = []
    for needle, why in [
        ('name = "jarvis-voice"', "the distribution name is not jarvis-voice"),
        ('requires-python = ">=3.11"', "does not require Python 3.11+"),
        ('jarvis = "jarvis.__main__:main"', "missing the 'jarvis' entry point"),
        ('"jarvis.ui" = ["web/*"]', "does not ship the jarvis/ui/web assets"),
        ("setuptools", "no setuptools build backend"),
    ]:
        if needle not in text:
            problems.append(why)
    for dep in ("openai", "websockets", "sounddevice", "numpy", "sherpa-onnx",
                "pywebview", "pystray", "pythonnet", "pillow"):
        if dep not in text:
            problems.append(f"dependency {dep!r} is not declared")
    if problems:
        return False, "; ".join(problems)
    return True, ("jarvis-voice, requires-python >=3.11, entry point 'jarvis', "
                  "package-data jarvis.ui web/*, direct deps mirrored")


@check("setup.sh is honest on macOS/Linux and never fakes an install")
def check_setup_sh():
    p = ROOT / "setup.sh"
    if not p.is_file():
        return False, "setup.sh is missing"
    text = read(p)
    problems = []
    if "Windows" not in text:
        problems.append("never names Windows as the supported platform")
    if "pip install" in text:
        problems.append("runs an install instead of stopping")
    if "exit 1" not in text:
        problems.append("does not exit non-zero")
    if "python3.11" not in text:
        problems.append("does not check for a Python 3.11+ interpreter")
    if "INSTALL.md#platform-support" not in text:
        problems.append("does not point at the platform-support roadmap")
    if problems:
        return False, "; ".join(problems)
    return True, ("checks the interpreter, names Windows, installs nothing, "
                  "points at INSTALL.md, exits 1")


# Constructs that must never appear in the Windows one-command installer.
# NOTE: the word "Defender" IS allowed - the banner promises not to touch it -
# so it is deliberately not in this list. The real rule is: never *change* it.
PS_BANNED = [
    "set-executionpolicy",
    "get-executionpolicy",
    "add-mppreference",
    "set-mppreference",
    "remove-mppreference",
    "mpcmdrun",
    "start-process",
    "runas",
    "winget install",
    "choco install",
    "msiexec",
]


@check("install.ps1 is a genuine one-command install with no unsafe construct")
def check_install_ps1():
    p = ROOT / "install.ps1"
    if not p.is_file():
        return False, "install.ps1 is missing"
    text = read(p)
    low = text.lower()
    problems = []

    for needle in [
        "irm https://raw.githubusercontent.com/rizn91/jarvis/main/install.ps1 | iex",
        "$env:localappdata",
        "'py'",                       # the py launcher is tried at all
        "-3.11", "-3.12",             # exact new launchers before bare py -3
        "'python'",                   # final fallback
        "https://www.python.org/downloads/",
        "git clone",
        "archive/refs",               # release-zip fallback when git is absent
        "git -c",                     # updates an existing checkout with pull
        "setup.cmd",                  # reuses the tested install path
        "jarvis_python_base",         # passes the chosen Python to setup.cmd
        "wscript.shell",              # creates the shortcut
        "-m jarvis",                  # the shortcut runs the tray app
        "dryrun",                     # -DryRun exists
        "uninstall",                  # -Uninstall exists
        "would remove",               # uninstall has a dry-run listing
        "remove-item",                # uninstall can actually remove things
    ]:
        if needle not in low:
            problems.append(f"install.ps1 never mentions {needle!r}")

    for bad in PS_BANNED:
        if bad in low:
            problems.append(f"install.ps1 contains the banned construct {bad!r}")

    if "$psscriptroot" in low:
        problems.append("install.ps1 uses $PSScriptRoot, so `irm | iex` would break")

    # A recursive delete is only acceptable against a script variable, never
    # against a literal drive path.
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip().lower()
        if "remove-item" in line and "-recurse" in line:
            if "$" not in line:
                problems.append(f"line {lineno} recursively deletes a literal path: {raw.strip()}")
            elif re.search(r"[a-z]:\\", line):
                problems.append(f"line {lineno} recursively deletes a fixed drive path: {raw.strip()}")

    if problems:
        return False, "; ".join(problems)
    return True, ("one-line command documented, Python detection, git + zip "
                  "fallbacks, setup.cmd reused, shortcut, -DryRun and -Uninstall")


@check("install.ps1 is CRLF and install.sh is LF")
def check_installer_line_endings():
    problems = []
    ps = (ROOT / "install.ps1").read_bytes()
    if CRLF not in ps:
        problems.append("install.ps1 has no CRLF at all")
    lone = sum(1 for i, c in enumerate(ps)
               if c == LF and (i == 0 or ps[i - 1] != CR))
    if lone:
        problems.append(f"install.ps1 has {lone} bare LF (must be CRLF-only)")
    sh = (ROOT / "install.sh").read_bytes()
    if CRLF in sh:
        problems.append("install.sh contains CRLF (POSIX scripts must be LF)")
    if problems:
        return False, "; ".join(problems)
    return True, "install.ps1 is CRLF-only, install.sh is LF-only"

@check("install.ps1 -DryRun exits 0 and creates nothing")
def check_install_ps1_dryrun():
    ps = powershell()
    if not ps:
        return True, "SKIPPED (no powershell on PATH)"

    app_dir = local_app_data()
    if app_dir is not None:
        app_dir = app_dir / "app"
    existed_before = bool(app_dir and app_dir.exists())

    proc = run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(ROOT / "install.ps1"), "-DryRun"], timeout=120)
    out = (proc.stdout or "") + (proc.stderr or "")
    low = out.lower()

    problems = []
    if proc.returncode != 0:
        problems.append(f"exit code {proc.returncode}")

    for needle in ["dry run", "would run", "would create"]:
        if needle not in low:
            problems.append(f"the dry run output never mentions {needle!r}")

    if app_dir is not None and str(app_dir).lower() not in low:
        problems.append("the dry run never names the default install directory")

    # A dry run must never claim to have done the work.
    for lie in ["cloned with git", "setup.cmd finished", "[ok]  installed"]:
        if lie in low:
            problems.append(f"the dry run reported work it did not do: {lie!r}")

    if app_dir is not None and not existed_before and app_dir.exists():
        problems.append(f"the dry run CREATED {app_dir}")

    if problems:
        return False, "; ".join(problems)
    return True, "exit=0, printed the plan, created no app directory"


@check("install.ps1 -Uninstall -DryRun lists the plan and deletes nothing")
def check_install_ps1_uninstall_dryrun():
    ps = powershell()
    if not ps:
        return True, "SKIPPED (no powershell on PATH)"

    data = local_app_data()
    if data is None:
        return True, "SKIPPED (no LOCALAPPDATA)"

    data.mkdir(parents=True, exist_ok=True)
    marker = data / "_smoke_test_marker_ps1.txt"
    marker.write_text("if this file is gone, install.ps1 -Uninstall -DryRun "
                      "was destructive\n", encoding="utf-8")

    problems: list[str] = []
    try:
        proc = run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(ROOT / "install.ps1"), "-Uninstall", "-DryRun"],
                   timeout=120)
        out = (proc.stdout or "") + (proc.stderr or "")
        low = out.lower()

        if proc.returncode != 0:
            problems.append(f"exit code {proc.returncode}")
        if not marker.exists():
            problems.append("THE MARKER FILE WAS DELETED - the dry run was destructive")
        if not data.is_dir():
            problems.append("the data directory was removed")
        for needle in ["would remove", ".venv", "jarvis", "openai_api_key",
                       "dry run"]:
            if needle not in low:
                problems.append(f"the plan never mentions {needle!r}")
        if "removed:" in low:
            problems.append("the dry run reported a removal it did not make")
    finally:
        try:
            marker.unlink()
        except OSError:
            pass

    if problems:
        return False, "; ".join(problems)
    return True, "exit=0, full plan printed, marker survived, data dir intact"


@check("install.sh refuses without --force, installs nothing, fakes nothing")
def check_install_sh():
    p = ROOT / "install.sh"
    if not p.is_file():
        return False, "install.sh is missing"
    bash = shutil.which("bash")
    if not bash:
        return True, "SKIPPED (no bash on PATH)"

    probe = ROOT / "_install_sh_probe"
    problems: list[str] = []
    try:
        # 1. no --force: it must refuse and create nothing.
        proc = run([bash, "install.sh", "--dir", "_install_sh_probe"], timeout=60)
        out = (proc.stdout or "") + (proc.stderr or "")
        low = out.lower()
        if proc.returncode == 0:
            problems.append("exited 0 without --force (it must refuse)")
        if "windows" not in low:
            problems.append("never says Jarvis is Windows-only")
        if "install.ps1" not in out:
            problems.append("does not point Windows users at install.ps1")
        if probe.exists():
            problems.append("created the target directory even though it refused")
        for fake in ["successfully installed", "installation complete",
                     "setup finished"]:
            if fake in low:
                problems.append(f"claims success it did not achieve: {fake!r}")

        # 2. --dry-run may exit 0, but must still create nothing.
        dry = run([bash, "install.sh", "--dry-run", "--dir", "_install_sh_probe"],
                  timeout=60)
        dout = (dry.stdout or "") + (dry.stderr or "")
        dlow = dout.lower()
        if dry.returncode != 0:
            problems.append(f"--dry-run exited {dry.returncode}")
        if "would" not in dlow:
            problems.append("--dry-run does not describe what it would do")
        if "cannot run here" not in dlow:
            problems.append("--dry-run never says the app cannot run here")
        if probe.exists():
            problems.append("--dry-run created the target directory")
    finally:
        if probe.exists():
            shutil.rmtree(probe, ignore_errors=True)

    if problems:
        return False, "; ".join(problems)
    return True, ("refuses (exit 1) and creates nothing without --force; "
                  "--dry-run exits 0 and creates nothing; never claims success")


# ------------------------------------------------------------------- the runner

def run_all() -> list[tuple[str, bool, str]]:
    results = []
    for name, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as exc:  # a broken check is a failed check
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append((name, ok, detail))
    return results


def main() -> int:
    print("=" * 74)
    print(" Jarvis - run-script / packaging smoke test (non-destructive)")
    print(f" root: {ROOT}")
    print("=" * 74)
    rows = run_all()
    for name, ok, detail in rows:
        print(f"- {'PASS' if ok else 'FAIL'}  {name}")
        if detail:
            print(f"        {detail}")
    passed = sum(1 for _, ok, _ in rows if ok)
    failed = len(rows) - passed
    print("-" * 74)
    print("TOTAL %d  PASSED %d  FAILED %d" % (len(rows), passed, failed))
    return 0 if failed == 0 else 1


def test_scripts_smoke():
    """pytest entry point (no pytest import needed)."""
    failures = [f"{name}: {detail}"
                for name, ok, detail in run_all() if not ok]
    assert not failures, "script smoke test failures:\n" + "\n".join(failures)


if __name__ == "__main__":
    sys.exit(main())
