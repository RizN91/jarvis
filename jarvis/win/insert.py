"""Text insertion into the captured target, safely.

RULES ENFORCED HERE (build spec section 5)
------------------------------------------
1. Never press Enter or Send. No exceptions, no configuration flag.
2. Insert at the caret; never select-all-and-replace a field.
3. Refuse password fields, secure prompts, read-only fields, elevated windows,
   and terminals-by-default (a multiline paste can EXECUTE a command even with
   no trailing Enter). Terminals get preview/copy instead.
4. Only paste via the clipboard when the existing clipboard can be restored
   FAITHFULLY. If it holds any format we cannot round-trip byte-for-byte, we
   fall back to unicode key injection so no clipboard data is ever destroyed.
5. Restore the clipboard only if nobody else changed it while we held it.
6. Restore held modifier keys so we never leave a stuck Ctrl/Shift/Alt/Win.
7. Undo only removes text THIS app inserted, and refuses if the user typed
   anything after it, so unrelated edits are never undone.

PRIMARY METHOD
--------------
SendInput with KEYEVENTF_UNICODE - the documented Windows text-input API. It
inserts at the caret, handles Unicode and newlines, needs no clipboard, and is
subject to UIPI (which we respect rather than bypass).

CLIPBOARD METHOD (fallback only)
--------------------------------
Used when the text is very long or the target needs a real paste (some
Electron/WebView2 editors drop synthesized unicode events). Guarded by
ClipboardGuard below.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import hashlib
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from . import target as tgt
from ..logsetup import get as _log

log = _log("insert")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ----------------------------------------------------------- SendInput
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wt.UINT

VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN = 0x11, 0x10, 0x12, 0x5B, 0x5C
VKS_CONTROL, VKS_SHIFT, VKS_MENU = 0xA2, 0xA0, 0xA4
VK_V = 0x56
VK_A = 0x41

# ----------------------------------------------------------- clipboard
CF_TEXT = 1
CF_BITMAP = 2
CF_METAFILEPICT = 3
CF_DIB = 8
CF_PALETTE = 9
CF_ENHMETAFILE = 14
CF_UNICODETEXT = 13
CF_HTML = 0x0000C01E  # "HTML Format"
CF_RTF = 0x0000C001   # "Rich Text Format"
GMEM_MOVEABLE = 0x0002

# Formats that are HANDLES (GDI objects), not HGLOBAL memory. GetClipboardData
# for these returns a bitmap/metafile/palette handle that cannot be copied by
# locking memory, so we cannot store and restore them byte-for-byte. If any of
# these is on the clipboard we refuse the clipboard path entirely rather than
# silently destroying the user's copied image.
GDI_HANDLE_FORMATS = {CF_BITMAP, CF_METAFILEPICT, CF_PALETTE, CF_ENHMETAFILE,
                      0x0080, 0x0081, 0x0082, 0x0083, 0x008E}

user32.OpenClipboard.argtypes = [wt.HWND]
user32.OpenClipboard.restype = wt.BOOL
user32.EmptyClipboard.restype = wt.BOOL
user32.CloseClipboard.restype = wt.BOOL
user32.GetClipboardData.argtypes = [wt.UINT]
user32.GetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
user32.SetClipboardData.restype = wt.HANDLE
user32.GetClipboardSequenceNumber.restype = wt.DWORD
user32.EnumClipboardFormats.argtypes = [wt.UINT]
user32.EnumClipboardFormats.restype = wt.UINT

kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
kernel32.GlobalSize.argtypes = [wt.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalFree.argtypes = [wt.HGLOBAL]


class ClipboardError(RuntimeError):
    pass


# ---------------------------------------------- window plumbing for the
# clipboard owner (delayed rendering needs a window to receive WM_RENDERFORMAT)
HWND_MESSAGE = -3
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR),
    ]


user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wt.ATOM
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostThreadMessageW.restype = wt.BOOL
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = ctypes.c_int


def _open_clipboard(timeout: float = 1.0) -> None:
    """Clipboard is a shared, single-owner resource; retry briefly."""
    deadline = time.monotonic() + timeout
    while True:
        if user32.OpenClipboard(None):
            return
        if time.monotonic() >= deadline:
            raise ClipboardError(
                "the clipboard is locked by another application "
                "(it was left unchanged)"
            )
        time.sleep(0.02)


def clipboard_sequence() -> int:
    return int(user32.GetClipboardSequenceNumber())


def _pb_bytes(handle: int, size: Optional[int] = None) -> bytes:
    if not handle:
        return b""
    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        return b""
    try:
        n = size if size is not None else int(kernel32.GlobalSize(handle))
        return ctypes.string_at(ptr, n)
    finally:
        kernel32.GlobalUnlock(handle)


@dataclass
class ClipboardSnapshot:
    """Everything needed to decide whether a faithful restore is possible."""

    formats: list[int] = field(default_factory=list)
    text: Optional[str] = None
    blobs: dict[int, bytes] = field(default_factory=dict)
    opaque: list[int] = field(default_factory=list)
    sequence: int = 0
    empty: bool = True

    @property
    def restore_safe(self) -> bool:
        """True only when every format present can be written back verbatim.

        Real Windows clipboards are not simple: copying text in Chrome adds
        registered custom formats such as "Chromium Web Custom MIME Data
        Format", "CanIncludeInClipboardHistory" and "CanUploadToCloudClipboard"
        alongside CF_UNICODETEXT. Those ARE HGLOBAL-backed, so we can hold and
        restore them byte-for-byte - which is why the check is "is anything
        here an un-copyable GDI handle?", not "is this a known text format?".
        An earlier, stricter version rejected every real-world clipboard and
        silently disabled the paste path; the integration test caught that.
        """
        return not self.opaque

    def summary(self) -> str:
        return (f"{len(self.formats)} format(s), {len(self.blobs)} captured, "
                f"opaque={self.opaque}, safe={self.restore_safe}")


def snapshot_clipboard() -> ClipboardSnapshot:
    """Read the clipboard without modifying it.

    Every HGLOBAL-backed format is captured by raw bytes. GDI-handle formats
    are recorded in `opaque` because they cannot be round-tripped.
    """
    snap = ClipboardSnapshot(sequence=clipboard_sequence())
    _open_clipboard()
    try:
        fmt = 0
        while True:
            fmt = int(user32.EnumClipboardFormats(fmt))
            if not fmt:
                break
            snap.formats.append(fmt)
        snap.empty = not snap.formats

        for f in snap.formats:
            if f in GDI_HANDLE_FORMATS:
                snap.opaque.append(f)
                continue
            try:
                h = user32.GetClipboardData(f)
                raw = _pb_bytes(h)
                if raw:
                    snap.blobs[f] = raw
            except Exception:
                snap.opaque.append(f)

        if CF_UNICODETEXT in snap.blobs:
            try:
                snap.text = snap.blobs[CF_UNICODETEXT].decode(
                    "utf-16-le", errors="replace").rstrip("\x00")
            except Exception:
                snap.text = None
        if snap.text is None and CF_TEXT in snap.blobs:
            snap.text = snap.blobs[CF_TEXT].decode(
                "mbcs", errors="replace").rstrip("\x00")
    finally:
        user32.CloseClipboard()
    return snap


def _set_clipboard_text(text: str) -> None:
    data = text.encode("utf-16-le") + b"\x00\x00"
    _open_clipboard()
    try:
        if not user32.EmptyClipboard():
            raise ClipboardError("EmptyClipboard failed")
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            raise ClipboardError("GlobalAlloc failed")
        ptr = kernel32.GlobalLock(h)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            raise ClipboardError("SetClipboardData failed")
    finally:
        user32.CloseClipboard()


def _restore_clipboard(snap: ClipboardSnapshot) -> None:
    """Best-effort faithful restore of a snapshot we judged restore_safe."""
    _open_clipboard()
    try:
        user32.EmptyClipboard()
        for fmt, blob in snap.blobs.items():
            h = kernel32.GlobalAlloc(GMEM_MOVEABLE, max(1, len(blob)))
            if not h:
                continue
            ptr = kernel32.GlobalLock(h)
            if ptr:
                ctypes.memmove(ptr, blob, len(blob))
                kernel32.GlobalUnlock(h)
                if not user32.SetClipboardData(fmt, h):
                    kernel32.GlobalFree(h)
    finally:
        user32.CloseClipboard()


class _ClipboardOwner:
    """Message-only window that serves clipboard data on demand.

    WHY DELAYED RENDERING
    ---------------------
    When we insert long text by pasting, we must know when the target has
    actually READ the clipboard before we restore the user's original content.
    A fixed sleep is a race: the integration test caught Chromium pasting the
    *restored* clipboard (26 characters of the previous contents) because we put
    the original back before Chromium had consumed ours.

    Delayed rendering removes the guesswork. We claim ownership and pass NULL to
    SetClipboardData, promising to supply the data when asked. Windows then
    sends WM_RENDERFORMAT to our window at the moment the consumer requests it,
    which is a definitive "the paste happened" signal.
    """

    WM_RENDERFORMAT = 0x030A
    WM_RENDERALLFORMATS = 0x0301
    WM_DESTROY = 0x0002
    WM_QUIT = 0x0012

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._hwnd = 0
        self._ready = threading.Event()
        self._lock = threading.RLock()
        self._pending: dict[int, str] = {}
        self._served = threading.Event()
        self._proc = WNDPROC(self._wndproc)
        self.available = False

    # ------------------------------------------------------------ lifecycle
    def start(self, timeout: float = 3.0) -> bool:
        if self._hwnd:
            return True
        self._thread = threading.Thread(target=self._run, name="clip-owner",
                                        daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return bool(self._hwnd)

    def stop(self) -> None:
        if self._thread_id:
            try:
                user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=1.0)

    # ---------------------------------------------------------------- offer
    def offer_text(self, text: str) -> bool:
        """Become the clipboard owner, promising to render `text` on demand."""
        if not self.start():
            return False
        with self._lock:
            self._pending[CF_UNICODETEXT] = text
            self._served.clear()
        try:
            _open_clipboard()
            try:
                if not user32.EmptyClipboard():
                    return False
                # NULL handle == "I will supply this later" (delayed rendering).
                if not user32.SetClipboardData(CF_UNICODETEXT, None):
                    return False
            finally:
                user32.CloseClipboard()
            self.available = True
            return True
        except Exception as exc:
            log.warning("delayed clipboard offer failed: %s", exc)
            return False

    def wait_for_consumer(self, timeout: float = 2.0) -> bool:
        """Block until a consumer actually read the clipboard data."""
        return self._served.wait(timeout)

    def last_served(self) -> bool:
        return self._served.is_set()

    # ------------------------------------------------------------ internals
    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg in (self.WM_RENDERFORMAT, self.WM_RENDERALLFORMATS):
            with self._lock:
                text = self._pending.get(CF_UNICODETEXT)
            if text is None:
                return 0
            data = text.encode("utf-16-le") + b"\x00\x00"
            h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if h:
                ptr = kernel32.GlobalLock(h)
                if ptr:
                    ctypes.memmove(ptr, data, len(data))
                    kernel32.GlobalUnlock(h)
                    # Ownership transfers to the clipboard: do NOT free on success.
                    if not user32.SetClipboardData(CF_UNICODETEXT, h):
                        kernel32.GlobalFree(h)
            self._served.set()
            if msg == self.WM_RENDERALLFORMATS:
                with self._lock:
                    self._pending.clear()
            return 0
        if msg == self.WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self) -> None:
        self._thread_id = int(kernel32.GetCurrentThreadId())
        hinst = kernel32.GetModuleHandleW(None)
        cls = f"JarvisClipOwner_{self._thread_id}"
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = cls
        user32.RegisterClassW(ctypes.byref(wc))
        self._hwnd = int(user32.CreateWindowExW(
            0, cls, "JarvisClipOwner", 0, 0, 0, 0, 0,
            wt.HWND(HWND_MESSAGE), None, hinst, None) or 0)
        self._ready.set()
        msg = wt.MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


_owner_lock = threading.Lock()
_owner: Optional["_ClipboardOwner"] = None


def clipboard_owner() -> "_ClipboardOwner":
    global _owner
    with _owner_lock:
        if _owner is None:
            _owner = _ClipboardOwner()
        return _owner


class ClipboardGuard:
    """Hold the clipboard for one paste, then restore it ONLY if it is still
    ours. If the user copied something in the meantime, their data wins."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.snapshot: Optional[ClipboardSnapshot] = None
        self.ours_sequence: Optional[int] = None
        self.restored = False
        self.skipped_reason: Optional[str] = None
        self.owner: Optional[_ClipboardOwner] = None
        self.delayed = False

    def __enter__(self) -> "ClipboardGuard":
        if not self.enabled:
            return self
        snap = snapshot_clipboard()
        self.snapshot = snap
        if not snap.restore_safe:
            self.skipped_reason = (
                "clipboard holds formats this app cannot restore exactly, so it "
                "will be restored only if untouched"
            )
        return self

    def set_text(self, text: str) -> bool:
        """Offer `text` to the clipboard.

        Prefers delayed rendering so the caller can wait for the target to
        consume it. Returns True when the delayed path is active.
        """
        global _owner
        if _owner is None:
            _owner = _ClipboardOwner()
        self.owner = _owner
        if self.owner.offer_text(text):
            self.delayed = True
            self.ours_sequence = clipboard_sequence()
            return True
        # Fallback: plain immediate write (no consumption signal available).
        _set_clipboard_text(text)
        self.delayed = False
        self.ours_sequence = clipboard_sequence()
        return False

    def wait_for_consumer(self, timeout: float = 2.0,
                          fallback_wait: float = 0.45) -> bool:
        """Wait for the target to actually read the clipboard."""
        if not self.delayed or self.owner is None:
            time.sleep(fallback_wait)
            return False
        served = self.owner.wait_for_consumer(timeout)
        if not served:
            # The consumer never asked for the data (some apps read the
            # clipboard through OLE without triggering a render, and some
            # ignore paste entirely). Give the paste a bounded settle window
            # before we put the original back.
            time.sleep(fallback_wait)
        else:
            # Render happened; a short beat lets the control finish inserting.
            time.sleep(0.08)
        return served

    def __exit__(self, *exc) -> None:
        if not self.enabled or self.snapshot is None:
            return
        try:
            now = clipboard_sequence()
            if self.ours_sequence is None:
                return
            if now != self.ours_sequence:
                # Someone (the user, or another app) changed the clipboard while
                # we were working. Their content takes precedence; we do not
                # overwrite it.
                log.info("clipboard changed by another actor; not restoring")
                self.skipped_reason = ("you changed the clipboard during dictation, "
                                       "so it was left alone")
                return
            if not self.snapshot.restore_safe:
                log.info("not restoring non-round-trippable clipboard")
                return
            if self.snapshot.empty and not self.snapshot.blobs:
                # It was empty before us; leave our text so the user can paste
                # it manually if insertion failed.
                return
            _restore_clipboard(self.snapshot)
            self.restored = True
        except Exception as err:
            log.warning("clipboard restore failed: %s", err)


# ------------------------------------------------------------ modifiers


def modifier_states() -> dict[int, bool]:
    return {vk: bool(user32.GetAsyncKeyState(vk) & 0x8000)
            for vk in (VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN)}


def _key(vk: int, up: bool = False, scan: int = 0, unicode_mode: bool = False) -> INPUT:
    flags = 0
    if up:
        flags |= KEYEVENTF_KEYUP
    if unicode_mode:
        flags |= KEYEVENTF_UNICODE
    ki = KEYBDINPUT(wVk=0 if unicode_mode else vk, wScan=vk if unicode_mode else scan,
                    dwFlags=flags, time=0, dwExtraInfo=0)
    return INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=ki))


def _send(inputs: list[INPUT]) -> int:
    if not inputs:
        return 0
    arr = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        err = ctypes.get_last_error()
        # 0 with ERROR_ACCESS_DENIED usually means an elevated target (UIPI).
        raise OSError(err, f"SendInput delivered {sent}/{len(inputs)} events")
    return sent


def _release_modifiers() -> dict[int, bool]:
    """Temporarily release held modifiers so injected text is not modified.

    Returns which were held, so they can be re-pressed afterwards.
    """
    held = {vk: down for vk, down in modifier_states().items() if down}
    if held:
        for vk in held:
            _send([_key(vk, up=True)])
        time.sleep(0.02)
    return held


def _restore_modifiers(held: dict[int, bool]) -> None:
    for vk in held:
        try:
            _send([_key(vk)])
        except OSError:
            pass


# --------------------------------------------------------------- results


@dataclass
class InsertResult:
    ok: bool
    method: str = ""              # unicode | paste | preview_only | refused
    chars: int = 0
    detail: str = ""
    undo_token: Optional[str] = None
    clipboard_restored: bool = False
    held_for_user: bool = False   # text is in the overlay awaiting Copy/Insert
    latency_ms: float = 0.0

    def as_dict(self) -> dict:
        return {
            "ok": self.ok, "method": self.method, "chars": self.chars,
            "detail": self.detail, "clipboard_restored": self.clipboard_restored,
            "held_for_user": self.held_for_user,
            "latency_ms": round(self.latency_ms, 1),
        }


# --------------------------------------------------- undo bookkeeping

_undo_lock = threading.RLock()
_last_insert: Optional[dict] = None


def _signature(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def note_external_typing() -> None:
    """Called by the keyboard hook for every real user key press.

    Used to invalidate the undo token, so 'Undo last insertion' can never
    remove characters the user typed themselves.
    """
    with _undo_lock:
        if _last_insert is not None:
            _last_insert["dirty"] = True


def can_undo() -> bool:
    with _undo_lock:
        return bool(_last_insert and not _last_insert.get("dirty"))


def _record_insert(target: tgt.TargetInfo, text: str) -> str:
    global _last_insert
    token = _signature(text)
    with _undo_lock:
        _last_insert = {
            "token": token, "target": target.signature(),
            "chars": len(text), "dirty": False, "ts": time.time(),
            "text": text,
        }
    return token


# ------------------------------------------------------------ insertion

# Above this many characters we prefer a real paste: injecting 20k unicode
# key events is slow and some editors drop synthesized events.
PASTE_THRESHOLD_CHARS = 1200
SENDINPUT_CHUNK = 64


# ---------------------------------------------------------- line breaks

# Dictation must NEVER auto-press Enter/Send (build spec section 5): a stray
# Enter submits messages and executes commands. We therefore never synthesize
# VK_RETURN. Line breaks, if the text contains any, are injected as text
# characters only when the user has explicitly asked for them, and are
# flattened otherwise with a visible explanation.
VK_RETURN = 0x0D

# Controls that store a line break as CR rather than LF. Injecting LF into a
# RichEdit produces no visible break at all, which is what the integration test
# caught. NOTE: this is a text character (KEYEVENTF_UNICODE), never a
# VK_RETURN keystroke - see ReturnKeySpy in the test.
RICHTEXT_LIKE = {"Edit", "RichEdit", "RichEdit20W", "RichEdit20A", "RichEdit50W",
                 "RICHEDIT60W", "RichEditD2DPT", "RichEditD2DPT64"}


def prepare_text(text: str, target: "tgt.TargetInfo",
                 allow_line_breaks: bool = False) -> tuple[str, Optional[str]]:
    """Return (text_to_insert, notice).

    A bare newline character is content, not a keystroke, so injecting it does
    NOT press the Send button in a chat box. We still default to flattening for
    single-line targets, because a flattened line is never surprising.
    """
    if "\r" not in text and "\n" not in text:
        return text, None
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if allow_line_breaks:
        if target.control_class in RICHTEXT_LIKE:
            return normalized.replace("\n", "\r"), "line breaks were kept as explicit text"
        return normalized, "line breaks were kept as explicit text"
    if target.is_multiline:
        if target.control_class in RICHTEXT_LIKE:
            return normalized.replace("\n", "\r"), \
                "line breaks were kept because the target is multiline"
        return normalized, "line breaks were kept because the target is multiline"
    flattened = re.sub(r"\n+", " ", normalized).strip()
    return flattened, ("line breaks were replaced with spaces so nothing could "
                       "be submitted automatically")


def _insert_unicode(text: str, inter_event_delay: float = 0.0) -> None:
    inputs: list[INPUT] = []
    for ch in text:
        # Windows expects UTF-16 code units; a character outside the BMP
        # becomes a proper surrogate pair when taken one unit at a time.
        units = ch.encode("utf-16-le")
        for i in range(0, len(units), 2):
            unit = int.from_bytes(units[i:i + 2], "little")
            inputs.append(_key(unit, unicode_mode=True))
            inputs.append(_key(unit, up=True, unicode_mode=True))
    for i in range(0, len(inputs), SENDINPUT_CHUNK):
        _send(inputs[i:i + SENDINPUT_CHUNK])
        if inter_event_delay:
            time.sleep(inter_event_delay)


def _paste_shortcut() -> None:
    """Ctrl+V only. NEVER followed by Enter."""
    _send([
        _key(VK_CONTROL), _key(VK_V), _key(VK_V, up=True), _key(VK_CONTROL, up=True),
    ])


def insert_text(text: str, target: tgt.TargetInfo,
                allow_clipboard: bool = True,
                force_preview: bool = False,
                allow_line_breaks: bool = False) -> InsertResult:
    """Insert `text` into `target`, or refuse and explain why."""
    started = time.monotonic()
    text = text or ""
    if not text.strip():
        return InsertResult(False, "refused", 0, "nothing was captured")

    def done(ok: bool, method: str, chars: int, detail: str, **kw) -> InsertResult:
        return InsertResult(ok, method, chars, detail,
                            latency_ms=(time.monotonic() - started) * 1000.0, **kw)

    # ---- 1. policy refusal, before touching anything -------------------
    if target.blocked_reason:
        return done(False, "refused", 0, target.blocked_reason)
    if target.is_password:
        return done(False, "refused", 0, "refusing to type into a password field")
    if force_preview or target.requires_preview:
        return done(
            True, "preview_only", 0,
            "this target is a terminal or an unrecognised control — the text is held "
            "for you to review, Copy, or Insert deliberately",
            held_for_user=True,
        )

    valid, why = tgt.target_still_valid(target)
    if not valid:
        return done(True, "preview_only", 0,
                    f"{why} — the text is held in the pop-up instead of being typed "
                    f"into a different window", held_for_user=True)

    if tgt.is_elevated_window(target.hwnd):
        return done(True, "preview_only", 0,
                    "that window runs with administrator rights, so Windows blocks "
                    "injected text — copy it from here instead", held_for_user=True)

    # ---- 1b. line-break policy (never synthesize VK_RETURN) -----------
    text, break_notice = prepare_text(text, target,
                                      allow_line_breaks=allow_line_breaks)
    if not text.strip():
        return done(False, "refused", 0, "nothing was captured")

    # ---- 2. choose the method -----------------------------------------
    use_paste = False
    guard: Optional[ClipboardGuard] = None
    if allow_clipboard and len(text) >= PASTE_THRESHOLD_CHARS:
        probe = snapshot_clipboard()
        if probe.restore_safe:
            use_paste = True
        else:
            log.info("clipboard not round-trippable (%s); using key injection",
                     probe.summary())

    held_modifiers = _release_modifiers()
    try:
        if use_paste:
            guard = ClipboardGuard(enabled=True)
            with guard:
                guard.set_text(text)
                _paste_shortcut()
                # Wait for the target to actually consume the clipboard instead
                # of guessing with a fixed sleep. Chromium pasted the RESTORED
                # clipboard when we guessed, which is why this exists.
                served = guard.wait_for_consumer(timeout=2.0)
            method = "paste"
            if guard.restored:
                detail = ("inserted with a real paste; the clipboard was restored"
                          + ("" if served else " (consumption unconfirmed)"))
            else:
                detail = "inserted with a real paste"
        else:
            _insert_unicode(text)
            method = "unicode"
            detail = "inserted at the caret"

        token = _record_insert(target, text)
        # Verify the target still exists after insertion (a crash/close means
        # the user will not have seen the text).
        if not user32.IsWindow(target.hwnd):
            return done(True, "preview_only", len(text),
                        "the window closed while the text was being sent — "
                        "check the result and re-insert if needed",
                        held_for_user=True, undo_token=token)
        return done(True, method, len(text), detail, undo_token=token,
                    clipboard_restored=bool(guard and guard.restored))
    except OSError as exc:
        # Typically UIPI / elevated target / blocked desktop.
        return done(True, "preview_only", 0,
                    f"Windows refused the injected text ({exc.strerror or exc}) — "
                    f"the text is held here instead", held_for_user=True)
    except ClipboardError as exc:
        return done(True, "preview_only", 0,
                    f"{exc} — the text is held here so you can paste it yourself",
                    held_for_user=True)
    finally:
        _restore_modifiers(held_modifiers)


def undo_last_insertion() -> InsertResult:
    """Remove only the characters this app inserted, and only if nothing else
    has been typed since. Otherwise refuse - we will not undo the user's own
    edits."""
    started = time.monotonic()
    global _last_insert
    with _undo_lock:
        rec = dict(_last_insert) if _last_insert else None
    if not rec:
        return InsertResult(False, "refused", 0, "there is nothing to undo")
    if rec.get("dirty"):
        return InsertResult(
            False, "refused", 0,
            "you typed or pressed keys after that insertion, so an automatic undo "
            "could remove your own work — use Ctrl+Z in the app instead",
        )
    if time.time() - rec.get("ts", 0) > 120:
        return InsertResult(False, "refused", 0,
                            "that insertion is more than two minutes old; use Ctrl+Z "
                            "in the app instead")

    chars = int(rec.get("chars", 0))
    if chars <= 0 or chars > 5000:
        return InsertResult(False, "refused", 0, "nothing safe to undo")

    inputs: list[INPUT] = []
    for _ in range(chars):
        inputs.append(_key(0x08))          # VK_BACK
        inputs.append(_key(0x08, up=True))
    held = _release_modifiers()
    try:
        for i in range(0, len(inputs), SENDINPUT_CHUNK):
            _send(inputs[i:i + SENDINPUT_CHUNK])
        with _undo_lock:
            _last_insert = None
        return InsertResult(
            True, "undo", chars,
            f"removed the {chars} characters this app inserted",
            latency_ms=(time.monotonic() - started) * 1000.0,
        )
    except OSError as exc:
        return InsertResult(False, "refused", 0, f"could not undo: {exc}")
    finally:
        _restore_modifiers(held)
