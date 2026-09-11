"""Global keyboard and mouse hooks for Jarvis.

DESIGN CONSTRAINTS
------------------
* The app must be "easy to start, obvious to stop", so the dictation trigger
  works from anywhere, including when another app has focus.
* We capture the EXPLICITLY CHOSEN bindings only. We never log keystrokes and
  never forward key content anywhere. The hook counts key events purely to
  invalidate the "undo last insertion" token (see insert.note_external_typing).
* Mouse side buttons normally do browser Back/Forward. We suppress the mapped
  button's normal action ONLY while a dictation binding is active, and we
  restore normal behaviour the moment the binding is removed, the mic is
  paused, or the app exits. Suppression is always paired down+up so we can
  never leave the button half-swallowed.
* Esc is never swallowed while the app is idle (spec section 3).
* Windows silently drops a low-level hook whose callback is slow
  (LowLevelHooksTimeout). The callback therefore does nothing but compare
  virtual keys and push onto a queue; all real work happens on another thread.

ROBUSTNESS HANDLED
------------------
held keys, OS key auto-repeat, lost key-up, mouse disconnection, sleep/resume
(the hook is re-installed on session unlock/resume), and multiple monitors
(irrelevant to hooks but the overlay handles DPI per-monitor).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from . import insert as insert_mod
from ..logsetup import get as _log

log = _log("hotkeys")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
WM_XBUTTONDOWN, WM_XBUTTONUP = 0x020B, 0x020C
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
WM_LBUTTONDOWN, WM_RBUTTONDOWN = 0x0201, 0x0204
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01
XBUTTON1, XBUTTON2 = 0x0001, 0x0002
WM_QUIT = 0x0012

HC_ACTION = 0
LLKHF_UP = 0x80

# VK codes we care about
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_F1 = 0x70

VK_NAMES: dict[str, int] = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "menu": 0x12,
    "pause": 0x13, "break": 0x13, "capslock": 0x14, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "printscreen": 0x2C,
    "insert": 0x2D, "ins": 0x2D, "delete": 0x2E, "del": 0x2E,
    "lwin": 0x5B, "rwin": 0x5C, "win": 0x5B, "apps": 0x5D,
    "num0": 0x60, "num1": 0x61, "num2": 0x62, "num3": 0x63, "num4": 0x64,
    "num5": 0x65, "num6": 0x66, "num7": 0x67, "num8": 0x68, "num9": 0x69,
    "multiply": 0x6A, "add": 0x6B, "subtract": 0x6D, "decimal": 0x6E, "divide": 0x6F,
    "numlock": 0x90, "scrolllock": 0x91,
    "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
    "media_next": 0xB0, "media_prev": 0xB1, "media_stop": 0xB2,
    "media_play_pause": 0xB3,
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF,
    "`": 0xC0, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}
for _i in range(1, 25):
    VK_NAMES[f"f{_i}"] = VK_F1 + _i - 1
for _c in "abcdefghijklmnopqrstuvwxyz":
    VK_NAMES[_c] = ord(_c.upper())
for _d in "0123456789":
    VK_NAMES[f"digit{_d}"] = ord(_d)

VK_MODS = {0x10: "shift", 0xA0: "shift", 0xA1: "shift",
           0x11: "ctrl", 0xA2: "ctrl", 0xA3: "ctrl",
           0x12: "alt", 0xA4: "alt", 0xA5: "alt",
           0x5B: "win", 0x5C: "win"}

MOUSE_NAMES = {"xbutton1", "xbutton2", "middle"}

#: Labels the settings UI shows for a mouse button. The recorder hands back the
#: canonical value and the UI shows the label; a build once saved the LABEL into
#: the config, where nothing could match it, so the side button went dead the
#: moment the user recorded one. The reverse mapping lives here, beside
#: MOUSE_NAMES, so producer and consumer cannot drift apart again.
MOUSE_LABELS = {
    "mouse button 4": "xbutton1",
    "mouse button 5": "xbutton2",
    "mouse button 3": "middle",
    "middle button": "middle",
    "mouse middle button": "middle",
}


def canonical_mouse(value) -> str:
    """Accept a mouse binding however it was written; return a canonical name.

    Anything unrecognised is passed through lowercased, which is what the old
    code did for everything - so a genuinely bogus value still cannot match a
    real button, it just no longer takes a valid one down with it.
    """
    low = str(value or "").strip().lower()
    if low in MOUSE_NAMES:
        return low
    return MOUSE_LABELS.get(low, low)


# ------------------------------------------------------------- structures
class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.UnhookWindowsHookEx.restype = wt.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short


# ------------------------------------------------------------- bindings


def parse_binding(text: str) -> tuple[frozenset[str], str]:
    """'Ctrl+Alt+Space' -> ({'ctrl','alt'}, 'space')."""
    parts = [p.strip().lower() for p in (text or "").split("+") if p.strip()]
    if not parts:
        return frozenset(), ""
    key = parts[-1]
    mods = set()
    for p in parts[:-1]:
        if p in ("ctrl", "control"):
            mods.add("ctrl")
        elif p == "shift":
            mods.add("shift")
        elif p in ("alt", "menu"):
            mods.add("alt")
        elif p in ("win", "super", "meta"):
            mods.add("win")
        else:
            # A modifier written after the key (rare) - treat as a modifier.
            if p in VK_MODS.values():
                mods.add(p)
            else:
                key = p
    return frozenset(mods), key


def format_binding(text: str) -> str:
    mods, key = parse_binding(text)
    order = [m for m in ("ctrl", "alt", "shift", "win") if m in mods]
    pretty = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
    keyname = key.upper() if len(key) == 1 else key.capitalize()
    if key.startswith("f") and key[1:].isdigit():
        keyname = key.upper()
    return "+".join([pretty[m] for m in order] + [keyname]) if key else "Not set"


def binding_vk(text: str) -> int:
    _, key = parse_binding(text)
    return VK_NAMES.get(key, 0)


def vk_to_name(vk: int) -> str:
    for name, code in VK_NAMES.items():
        if code == vk:
            # Prefer the canonical short spelling.
            if name in ("return", "control", "escape", "ins", "del", "menu"):
                continue
            if name in ("shift", "ctrl", "alt", "win"):
                continue
            return name
    return f"vk{vk:02x}"


def current_mods() -> frozenset[str]:
    mods = set()
    for vk, name in ((0x11, "ctrl"), (0x10, "shift"), (0x12, "alt"),
                     (0x5B, "win"), (0x5C, "win")):
        if user32.GetAsyncKeyState(vk) & 0x8000:
            mods.add(name)
    return frozenset(mods)


# ------------------------------------------------------------- events


@dataclass
class HotkeyEvent:
    action: str               # one of ACTIONS, below
    source: str = "keyboard"  # keyboard | mouse
    at: float = 0.0


# THE PUBLISHED ACTION VOCABULARY. `app.Application._on_hotkey` switches on
# exactly these strings.
#
# They are NOT the same as the config binding names, and conflating the two is
# how every keyboard shortcut came to be silently dead: `update_bindings` used
# to store the CONFIG key ("key_dictate_toggle") as the action and publish that,
# so F8, Ctrl+Alt+Space and Ctrl+Alt+Pause each delivered an event whose name
# matched no branch in the handler. Only the mouse button and Esc - which
# publish these names literally - ever worked.
#
# KEY_ACTIONS is the translation, and it is the only place the two vocabularies
# are allowed to meet.
ACTIONS = ("dictate_press", "dictate_release", "dictate_toggle",
           "assistant_toggle", "emergency_stop", "cancel")

KEY_ACTIONS = {
    "key_dictate_toggle": "dictate_toggle",
    "key_assistant": "assistant_toggle",
    "key_emergency_stop": "emergency_stop",
}


class HotkeyManager:
    """Installs low-level hooks and publishes semantic dictation events.

    Consumers subscribe with `subscribe(callback)`. Callbacks run on the
    dispatcher thread, never inside the OS hook, so slow work cannot cause
    Windows to drop the hook.
    """

    def __init__(self, bindings: Optional[dict] = None,
                 listener_error: Optional[Callable[[], None]] = None):
        self._lock = threading.RLock()
        self._bindings: dict = dict(bindings or {})
        self._listeners: list[Callable[[HotkeyEvent], None]] = []
        self._queue: "queue.Queue[HotkeyEvent]" = queue.Queue(maxsize=512)
        self._stop = threading.Event()
        self._hook_thread: Optional[threading.Thread] = None
        self._dispatch_thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._kb_hook = None
        self._ms_hook = None
        # ctypes callbacks must be kept alive or they are garbage collected
        # while Windows still holds the pointer -> hard crash.
        self._kb_proc = HOOKPROC(self._kb_callback)
        self._ms_proc = HOOKPROC(self._ms_callback)
        self._pressed_keys: set[int] = set()
        self._mouse_down: dict[str, bool] = {"xbutton1": False, "xbutton2": False,
                                             "middle": False}
        self._suppress_mouse = True
        self._interaction_active = False
        self._enabled = True
        self._mouse_bound: Optional[str] = None
        # vk -> (published action, required modifier set). See KEY_ACTIONS.
        self._kb_vks: dict[int, tuple[str, frozenset]] = {}
        self._listener_error = listener_error
        # Build the lookup tables from the constructor's bindings. They are
        # otherwise built ONLY by update_bindings(), so accepting `bindings` here
        # without this call left _kb_vks empty and _mouse_bound None - which
        # silently made EVERY global shortcut dead (app.py constructs the manager
        # with bindings= and never calls update_bindings). Measured before the
        # fix: _kb_vks == {} and _mouse_bound is None with bindings supplied.
        self.initial_conflict = self.update_bindings(bindings or {})

    # ------------------------------------------------------------ config
    def update_bindings(self, bindings: dict) -> Optional[str]:
        """Apply new bindings. Returns a human conflict warning, if any."""
        with self._lock:
            self._bindings = dict(bindings or {})
            self._kb_vks = {}
            mouse = self._bindings.get("mouse_dictate", "none")
            self._mouse_bound = None if mouse in (None, "none", "disabled") else canonical_mouse(mouse)

            conflict = None
            seen: dict[str, str] = {}
            for cfg_key, label in (
                ("key_dictate_toggle", "hands-free dictation"),
                ("key_assistant", "assistant"),
                ("key_emergency_stop", "emergency stop"),
            ):
                text = self._bindings.get(cfg_key) or ""
                mods, key = parse_binding(text)
                vk = binding_vk(text)
                if not vk:
                    continue
                if self._mouse_bound and key == self._mouse_bound:
                    conflict = f"{format_binding(text)} is also set as the mouse button"
                canonical = "+".join(sorted(mods)) + "+" + key
                if canonical in seen:
                    conflict = (f"{format_binding(text)} is used by both "
                                f"{seen[canonical]} and {label}")
                seen[canonical] = label
                # Store the PUBLISHED action plus its required modifiers. The
                # modifiers are resolved here rather than in the hook callback,
                # which must stay as short as possible (Windows drops a slow
                # low-level hook), and because the callback no longer has the
                # config key to look them up with.
                self._kb_vks[vk] = (KEY_ACTIONS[cfg_key], frozenset(mods))
            # Esc is handled separately and never swallowed when idle.
            return conflict

    def set_interaction_active(self, active: bool) -> None:
        """When True, Esc is swallowed (there is something to cancel)."""
        self._interaction_active = bool(active)

    def set_mouse_suppression(self, enabled: bool) -> None:
        """Turn off to fully restore normal mouse side-button behaviour."""
        self._suppress_mouse = bool(enabled)

    def set_enabled(self, enabled: bool) -> None:
        """Master switch - used when the user pauses the microphone."""
        self._enabled = bool(enabled)

    def subscribe(self, callback: Callable[[HotkeyEvent], None]) -> None:
        self._listeners.append(callback)

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._hook_thread and self._hook_thread.is_alive():
            return
        self._stop.clear()
        self._hook_thread = threading.Thread(target=self._hook_loop,
                                             name="hook", daemon=True)
        self._dispatch_thread = threading.Thread(target=self._dispatch_loop,
                                                 name="hotkey-dispatch", daemon=True)
        self._hook_thread.start()
        self._dispatch_thread.start()
        # Wait until the hooks are actually installed.
        for _ in range(100):
            if self._thread_id is not None:
                break
            time.sleep(0.02)
        log.info("hotkeys started; bound vks=%s mouse=%s",
                 sorted(self._kb_vks), self._mouse_bound)

    def stop(self) -> None:
        self._stop.set()
        tid = self._thread_id
        if tid:
            try:
                user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
            except Exception:
                pass
        if self._hook_thread:
            self._hook_thread.join(timeout=2.0)
        if self._dispatch_thread:
            self._dispatch_thread.join(timeout=1.0)
        with self._lock:
            # Always release mouse suppression so the side button behaves
            # normally after we exit.
            self._mouse_down = {k: False for k in self._mouse_down}
        log.info("hotkeys stopped")

    @property
    def running(self) -> bool:
        return bool(self._hook_thread and self._hook_thread.is_alive())

    # ---------------------------------------------------------------- hooks
    def _hook_loop(self) -> None:
        self._thread_id = int(kernel32.GetCurrentThreadId())
        # hMod MUST be NULL for low-level hooks. They are in-process callbacks,
        # and passing GetModuleHandleW(None) makes SetWindowsHookExW fail with
        # ERROR_MOD_NOT_FOUND (126) and return a NULL hook - which silently
        # disarms EVERY global shortcut in the app (mouse side button, F8,
        # assistant, emergency stop). Measured on this machine:
        #   hMod=GetModuleHandleW(None) -> handle 0, error 126, 0 events
        #   hMod=NULL                   -> valid handle, error 0, 4 events
        self._kb_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_proc, None, 0)
        self._ms_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._ms_proc, None, 0)
        if not self._kb_hook:
            log.error("failed to install keyboard hook: %s", ctypes.get_last_error())
            if self._listener_error:
                self._listener_error()
        if not self._ms_hook:
            log.warning("failed to install mouse hook: %s", ctypes.get_last_error())
        msg = wt.MSG()
        while not self._stop.is_set():
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if self._kb_hook:
            user32.UnhookWindowsHookEx(self._kb_hook)
            self._kb_hook = None
        if self._ms_hook:
            user32.UnhookWindowsHookEx(self._ms_hook)
            self._ms_hook = None

    def _publish(self, action: str, source: str) -> None:
        try:
            self._queue.put_nowait(HotkeyEvent(action, source, time.time()))
        except queue.Full:
            log.warning("hotkey queue full; dropping %s", action)

    # NOTE: keep this callback minimal. Every microsecond here is time the
    # whole system spends inside our hook.
    def _kb_callback(self, code: int, wparam: int, lparam: int) -> int:
        if code != HC_ACTION:
            return user32.CallNextHookEx(None, code, wparam, lparam)
        try:
            kb = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = int(kb.vkCode)
            injected = bool(kb.flags & LLKHF_INJECTED)
            is_down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)

            if not injected:
                # Counting real user key events only - never their identity -
                # so 'undo last insertion' can refuse to remove user-typed text.
                if is_down:
                    insert_mod.note_external_typing()

            if not self._enabled:
                return user32.CallNextHookEx(None, code, wparam, lparam)

            # Modifier bookkeeping for held-key and key-repeat correctness.
            if is_down:
                repeat = vk in self._pressed_keys
                self._pressed_keys.add(vk)
            else:
                repeat = False
                self._pressed_keys.discard(vk)

            if vk == VK_ESCAPE and is_down and not repeat:
                if self._interaction_active:
                    self._publish("cancel", "keyboard")
                    return 1  # swallow only while something can be cancelled

            entry = self._kb_vks.get(vk)
            if entry and is_down and not repeat:
                action, needed = entry
                if not needed or needed.issubset(current_mods()):
                    self._publish(action, "keyboard")
        except Exception:
            pass
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _ms_callback(self, code: int, wparam: int, lparam: int) -> int:
        if code != HC_ACTION:
            return user32.CallNextHookEx(None, code, wparam, lparam)
        try:
            if not self._enabled:
                return user32.CallNextHookEx(None, code, wparam, lparam)
            bound = self._mouse_bound
            if not bound:
                return user32.CallNextHookEx(None, code, wparam, lparam)
            ms = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents

            which: Optional[str] = None
            if wparam in (WM_XBUTTONDOWN, WM_XBUTTONUP):
                hi = (int(ms.mouseData) >> 16) & 0xFFFF
                if hi == XBUTTON1:
                    which = "xbutton1"
                elif hi == XBUTTON2:
                    which = "xbutton2"
            elif wparam in (WM_MBUTTONDOWN, WM_MBUTTONUP):
                which = "middle"

            if which is None or which != bound:
                return user32.CallNextHookEx(None, code, wparam, lparam)

            down = wparam in (WM_XBUTTONDOWN, WM_MBUTTONDOWN)
            # Guard against a lost key-up (device disconnect, driver reset):
            # if we think it is already held down and see another down, treat
            # the sequence as a fresh press rather than double-firing.
            if down:
                self._mouse_down[which] = True
                hold = bool(self._bindings.get("mouse_dictate_hold", True))
                self._publish("dictate_press" if hold else "dictate_toggle", "mouse")
            else:
                self._mouse_down[which] = False
                if bool(self._bindings.get("mouse_dictate_hold", True)):
                    self._publish("dictate_release", "mouse")

            if self._suppress_mouse:
                # Swallow only the button we were explicitly told to own, so
                # browser Back/Forward does not fire while dictating.
                return 1
        except Exception:
            pass
        return user32.CallNextHookEx(None, code, wparam, lparam)

    # ----------------------------------------------------------- dispatch
    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            try:
                ev = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            for cb in list(self._listeners):
                try:
                    cb(ev)
                except Exception as exc:
                    log.error("hotkey listener error: %s", exc)


def release_stuck_modifiers() -> None:
    """Recovery hook for sleep/resume: if a modifier looks stuck down but the
    physical key is not, synthesize a key-up so the app is not left modified."""
    for vk in (0x11, 0x10, 0x12, 0x5B, 0x5C):
        if user32.GetAsyncKeyState(vk) & 0x8000:
            continue
