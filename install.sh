#!/usr/bin/env bash
# ===========================================================================
#  Jarvis installer - macOS / Linux (the POSIX counterpart to install.ps1).
#
#  Jarvis is a WINDOWS-ONLY application today. This script does not pretend
#  otherwise: by default it explains that, offers the development-only path,
#  and exits non-zero having installed nothing.
#
#  It exists so that the one-command story is honest on every platform, and so
#  that a contributor who only wants the settings web UI can get a Python
#  environment with --force.
#
#  Usage:
#      install.sh                 explain + refuse (exit 1)
#      install.sh --dry-run       print exactly what would happen (exit 0)
#      install.sh --force         set up a DEVELOPMENT-ONLY venv (the app
#                                 still does not run on this platform)
#      install.sh --dir <path>    where to put it
#      install.sh --version <ref> git branch/tag to install (default: main)
# ===========================================================================
set -u

REPO_URL="https://github.com/RizN91/jarvis.git"
REPO_SLUG="RizN91/jarvis"
RAW_BASE="https://raw.githubusercontent.com/RizN91/jarvis"
DEFAULT_REF="main"
WINDOWS_ONELINER="irm https://raw.githubusercontent.com/RizN91/jarvis/main/install.ps1 | iex"

FORCE=0
DRYRUN=0
DIR=""
VERSION="${DEFAULT_REF}"

# ------------------------------------------------------------------ arguments

while [ $# -gt 0 ]; do
    case "$1" in
        --force|-f)      FORCE=1 ;;
        --dry-run|-n)    DRYRUN=1 ;;
        --dir)           shift; DIR="${1:-}" ;;
        --dir=*)         DIR="${1#--dir=}" ;;
        --version)       shift; VERSION="${1:-}" ;;
        --version=*)     VERSION="${1#--version=}" ;;
        --ref)           shift; VERSION="${1:-}" ;;
        --ref=*)         VERSION="${1#--ref=}" ;;
        -h|--help)       echo "Usage: install.sh [--force] [--dry-run] [--dir PATH] [--version REF]"; exit 0 ;;
        *)               echo "install.sh: unknown option '$1' (try --help)" >&2; exit 2 ;;
    esac
    shift
done

# --------------------------------------------------------------------- output

if [ -t 1 ]; then
    C_RESET="$(printf '\033[0m')"; C_BOLD="$(printf '\033[1m')"
    C_CYAN="$(printf '\033[36m')"; C_GREEN="$(printf '\033[32m')"
    C_YELLOW="$(printf '\033[33m')"; C_RED="$(printf '\033[31m')"; C_GREY="$(printf '\033[90m')"
else
    C_RESET=""; C_BOLD=""; C_CYAN=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_GREY=""
fi

step()  { printf '\n%s==> %s%s\n' "${C_CYAN}" "$1" "${C_RESET}"; }
ok()    { printf '    %s[ok]%s  %s\n' "${C_GREEN}" "${C_RESET}" "$1"; }
info()  { printf '    %s\n' "$1"; }
warn()  { printf '    %s[!]%s   %s\n' "${C_YELLOW}" "${C_RESET}" "$1"; }
fail()  { printf '    %s[x]%s   %s\n' "${C_RED}" "${C_RESET}" "$1"; }

# ---------------------------------------------------------------------- layout

if [ -z "${DIR}" ]; then
    DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/jarvis/app"
fi
case "${DIR}" in
    /*) : ;;
    *)  DIR="$(pwd)/${DIR}" ;;
esac
VENV="${DIR}/.venv"
VPY="${VENV}/bin/python"

# ------------------------------------------------------------------ platform

HOST="other"
case "$(uname -s 2>/dev/null || echo unknown)" in
    MINGW*|MSYS*|CYGWIN*) HOST="windows" ;;
    Darwin)               HOST="macos" ;;
    Linux)                HOST="linux" ;;
    *)                    HOST="other" ;;
esac

# -------------------------------------------------------------------- python

find_python() {
    for cand in python3.11 python3.12 python3 python; do
        if command -v "${cand}" >/dev/null 2>&1; then
            if "${cand}" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) and sys.maxsize > 2**32 else 1)' 2>/dev/null; then
                printf '%s' "${cand}"
                return 0
            fi
        fi
    done
    return 1
}

# --------------------------------------------------------------------- banner

cat <<EOF

  ======================================================================
   Jarvis installer (macOS / Linux)
  ======================================================================

   Target directory: ${DIR}
   Git ref:          ${VERSION}

   Jarvis is a WINDOWS-ONLY application. Its core uses Win32 APIs that
   have no equivalent here:
     * global input hooks        - SetWindowsHookEx
     * synthetic text insertion  - SendInput / clipboard
     * the floating overlay      - UpdateLayeredWindow
     * secret storage            - Windows Credential Manager / DPAPI
     * session notifications     - WTS WM_WTSSESSION_CHANGE
     * tray + settings window    - pystray / WebView2

   Roadmap: docs/INSTALL.md#platform-support
EOF

if [ "${DRYRUN}" -eq 1 ]; then
    printf '\n   DRY RUN: nothing will be created, downloaded or changed.\n\n'
fi

# ------------------------------------------------------------------ dry run

if [ "${DRYRUN}" -eq 1 ]; then
    step "What this script would do (dry run)"
    if PY="$(find_python)"; then
        ok "found a suitable Python: ${PY} ($("${PY}" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])'))"
    else
        warn "no 64-bit Python 3.11+ found on PATH (checked python3.11, python3.12, python3, python)"
    fi
    info "would clone ${REPO_URL} (${VERSION}) into ${DIR}, or download ${RAW_BASE}/archive/refs/heads/${VERSION}.zip if git is absent"
    info "would create ${VENV} and install requirements.txt into it (DEVELOPMENT env only - the app cannot run here)"
    info "would NOT create any Windows shortcut, service, registry entry or startup item"
    step "Dry run complete - nothing was changed"
    exit 0
fi

# ------------------------------------------------------------ the honest gate

if [ "${HOST}" = "windows" ]; then
    step "You are running a POSIX shell on Windows"
    fail "This script will not install Jarvis here - Windows uses a different path."
    info "On Windows, open PowerShell and paste this ONE line instead:"
    printf '\n      %s%s%s\n\n' "${C_BOLD}" "${WINDOWS_ONELINER}" "${C_RESET}"
    info "(install.ps1 finds Python, clones Jarvis, builds the venv and adds a"
    info " Start Menu shortcut. It never elevates and never installs Python.)"
    if [ "${FORCE}" -eq 0 ]; then
        info ""
        info "Nothing was installed. Re-run with --force only if you specifically"
        info "want a POSIX development environment (settings web UI) on Windows."
        exit 1
    fi
    warn "--force given: continuing to build a DEVELOPMENT environment only."
else
    if [ "${FORCE}" -eq 0 ]; then
        step "Jarvis does not run on this platform yet"
        fail "Nothing was installed."
        info ""
        info "The application needs the Win32 layer listed above. What IS portable"
        info "today is the settings web UI (jarvis/ui/web), which you can develop"
        info "in a plain browser with no Python at all."
        info ""
        info "If you specifically want a development Python environment for that UI:"
        printf '\n      %scurl -fsSL %s/install.sh | bash -s -- --force%s\n\n' "${C_BOLD}" "${RAW_BASE}" "${C_RESET}"
        info "or, from a checkout:   bash install.sh --force"
        info ""
        info "That creates a venv and installs requirements.txt, but the app itself"
        info "still will not start here - this script will say so, not fake success."
        exit 1
    fi
    warn "--force given: building a DEVELOPMENT environment only."
fi

# ------------------------------------------------------ force: dev environment

if ! PY="$(find_python)"; then
    step "Looking for a 64-bit Python 3.11 or newer"
    fail "No suitable interpreter found (checked python3.11, python3.12, python3, python)."
    info "Install 64-bit Python 3.11+ from https://www.python.org/downloads/ and re-run."
    info "This script will not install Python for you."
    exit 1
fi
step "Looking for a 64-bit Python 3.11 or newer"
ok "using ${PY} ($("${PY}" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])'))"

HAVE_GIT=0
if command -v git >/dev/null 2>&1; then HAVE_GIT=1; fi

if [ -d "${DIR}/.git" ]; then
    step "Updating the existing checkout at ${DIR}"
    if ! ( cd "${DIR}" && git fetch --tags --quiet && git checkout --quiet "${VERSION}" && git pull --ff-only --quiet ); then
        fail "git could not update ${DIR}. Use --dir <other path> for a clean install."
        exit 1
    fi
    ok "updated with git pull"
elif [ -d "${DIR}/jarvis" ]; then
    step "An existing source tree is already at ${DIR} - leaving it in place"
    ok "reusing ${DIR}"
else
    if [ "${HAVE_GIT}" -eq 1 ]; then
        step "Cloning Jarvis (${VERSION}) with git"
        mkdir -p "$(dirname "${DIR}")"
        if ! git clone --depth 1 --branch "${VERSION}" "${REPO_URL}" "${DIR}"; then
            fail "git clone failed (no network, or the ref does not exist)."
            exit 1
        fi
        ok "cloned with git"
    else
        step "git is not installed - downloading the release zip instead"
        ZIP_URL="${RAW_BASE}/archive/refs/heads/${VERSION}.zip"
        [ "${VERSION}" = "${DEFAULT_REF}" ] || ZIP_URL="https://github.com/${REPO_SLUG}/archive/refs/tags/${VERSION}.zip"
        TMP="$(mktemp -d 2>/dev/null || echo /tmp/jarvis-install-$$)"
        mkdir -p "${TMP}"
        if ! curl -fsSL "${ZIP_URL}" -o "${TMP}/jarvis.zip"; then
            fail "download failed: ${ZIP_URL}"
            exit 1
        fi
        if ! unzip -q "${TMP}/jarvis.zip" -d "${TMP}"; then
            fail "could not unpack ${TMP}/jarvis.zip"
            exit 1
        fi
        SRC="$(find "${TMP}" -maxdepth 1 -mindepth 1 -type d | head -n 1)"
        if [ -z "${SRC}" ] || [ ! -f "${SRC}/requirements.txt" ]; then
            fail "the downloaded archive did not contain the Jarvis source tree."
            exit 1
        fi
        mkdir -p "${DIR}"
        cp -R "${SRC}/." "${DIR}/"
        rm -rf "${TMP}"
        ok "downloaded and unpacked the release zip"
    fi
fi

step "Creating a development virtual environment with ${PY}"
if [ ! -x "${VPY}" ]; then
    if ! "${PY}" -m venv "${VENV}"; then
        fail "could not create ${VENV}"
        exit 1
    fi
    ok "created ${VENV}"
else
    ok "reusing the existing ${VENV}"
fi

step "Upgrading pip inside the venv (your global Python is never touched)"
"${VPY}" -m pip install --upgrade pip >/dev/null 2>&1 || warn "pip upgrade failed (offline?) - continuing"

step "Installing requirements.txt"
warn "Some dependencies are Windows-only (pywin32, comtypes, pythonnet, ...); the"
warn "installer will report what failed rather than pretending it all worked."
"${VPY}" -m pip install -r "${DIR}/requirements.txt" || warn "some dependencies did not install (expected on this platform)"

DEV_OK=0
if ( cd "${DIR}" && "${VPY}" -c 'import jarvis' ) >/dev/null 2>&1; then
    DEV_OK=1
fi

step "Result"
if [ "${DEV_OK}" -eq 1 ]; then
    ok "a Python environment for the settings web UI / development is ready at ${VENV}"
else
    fail "the development environment could not be completed (dependency install failed)."
fi
cat <<EOF

  ${C_YELLOW}${C_BOLD}THE JARVIS APPLICATION STILL DOES NOT RUN ON THIS PLATFORM.${C_RESET}
  No Windows-only component was installed, and none can be. This is a
  development environment for jarvis/ui/web only.

  To work on the settings UI with no Python at all, just open
  ${DIR}/jarvis/ui/web/index.html in a browser.

  For the real application, use Windows:
      ${WINDOWS_ONELINER}

EOF

if [ "${DEV_OK}" -eq 1 ]; then
    exit 0
else
    exit 1
fi
