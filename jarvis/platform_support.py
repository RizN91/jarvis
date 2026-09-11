"""Platform capability reporting for Jarvis.

Jarvis is a **Windows-only** desktop application. Its core depends on Win32
APIs with no equivalent in this codebase:

  * global low-level input hooks        - ``SetWindowsHookEx``
  * synthetic text insertion            - ``SendInput`` / ``Clipboard``
  * the transparent always-on-top pill  - ``UpdateLayeredWindow`` overlays
  * per-user secret storage             - Windows Credential Manager + DPAPI
  * session change notifications        - WTS ``WM_WTSSESSION_CHANGE``
  * the tray icon and Settings window   - pystray/WebView2 on Windows

This module exists so ``python -m jarvis`` on macOS or Linux fails with a clear
sentence instead of an ``ImportError`` traceback from the first ``ctypes.WinDLL``
call. It never imports the Windows-only modules itself.
"""

from __future__ import annotations

import sys

#: Platforms on which the application is supported. ``sys.platform`` values.
SUPPORTED_PLATFORMS = ("win32",)

#: Where the honest platform story lives in the docs.
ROADMAP_ANCHOR = "docs/INSTALL.md#platform-support"

_FRIENDLY = {
    "win32": "Windows",
    "cygwin": "Windows",
    "darwin": "macOS",
    "linux": "Linux",
}


def current_platform() -> str:
    """Return the running ``sys.platform`` value."""
    return sys.platform


def platform_label(plat: str | None = None) -> str:
    """A human-friendly platform name."""
    plat = plat or current_platform()
    return _FRIENDLY.get(plat, plat)


def is_supported(plat: str | None = None) -> bool:
    """True when *plat* (default: the running platform) is supported."""
    return (plat or current_platform()) in SUPPORTED_PLATFORMS


def support_reason(plat: str | None = None) -> str:
    """A one-paragraph explanation of the support status for *plat*."""
    plat = plat or current_platform()
    if is_supported(plat):
        return (
            "Windows is the supported platform. Jarvis uses Win32 global input "
            "hooks, SendInput text insertion, layered overlay windows, the "
            "Windows Credential Manager and WTS session notifications."
        )
    return (
        f"Jarvis is not supported on {platform_label(plat)} yet. The application "
        "is Windows-only because it relies on Win32 APIs: SetWindowsHookEx "
        "global hotkeys, SendInput text insertion, UpdateLayeredWindow overlays, "
        "Windows Credential Manager / DPAPI secret storage and WTS session "
        "notifications. A port needs a platform layer implementing each of "
        f"those; see the roadmap in {ROADMAP_ANCHOR}."
    )


def unsupported_message(plat: str | None = None) -> str:
    """The complete, friendly message shown when run on an unsupported OS."""
    plat = plat or current_platform()
    return (
        f"Jarvis does not run on {platform_label(plat)} yet.\n\n"
        f"{support_reason(plat)}\n\n"
        "The settings web UI is portable in principle, but the dictation, "
        "hotkey, overlay and credential layers are not."
    )
