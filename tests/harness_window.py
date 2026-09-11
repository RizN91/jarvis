"""Deterministic real-Windows test harness: a genuine Win32 EDIT control.

WHY
---
Windows 11 Notepad is a packaged WinUI app that restores its previous document
on every launch and offers no reliable way to reset it. Measuring character-level
injection fidelity against it produced non-reproducible numbers (see the first
attempt recorded in docs/TEST_RESULTS.md).

This harness creates a REAL top-level Win32 window with a REAL multiline EDIT
child, in its own thread with its own message loop. Nothing is mocked: the
control is the same USER32 "EDIT" class Windows apps use, it is driven through
the same SendInput path the product uses, and its text is read back with
WM_GETTEXT.

Threading notes that matter:
  * SetFocus only works inside the calling thread's input queue, so the main
    thread must AttachThreadInput before focusing the harness thread's control,
    and the harness's WM_SETFOCUS handler focuses the EDIT within its own queue.
  * GetFocus() returns the focus for the CALLING thread, so it is useless from
    the main thread. Focus is verified with GetGUIThreadInfo(thread_id).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
import time
from typing import Optional

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_VSCROLL = 0x00200000
WS_TABSTOP = 0x00010000
ES_MULTILINE = 0x0004
ES_AUTOVSCROLL = 0x0040
ES_WANTRETURN = 0x1000
ES_NOHIDESEL = 0x0100
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WM_SETTEXT = 0x000C
WM_CLOSE = 0x0010
WM_SETFOCUS = 0x0007
WM_DESTROY = 0x0002
WM_QUIT = 0x0012
SW_SHOW = 5
SW_RESTORE = 9

user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.DestroyWindow.argtypes = [wt.HWND]
user32.SetFocus.argtypes = [wt.HWND]
user32.SetFocus.restype = wt.HWND
user32.GetFocus.restype = wt.HWND
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.AttachThreadInput.restype = wt.BOOL
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.GetForegroundWindow.restype = wt.HWND
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.LockSetForegroundWindow.argtypes = [wt.UINT]
user32.LockSetForegroundWindow.restype = wt.BOOL

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR),
    ]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hwndActive", wt.HWND),
        ("hwndFocus", wt.HWND), ("hwndCapture", wt.HWND), ("hwndMenuOwner", wt.HWND),
        ("hwndMoveSize", wt.HWND), ("hwndCaret", wt.HWND), ("rcCaret", wt.RECT),
    ]


user32.GetGUIThreadInfo.argtypes = [wt.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.GetGUIThreadInfo.restype = wt.BOOL

READ_BUFFER = 1 << 20


def thread_focus(thread_id: int) -> int:
    """The focus window owned by `thread_id` (works cross-thread)."""
    gti = GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(GUITHREADINFO)
    if user32.GetGUIThreadInfo(thread_id, ctypes.byref(gti)):
        return int(gti.hwndFocus or 0)
    return 0


class EditHarness:
    """A real Win32 window with a real multiline EDIT control."""

    def __init__(self, title: str = "JarvisTestSurface"):
        self.title = title
        self.hwnd = 0
        self.edit = 0
        self.thread_id = 0
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._proc = WNDPROC(self._wndproc)
        self._closed = False
        self._settext_buf = None      # keeps the WM_SETTEXT buffer alive

    # ---------------------------------------------------------------- API
    def start(self, timeout: float = 10.0) -> bool:
        self._thread = threading.Thread(target=self._run, name="edit-harness",
                                        daemon=True)
        self._thread.start()
        return bool(self._ready.wait(timeout) and self.edit)

    def stop(self) -> None:
        self._closed = True
        if self.hwnd:
            try:
                user32.PostMessageW(wt.HWND(self.hwnd), WM_CLOSE, 0, 0)
            except Exception:
                pass
        if self.thread_id:
            try:
                user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=2.0)

    def set_text(self, text: str) -> None:
        """Set contents directly. Setup only - never a measured code path."""
        if not self.edit:
            return
        self._settext_buf = ctypes.create_unicode_buffer(text)
        user32.SendMessageW(wt.HWND(self.edit), WM_SETTEXT, wt.WPARAM(1),
                            wt.LPARAM(ctypes.addressof(self._settext_buf)))
        time.sleep(0.05)

    def read(self) -> str:
        if not self.edit:
            return ""
        buf = ctypes.create_unicode_buffer(READ_BUFFER)
        copied = int(user32.SendMessageW(
            wt.HWND(self.edit), WM_GETTEXT, wt.WPARAM(READ_BUFFER - 1),
            wt.LPARAM(ctypes.addressof(buf))))
        if copied < 0:
            return ""
        return buf.value

    @property
    def focused(self) -> bool:
        return bool(self.edit) and thread_focus(self.thread_id) == self.edit

    @property
    def foreground(self) -> bool:
        return int(user32.GetForegroundWindow() or 0) == int(self.hwnd)

    def focus_edit(self, timeout: float = 3.0) -> bool:
        """Make this window foreground and its EDIT the focused control."""
        deadline = time.monotonic() + timeout
        unlocked = False
        try:
            unlocked = bool(user32.LockSetForegroundWindow(2))  # LSFW_UNLOCK
        except Exception:
            pass
        try:
            while time.monotonic() < deadline:
                if self.focused and self.foreground:
                    return True
                user32.ShowWindow(wt.HWND(self.hwnd), SW_RESTORE)
                fg = user32.GetForegroundWindow()
                tid_fg = int(user32.GetWindowThreadProcessId(fg, None)) if fg else 0
                tid_me = int(kernel32.GetCurrentThreadId())
                attached = False
                try:
                    if tid_fg and tid_fg != tid_me:
                        attached = bool(user32.AttachThreadInput(tid_me, tid_fg, True))
                    user32.SetForegroundWindow(wt.HWND(self.hwnd))
                    user32.BringWindowToTop(wt.HWND(self.hwnd))
                except Exception:
                    pass
                finally:
                    if attached:
                        try:
                            user32.AttachThreadInput(tid_me, tid_fg, False)
                        except Exception:
                            pass
                # Focus the EDIT inside the harness thread's own input queue.
                try:
                    user32.AttachThreadInput(tid_me, self.thread_id, True)
                    user32.SetFocus(wt.HWND(self.edit))
                    user32.AttachThreadInput(tid_me, self.thread_id, False)
                except Exception:
                    pass
                time.sleep(0.08)
            return self.focused and self.foreground
        finally:
            if unlocked:
                try:
                    user32.LockSetForegroundWindow(1)  # LSFW_LOCK
                except Exception:
                    pass

    # ----------------------------------------------------------- internals
    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        if msg == WM_SETFOCUS:
            if self.edit:
                user32.SetFocus(wt.HWND(self.edit))
        try:
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        except Exception:
            return 0

    def _run(self) -> None:
        self.thread_id = int(kernel32.GetCurrentThreadId())
        hinst = kernel32.GetModuleHandleW(None)
        cls = f"JarvisEditHarness_{self.thread_id}"
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = cls
        user32.RegisterClassW(ctypes.byref(wc))

        self.hwnd = int(user32.CreateWindowExW(
            0, cls, self.title, WS_OVERLAPPEDWINDOW | WS_VISIBLE,
            120, 120, 720, 420, None, None, hinst, None) or 0)
        if not self.hwnd:
            self._ready.set()
            return
        self.edit = int(user32.CreateWindowExW(
            0, "EDIT", "",
            WS_CHILD | WS_VISIBLE | WS_VSCROLL | WS_TABSTOP | ES_MULTILINE
            | ES_AUTOVSCROLL | ES_WANTRETURN | ES_NOHIDESEL,
            8, 8, 690, 370, wt.HWND(self.hwnd), None, hinst, None) or 0)
        user32.ShowWindow(wt.HWND(self.hwnd), SW_SHOW)
        user32.SetFocus(wt.HWND(self.edit))
        self._ready.set()

        msg = wt.MSG()
        while not self._closed:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


if __name__ == "__main__":
    h = EditHarness()
    print("started:", h.start())
    print("hwnd=0x%X edit=0x%X thread=%d" % (h.hwnd, h.edit, h.thread_id))
    print("focus_edit:", h.focus_edit(), "foreground:", h.foreground,
          "focused:", h.focused)
    h.set_text("hello world")
    print("read back:", repr(h.read()))
    h.set_text("")
    print("after clear:", repr(h.read()))
    h.stop()
