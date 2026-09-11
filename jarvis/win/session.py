"""Windows session state: locked, unlocked, suspended, resumed.

WHY THIS EXISTS
---------------
Build spec section 4: "On lock, suspend, microphone disable, or quit, stop
capture and cloud sessions." That is a correctness AND a cost requirement - a
locked screen must not leave a billed Live session running, and must not keep
the microphone open.

Two independent mechanisms, because neither alone is sufficient:

1. POLLED DESKTOP CHECK (authoritative, always available)
   While the workstation is locked, the *input desktop* is no longer
   "Default" - Windows switches to the Winlogon secure desktop. Opening the
   input desktop and reading its name is the standard, reliable test and needs
   no message pump, so it works even before our window exists.

2. WTS / POWER NOTIFICATIONS (fast, event driven)
   WTSRegisterSessionNotification delivers WM_WTSSESSION_CHANGE with
   WTS_SESSION_LOCK / WTS_SESSION_UNLOCK, and WM_POWERBROADCAST delivers
   suspend/resume. These let us react immediately instead of on the next poll.

Detecting a locked session also matters for testing: SendInput cannot reach any
ordinary window while the secure desktop is up, so integration tests use
`is_locked()` to report an honest BLOCKED result instead of a false failure.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import subprocess
import threading
from typing import Callable, Optional

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)

DESKTOP_READOBJECTS = 0x0001
DESKTOP_SWITCHDESKTOP = 0x0100
UOI_NAME = 2

user32.OpenInputDesktop.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
user32.OpenInputDesktop.restype = wt.HANDLE
user32.GetUserObjectInformationW.argtypes = [
    wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]
user32.GetUserObjectInformationW.restype = wt.BOOL
user32.CloseDesktop.argtypes = [wt.HANDLE]

WM_WTSSESSION_CHANGE = 0x02B1
WM_POWERBROADCAST = 0x0218
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
WTS_CONSOLE_DISCONNECT = 0x2
WTS_CONSOLE_CONNECT = 0x1
NOTIFY_FOR_THIS_SESSION = 0

PBT_APMSUSPEND = 0x0004
PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_APMRESUMESUSPEND = 0x0007

HWND_MESSAGE = -3
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR),
    ]


def input_desktop_name() -> str:
    """Name of the desktop currently receiving input ("Default" when unlocked)."""
    h = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS | DESKTOP_SWITCHDESKTOP)
    if not h:
        return ""
    try:
        needed = wt.DWORD(0)
        user32.GetUserObjectInformationW(h, UOI_NAME, None, 0, ctypes.byref(needed))
        if not needed.value:
            return ""
        buf = ctypes.create_unicode_buffer(max(needed.value, 2))
        if not user32.GetUserObjectInformationW(h, UOI_NAME, buf, needed.value,
                                                ctypes.byref(needed)):
            return ""
        return buf.value
    finally:
        user32.CloseDesktop(h)


# Window classes that mean "nothing is really focused" - the desktop shell.
SHELL_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW",
                 "Windows.UI.Core.CoreWindow"}

user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetForegroundWindow.restype = wt.HWND

# Declare these explicitly. Without argtypes ctypes marshals LPARAM as a 32-bit
# C int, so any message with a large lparam makes the window procedure raise
# `OverflowError: int too long to convert` - which is swallowed by ctypes as
# "Exception ignored", leaving the window's messages unprocessed and spamming
# stderr. SessionMonitor implements "on lock/suspend, stop capture", so it has to
# actually receive WM_WTSSESSION_CHANGE / WM_POWERBROADCAST.
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t


def _foreground_class() -> str:
    try:
        h = user32.GetForegroundWindow()
        if not h:
            return ""
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, buf, 256)
        return buf.value
    except Exception:
        return ""


def is_locked() -> bool:
    """True when the workstation is locked or otherwise not on the user desktop.

    The authoritative signal is the INPUT DESKTOP: while locked, Windows
    switches input to the Winlogon secure desktop, so OpenInputDesktop returns
    a desktop whose name is not "Default". That test needs no message pump and
    no window, so it works before the app has a UI.

    LockApp.exe presence is only a TIEBREAKER. It is NOT sufficient on its own:
    LockApp.exe stays resident for a while after the user unlocks, so treating
    it as proof of a lock produced a false positive that made this app refuse to
    take the foreground on an unlocked desktop. We only believe it when no real
    application holds the foreground either.
    """
    name = input_desktop_name()
    if name and name.lower() != "default":
        return True

    fg_class = _foreground_class()
    if fg_class and fg_class not in SHELL_CLASSES:
        # A genuine application window has focus: the desktop is in use.
        return False

    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq LockApp.exe", "/NH"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.lower()
        return "lockapp.exe" in out
    except Exception:
        return False


class SessionMonitor:
    """Reports lock/unlock/suspend/resume through callbacks.

    `on_locked` must be fast and non-blocking: it is called from the message
    thread. Do the real work (closing sessions, stopping capture) on a queue.
    """

    def __init__(self,
                 on_locked: Optional[Callable[[str], None]] = None,
                 on_unlocked: Optional[Callable[[], None]] = None,
                 on_suspend: Optional[Callable[[], None]] = None,
                 on_resume: Optional[Callable[[], None]] = None,
                 poll_seconds: float = 2.0):
        self.on_locked = on_locked
        self.on_unlocked = on_unlocked
        self.on_suspend = on_suspend
        self.on_resume = on_resume
        self.poll_seconds = poll_seconds
        self._thread: Optional[threading.Thread] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._thread_id: Optional[int] = None
        self._hwnd = 0
        self._locked = False
        self._wndproc = WNDPROC(self._proc)
        self._registered = False

    # ------------------------------------------------------------ lifecycle
    @property
    def locked(self) -> bool:
        return self._locked

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._locked = is_locked()
        self._thread = threading.Thread(target=self._message_loop, name="session",
                                        daemon=True)
        self._thread.start()
        self._poll_thread = threading.Thread(target=self._poll_loop, name="session-poll",
                                             daemon=True)
        self._poll_thread.start()

    def stop(self) -> None:
        self._stop.set()
        tid = self._thread_id
        if tid:
            try:
                user32.PostThreadMessageW(tid, 0x0012, 0, 0)  # WM_QUIT
            except Exception:
                pass
        for t in (self._thread, self._poll_thread):
            if t:
                t.join(timeout=1.5)

    # --------------------------------------------------------------- internals
    def _emit_locked(self, reason: str) -> None:
        if self._locked and reason != "suspend":
            return
        self._locked = True
        if self.on_locked:
            try:
                self.on_locked(reason)
            except Exception:
                pass

    def _emit_unlocked(self) -> None:
        if not self._locked:
            return
        self._locked = False
        if self.on_unlocked:
            try:
                self.on_unlocked()
            except Exception:
                pass

    def _proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_WTSSESSION_CHANGE:
            if wparam == WTS_SESSION_LOCK:
                self._emit_locked("locked")
            elif wparam == WTS_SESSION_UNLOCK:
                self._emit_unlocked()
            elif wparam == WTS_CONSOLE_DISCONNECT:
                self._emit_locked("console disconnected")
        elif msg == WM_POWERBROADCAST:
            if wparam == PBT_APMSUSPEND:
                if self.on_suspend:
                    try:
                        self.on_suspend()
                    except Exception:
                        pass
                self._emit_locked("suspend")
            elif wparam in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
                if self.on_resume:
                    try:
                        self.on_resume()
                    except Exception:
                        pass
                # Re-verify instead of assuming: resume does not always mean
                # the user is back in front of an unlocked desktop.
                self._locked = is_locked()
        elif msg == 0x0002:  # WM_DESTROY
            user32.PostQuitMessage(0)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _message_loop(self) -> None:
        self._thread_id = int(kernel32.GetCurrentThreadId())
        hinst = kernel32.GetModuleHandleW(None)
        cls_name = f"JarvisSession_{self._thread_id}"
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = cls_name
        user32.RegisterClassW(ctypes.byref(wc))
        self._hwnd = int(user32.CreateWindowExW(
            0, cls_name, "JarvisSession", 0, 0, 0, 0, 0,
            wt.HWND(HWND_MESSAGE), None, hinst, None) or 0)
        if self._hwnd:
            try:
                user32.RegisterWindowMessageW("WM_WTSSESSION_CHANGE")
                self._registered = bool(wtsapi32.WTSRegisterSessionNotification(
                    wt.HWND(self._hwnd), NOTIFY_FOR_THIS_SESSION))
            except Exception:
                self._registered = False
        msg = wt.MSG()
        while not self._stop.is_set():
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if self._hwnd:
            try:
                if self._registered:
                    wtsapi32.WTSUnRegisterSessionNotification(wt.HWND(self._hwnd))
            except Exception:
                pass
            user32.DestroyWindow(wt.HWND(self._hwnd))

    def _poll_loop(self) -> None:
        """Fallback: catches locks that never generated a notification (for
        example when the app started while already locked), and guarantees we
        notice even if the message window failed to register."""
        while not self._stop.is_set():
            try:
                now = is_locked()
                if now and not self._locked:
                    self._emit_locked("detected by poll")
                elif not now and self._locked:
                    self._emit_unlocked()
            except Exception:
                pass
            self._stop.wait(self.poll_seconds)
