#!/usr/bin/env bash
# ===========================================================================
#  Jarvis setup - macOS / Linux.
#
#  Jarvis is a WINDOWS-ONLY application. This script does not pretend to
#  install it: it performs the same interpreter check as setup.cmd so the
#  message can be specific, then states the position honestly and exits
#  non-zero. See docs/INSTALL.md "Platform support" for what a port needs.
# ===========================================================================
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==========================================================================="
echo " Jarvis setup (macOS / Linux)"
echo " project folder: ${ROOT}"
echo "==========================================================================="
echo

# ---- look for a 64-bit CPython 3.11+ (the versions the lockfile targets) ----
FOUND=""
for cand in python3.11 python3 python; do
    if command -v "${cand}" >/dev/null 2>&1; then
        ver="$("${cand}" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
        if [ -n "${ver}" ] && "${cand}" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) and sys.maxsize > 2**32 else 1)' 2>/dev/null; then
            echo "[1/1] found CPython ${ver} (>= 3.11) at: $(command -v "${cand}")"
            FOUND="${cand}"
            break
        fi
    fi
done

if [ -z "${FOUND}" ]; then
    echo "[1/1] no 64-bit CPython 3.11 or newer found on PATH."
fi

echo
echo "==========================================================================="
echo " Jarvis does not run on macOS or Linux yet."
echo "==========================================================================="
echo
echo "The application is Windows-only because its core depends on Win32 APIs:"
echo "  * global input hooks          - SetWindowsHookEx"
echo "  * synthetic text insertion    - SendInput / clipboard"
echo "  * the transparent overlay     - UpdateLayeredWindow"
echo "  * secret storage              - Windows Credential Manager / DPAPI"
echo "  * session notifications       - WTS WM_WTSSESSION_CHANGE"
echo "  * the tray + settings window  - pystray / WebView2"
echo
echo "A port needs a platform layer implementing each of those. The settings"
echo "web UI is portable in principle; the rest is not."
echo
echo "  Roadmap: docs/INSTALL.md#platform-support"
echo
echo "On Windows, run setup.cmd instead. Nothing was installed."
echo "==========================================================================="
exit 1
