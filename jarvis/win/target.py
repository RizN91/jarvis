"""Target capture and validation.

WHY THIS MODULE EXISTS
----------------------
Dictation must not type into the wrong window. At activation we snapshot the
foreground window, its process, and the focused control. Before inserting we
re-verify all of it. If the user switched windows or fields, or the target
vanished, the caller holds the transcript in the overlay with Copy/Insert
options instead of typing into a different application.

SECURITY BOUNDARY
-----------------
We deliberately do NOT attempt to identify or write into elevated (UIPI
higher-integrity) windows, secure-desktop prompts, or password fields. If the
target is protected we refuse and surface a clear message. Bypassing UIPI is
out of scope by design - see build spec section 5/8.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from dataclasses import dataclass
import time
from typing import Optional

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)

# ------------------------------------------------------------ constants
ES_PASSWORD = 0x0020
ES_READONLY = 0x0800
GWL_STYLE = -16
GW_OWNER = 4

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAX_PATH_LONG = 32768

# Window classes we know are terminal-like: a multiline paste can EXECUTE a
# command even without a trailing Enter. These default to preview/copy only.
TERMINAL_CLASSES = {
    "ConsoleWindowClass",          # conhost (cmd, powershell, bash)
    "CASCADIA_HOSTING_WINDOW_CLASS",  # Windows Terminal
    "mintty",                      # git-bash / MSYS
    "PuTTY", "putty",
    "WindowsTerminal",
}

# Class names that are plain, safe text entry surfaces. RichEditD2DPT is the
# Win11 Notepad / modern RichEdit caret owner; without it Notepad falls back to
# the caret heuristic instead of being positively identified as an edit field.
EDIT_CLASSES = {"Edit", "RichEdit", "RichEdit20W", "RichEdit20A", "RichEdit50W",
                "RICHEDIT60W", "RichEditD2DPT", "RichEditD2DPT64",
                "Scintilla", "TextArea"}

# Browsers: text fields live inside the render process, so we cannot always see
# an Edit control. We allow them but require the window to be foreground and
# refuse if the caret owner reports a password field via UIA (checked in
# insert.py where UIA is available).
BROWSER_CLASSES = {"Chrome_WidgetWin_1", "MozillaWindowClass", "ApplicationFrameWindow"}

# Apps where auto-insertion is inappropriate outright.
BLOCKED_PROCESSES = {
    "logonui.exe", "consent.exe", "credentialuibroker.exe", "lsass.exe",
    "winlogon.exe", "securityhealthsystray.exe",
}
BLOCKED_TITLES = ("windows security", "user account control", "credential manager")

# WinUI / packaged (Store) apps such as Windows 11 Notepad are hosted inside a
# generic frame window. GetForegroundWindow() then returns
# "ApplicationFrameWindow" owned by applicationframehost.exe, NOT the real app.
# Identifying the target as the frame host would misreport the process and make
# the target look like an unknown control. We resolve through to the real app.
HOST_PROCESSES = {"applicationframehost.exe"}
HOST_CLASSES = {"ApplicationFrameWindow", "Windows.UI.Core.CoreWindow"}

# ------------------------------------------------------------ structs


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("flags", wt.DWORD),
        ("hwndActive", wt.HWND), ("hwndFocus", wt.HWND), ("hwndCapture", wt.HWND),
        ("hwndMenuOwner", wt.HWND), ("hwndMoveSize", wt.HWND), ("hwndCaret", wt.HWND),
        ("rcCaret", wt.RECT),
    ]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


user32.GetGUIThreadInfo.argtypes = [wt.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.GetGUIThreadInfo.restype = wt.BOOL
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
user32.GetWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.restype = wt.DWORD

kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [
    wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)
]
psapi.GetModuleFileNameExW.argtypes = [wt.HANDLE, wt.HMODULE, wt.LPWSTR, wt.DWORD]


def _get_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetClassNameW(hwnd, buf, 512)
    return buf.value


def _get_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(1024)
    user32.GetWindowTextW(hwnd, buf, 1024)
    return buf.value


def process_name_and_path(pid: int) -> tuple[str, str]:
    """Return (exe name lowercased, full path). Never raises."""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return "", ""
    try:
        size = wt.DWORD(MAX_PATH_LONG)
        buf = ctypes.create_unicode_buffer(MAX_PATH_LONG)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            path = buf.value
            return path.rsplit("\\", 1)[-1].lower(), path
        return "", ""
    finally:
        kernel32.CloseHandle(h)


GA_ROOTOWNER = 3
SW_RESTORE = 9
SW_SHOW = 5
LSFW_LOCK = 1
LSFW_UNLOCK = 2

# SetForegroundWindow succeeds only when the caller is allowed to change the
# foreground. The most reliable documented lever is LockSetForegroundWindow:
# LSFW_UNLOCK temporarily disables the foreground lock for this process, so a
# user-initiated "switch to app" really does land. We always put the lock back.
user32.LockSetForegroundWindow.argtypes = [wt.UINT]
user32.LockSetForegroundWindow.restype = wt.BOOL
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.IsIconic.argtypes = [wt.HWND]
user32.IsIconic.restype = wt.BOOL


def _wait_ready(hwnd: int, timeout: float = 5.0) -> bool:
    """Wait until the window exists, is visible and is not minimised."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if user32.IsWindow(wt.HWND(hwnd)) and user32.IsWindowVisible(wt.HWND(hwnd)):
            return True
        time.sleep(0.05)
    return bool(user32.IsWindow(wt.HWND(hwnd)))


def force_foreground(hwnd: int, timeout: float = 4.0) -> bool:
    """Reliably bring a window to the foreground.

    SetForegroundWindow is refused when the calling process does not own the
    foreground (the Windows foreground lock, and the "user is interacting"
    rule). Three documented levers are combined here: LockSetForegroundWindow
    to unlock, AttachThreadInput to share the foreground thread's input queue,
    and finally a benign ALT tap, which is the long-standing practical way to
    clear the lock.

    THIS IS NOT USED BY DICTATION. Dictation must never steal focus - the
    overlay is deliberately non-activating. This helper exists for the
    user-initiated "open/switch to an approved app" tool and for tests.
    """
    if not hwnd:
        return False
    _wait_ready(hwnd, timeout=min(5.0, timeout))

    # Deferred import: insert imports target, so a module-level import here
    # would be a cycle. Reusing its tested SendInput wrappers is better than
    # hand-rolling another INPUT struct.
    from . import insert as _ins

    unlocked = False
    try:
        unlocked = bool(user32.LockSetForegroundWindow(LSFW_UNLOCK))
    except Exception:
        unlocked = False

    try:
        deadline = time.monotonic() + timeout
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            if int(user32.GetForegroundWindow() or 0) == int(hwnd):
                return True

            user32.ShowWindow(wt.HWND(hwnd), SW_RESTORE)
            fg = user32.GetForegroundWindow()
            tid_fg = int(user32.GetWindowThreadProcessId(fg, None)) if fg else 0
            tid_me = int(kernel32.GetCurrentThreadId())
            attached = False
            try:
                if tid_fg and tid_fg != tid_me:
                    attached = bool(user32.AttachThreadInput(tid_me, tid_fg, True))
                user32.BringWindowToTop(wt.HWND(hwnd))
                user32.SetForegroundWindow(wt.HWND(hwnd))
            except Exception:
                pass
            finally:
                if attached:
                    try:
                        user32.AttachThreadInput(tid_me, tid_fg, False)
                    except Exception:
                        pass

            # A benign ALT tap is the practical unlock when the above is refused.
            try:
                _ins._send([_ins._key(0x12), _ins._key(0x12, up=True)])
                user32.SetForegroundWindow(wt.HWND(hwnd))
                user32.BringWindowToTop(wt.HWND(hwnd))
            except Exception:
                pass

            for _ in range(8):
                if int(user32.GetForegroundWindow() or 0) == int(hwnd):
                    return True
                time.sleep(0.04)
            time.sleep(0.1 * min(attempt, 4))
        return int(user32.GetForegroundWindow() or 0) == int(hwnd)
    finally:
        if unlocked:
            try:
                user32.LockSetForegroundWindow(LSFW_LOCK)
            except Exception:
                pass


user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.BringWindowToTop.restype = wt.BOOL
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.ShowWindow.restype = wt.BOOL


def root_owner_window(hwnd: int) -> int:
    """Walk up to the top-level window for `hwnd`."""
    if not hwnd:
        return 0
    try:
        root = user32.GetAncestor(wt.HWND(hwnd), GA_ROOTOWNER)
    except Exception:
        return int(hwnd)
    return int(root or hwnd)


advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
TOKEN_QUERY = 0x0008
TokenIntegrityLevel = 25
SECURITY_MANDATORY_MEDIUM_RID = 0x2000
SECURITY_MANDATORY_HIGH_RID = 0x3000


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wt.DWORD)]


class TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [("Label", SID_AND_ATTRIBUTES)]


advapi32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
advapi32.OpenProcessToken.restype = wt.BOOL
advapi32.GetTokenInformation.argtypes = [
    wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]
advapi32.GetTokenInformation.restype = wt.BOOL
advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wt.DWORD]
advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wt.DWORD)


def _token_integrity(process_handle: int) -> Optional[int]:
    """Return the process token's integrity RID, or None if unreadable."""
    token = wt.HANDLE()
    if not advapi32.OpenProcessToken(wt.HANDLE(process_handle), TOKEN_QUERY,
                                     ctypes.byref(token)):
        return None
    try:
        size = wt.DWORD(0)
        advapi32.GetTokenInformation(token, TokenIntegrityLevel, None, 0,
                                     ctypes.byref(size))
        if not size.value:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, TokenIntegrityLevel, buf,
                                            size.value, ctypes.byref(size)):
            return None
        label = ctypes.cast(buf, ctypes.POINTER(TOKEN_MANDATORY_LABEL)).contents
        sid = label.Label.Sid
        if not sid:
            return None
        count = advapi32.GetSidSubAuthorityCount(sid).contents.value
        if not count:
            return None
        return int(advapi32.GetSidSubAuthority(sid, count - 1).contents.value)
    finally:
        kernel32.CloseHandle(token)


def is_elevated_window(hwnd: int) -> bool:
    """True when the target process runs at a HIGHER integrity level than us.

    SendInput is subject to UIPI: it silently fails across that boundary. We
    detect it up front so the user gets a clear message instead of missing
    text. We never attempt to bypass the boundary.
    """
    if not hwnd:
        return False
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return False
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return True
    try:
        theirs = _token_integrity(int(h))
        mine = _token_integrity(int(kernel32.GetCurrentProcess()))
        if theirs is None or mine is None:
            return False
        return theirs > mine
    finally:
        kernel32.CloseHandle(h)


@dataclass
class TargetInfo:
    """A snapshot of where dictation was started. Re-validated before insert."""

    hwnd: int = 0
    focus_hwnd: int = 0
    caret_hwnd: int = 0
    thread_id: int = 0
    pid: int = 0
    process: str = ""
    exe_path: str = ""
    title: str = ""
    class_name: str = ""       # the app / top-level window class
    control_class: str = ""    # the focused control's class (real caret owner)
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    is_edit: bool = False
    is_terminal: bool = False
    is_browser: bool = False
    is_multiline: bool = False
    is_password: bool = False
    is_readonly: bool = False
    blocked_reason: Optional[str] = None

    # ---------------------------------------------------------- behaviour
    @property
    def insertable(self) -> bool:
        return self.blocked_reason is None and bool(self.hwnd)

    @property
    def requires_preview(self) -> bool:
        """Terminals and unknown surfaces: never auto-type (a multiline paste
        can execute a command)."""
        if self.is_terminal:
            return True
        if self.blocked_reason:
            return True
        # Known text surfaces and browsers report a caret/focus we can trust.
        return not (self.is_edit or self.is_browser or self.focus_hwnd or self.caret_hwnd)

    def signature(self) -> tuple:
        """Identity used to detect target drift between capture and insert."""
        return (self.hwnd, self.focus_hwnd, self.caret_hwnd, self.pid,
                self.class_name, self.title)

    def describe(self) -> str:
        if self.process:
            return f"{self.process} — {self.title[:60]}" if self.title else self.process
        return self.class_name or "unknown window"

    def as_dict(self) -> dict:
        return {
            "hwnd": self.hwnd, "process": self.process, "title": self.title,
            "class_name": self.class_name, "is_edit": self.is_edit,
            "is_terminal": self.is_terminal, "is_browser": self.is_browser,
            "is_password": self.is_password, "is_readonly": self.is_readonly,
            "requires_preview": self.requires_preview,
            "blocked_reason": self.blocked_reason, "describe": self.describe(),
        }


def _pid_of(hwnd: int) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
    return int(pid.value)


user32.EnumChildWindows.argtypes = [wt.HWND, ctypes.c_void_p, wt.LPARAM]
user32.EnumChildWindows.restype = wt.BOOL


def _find_hosted_app_window(host_hwnd: int, host_pid: int) -> int:
    """Find the real app window inside a UWP ApplicationFrameWindow host.

    Packaged apps run in their own process; their content window is a child of
    the frame host but belongs to a different PID. Returning the first child
    whose PID differs from the host's gives us the true application.
    """
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _l):
        h = int(hwnd)
        child_pid = _pid_of(h)
        if child_pid and child_pid != host_pid:
            found.append(h)
            return False  # first match is enough
        return True

    try:
        user32.EnumChildWindows(wt.HWND(host_hwnd), cb, 0)
    except Exception:
        return 0
    return found[0] if found else 0


def _style_flags(hwnd: int) -> int:
    return user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF


def capture_target(hwnd: Optional[int] = None) -> TargetInfo:
    """Snapshot the current input target.

    Called at activation time, BEFORE the overlay appears, so the overlay can
    never become the target itself.
    """
    info = TargetInfo()
    win = hwnd or user32.GetForegroundWindow()
    if not win:
        info.blocked_reason = "no foreground window"
        return info

    info.hwnd = int(win)
    tid = user32.GetWindowThreadProcessId(win, None)
    info.thread_id = int(tid)

    # ---- focused control inside that thread (the real text target) -----
    gti = GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(GUITHREADINFO)
    if user32.GetGUIThreadInfo(info.thread_id, ctypes.byref(gti)):
        info.focus_hwnd = int(gti.hwndFocus or 0)
        info.caret_hwnd = int(gti.hwndCaret or 0)

    top_pid = _pid_of(win)
    top_process, top_path = process_name_and_path(top_pid)
    top_class = _get_class(win)

    # ---- identify the REAL application ---------------------------------
    # For a packaged/WinUI app the foreground window is a generic frame host
    # (ApplicationFrameWindow / applicationframehost.exe). Report the app the
    # user is actually typing into, not the host.
    info.pid = top_pid
    info.process, info.exe_path = top_process, top_path
    info.class_name = top_class
    app_window = win

    hosted = (top_process in HOST_PROCESSES) or (top_class in HOST_CLASSES)
    if hosted:
        # The caret/focus window usually lives in the real app's process.
        for cand in (info.caret_hwnd, info.focus_hwnd):
            if not cand or cand == int(win):
                continue
            cand_pid = _pid_of(cand)
            cand_proc, cand_path = process_name_and_path(cand_pid)
            if cand_proc and cand_proc not in HOST_PROCESSES:
                info.pid, info.process, info.exe_path = cand_pid, cand_proc, cand_path
                break
        else:
            inner = _find_hosted_app_window(int(win), top_pid)
            if inner:
                app_window = inner
                info.pid = _pid_of(inner)
                info.process, info.exe_path = process_name_and_path(info.pid)
        real = app_window if app_window != int(win) else (
            info.caret_hwnd or info.focus_hwnd or int(win))
        real_class = _get_class(real)
        if real_class and real_class not in HOST_CLASSES:
            info.class_name = real_class

    info.title = _get_title(app_window) or _get_title(win)
    r = RECT()
    if user32.GetWindowRect(win, ctypes.byref(r)):
        info.rect = (r.left, r.top, r.right, r.bottom)

    control = info.caret_hwnd or info.focus_hwnd or info.hwnd
    info.control_class = _get_class(control) if control else ""
    ccls = info.control_class
    style = _style_flags(control) if control else 0

    info.is_edit = ccls in EDIT_CLASSES or ccls.startswith("Edit")
    info.is_terminal = ccls in TERMINAL_CLASSES or info.class_name in TERMINAL_CLASSES
    info.is_browser = info.class_name in BROWSER_CLASSES
    info.is_multiline = bool(style & 0x0004)  # ES_MULTILINE
    info.is_password = bool(style & ES_PASSWORD)
    info.is_readonly = bool(style & ES_READONLY)

    # ---- refusal conditions (explicit and testable) --------------------
    if info.process in BLOCKED_PROCESSES:
        info.blocked_reason = f"{info.process} is a protected system surface"
        return info
    low_title = (info.title or "").lower()
    if any(b in low_title for b in BLOCKED_TITLES):
        info.blocked_reason = "secure prompt detected — dictation is disabled here"
        return info
    if info.is_password:
        info.blocked_reason = "target is a password field"
        return info
    if info.is_readonly:
        info.blocked_reason = "target field is read-only"
        return info
    # Broker/secure-desktop windows live on a different desktop and cannot be
    # written to. Detect the well-known broker classes.
    if ccls in {"Credential Dialog Xaml Host", "Windows.UI.Core.CoreWindow"} \
            and "credential" in low_title:
        info.blocked_reason = "credential UI — dictation is disabled here"
        return info

    return info


def target_still_valid(target: TargetInfo) -> tuple[bool, str]:
    """Re-check the snapshot immediately before inserting."""
    if target.blocked_reason:
        return False, target.blocked_reason
    if not target.hwnd or not user32.IsWindow(target.hwnd):
        return False, "the original window was closed"
    if user32.IsIconic(target.hwnd):
        return False, "the original window was minimised"
    if not user32.IsWindowVisible(target.hwnd):
        return False, "the original window is no longer visible"

    fg = user32.GetForegroundWindow()
    if not fg:
        return False, "no window currently has focus"
    if int(fg) != target.hwnd:
        # Allow the case where focus moved to a child of the same top-level
        # window (common with browsers and tabs).
        root_fg = root_owner_window(int(fg))
        root_t = root_owner_window(target.hwnd)
        if root_fg and root_t and root_fg == root_t:
            return True, "ok"
        fg_pid = wt.DWORD()
        user32.GetWindowThreadProcessId(fg, ctypes.byref(fg_pid))
        if int(fg_pid.value) != target.pid:
            return False, "you switched to a different window"
        return False, "you switched to a different window in that app"

    # Caret owner moved to a different control -> the field changed.
    gti = GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(GUITHREADINFO)
    if user32.GetGUIThreadInfo(target.thread_id, ctypes.byref(gti)):
        now_caret = int(gti.hwndCaret or 0)
        now_focus = int(gti.hwndFocus or 0)
        if target.caret_hwnd and now_caret and now_caret != target.caret_hwnd:
            return False, "the text cursor moved to a different field"
        if target.focus_hwnd and now_focus and now_focus != target.focus_hwnd:
            return False, "you changed to a different field"
    return True, "ok"
