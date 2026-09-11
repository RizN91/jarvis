"""Entry point: `python -m jarvis`.

Uses a single-instance guard so two copies cannot both install global hooks and
fight over the same microphone.

This module is deliberately the FIRST thing a non-Windows user reaches, so the
platform check lives here - before any module that calls ``ctypes.WinDLL`` is
imported. On macOS/Linux the process prints one honest sentence and exits
non-zero instead of dying on an ImportError traceback.
"""

from __future__ import annotations

import ctypes
import os
import sys


def _single_instance() -> bool:
    """Return False if another copy is already running in this session."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ERROR_ALREADY_EXISTS = 183
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, "Global\\JarvisSingleton")
    if not handle:
        return True
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return False
    # Intentionally leaked for the process lifetime so the mutex stays held.
    return True


def _platform_gate(argv: list[str]) -> int | None:
    """Return an exit code when the platform is unsupported, else None.

    ``--version`` and ``--help`` are informational and do not touch Win32, so
    they still work everywhere; every other invocation stops here on a
    non-Windows platform.
    """
    from .platform_support import is_supported, unsupported_message
    plat = sys.platform
    if is_supported(plat):
        return None
    if "--version" in argv:
        from . import APP_NAME, __version__
        print(f"{APP_NAME} {__version__} (unsupported platform: {plat})")
        return 0
    if "--help" in argv or "-h" in argv:
        from . import APP_NAME, __version__
        print(f"{APP_NAME} {__version__}\n\n{unsupported_message(plat)}")
        return 0
    print(unsupported_message(plat), file=sys.stderr)
    return 3


def main(argv: list[str] | None = None) -> int:
    # APP_NAME is the single source of truth for the display name. Hardcoding
    # a product name here is what once left the app calling itself two
    # different names in its own UI after a rename.
    from . import APP_NAME
    argv = list(argv if argv is not None else sys.argv[1:])

    # Refuse clearly on an unsupported platform, before importing Win32 code.
    gate = _platform_gate(argv)
    if gate is not None:
        return gate

    if "--settings" in argv:
        from .ui.window import run_settings_standalone
        return run_settings_standalone(argv)
    if "--help" in argv or "-h" in argv:
        print(f"{APP_NAME} — Windows dictation and GPT-Live voice assistant\n\n"
              "  python -m jarvis              start the tray app\n"
              "  python -m jarvis.ui.window    open Settings only\n"
              "  python -m jarvis --version    print the version\n")
        return 0
    if "--version" in argv:
        from . import __version__
        print(f"{APP_NAME} {__version__}")
        return 0

    from .logsetup import setup
    setup(level=os.environ.get("JARVIS_LOG_LEVEL")
          or os.environ.get("BV_LOG_LEVEL", "INFO"))

    if not _single_instance():
        from .logsetup import get
        get("main").warning("another %s instance is already running", APP_NAME)
        print(f"{APP_NAME} is already running (look in the system tray).")
        return 1

    from .app import Application
    app = Application()
    install_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if install_path not in sys.path:
        sys.path.insert(0, install_path)
    try:
        app.start()
    except KeyboardInterrupt:
        app.quit()
    except Exception:
        from .logsetup import get
        log = get("main")
        log.exception("fatal error")
        # Show the failure rather than dying silently in a tray-less process.
        try:
            from . import config
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, f"{APP_NAME} could not start.\n\nSee the log at:\n"
                      + str(config.log_dir()),
                APP_NAME, 0x10)
        except Exception:
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
