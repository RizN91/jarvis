"""The floating island - a real per-pixel-alpha layered Windows window.

THE SHAPE IS THE STATE
----------------------
At rest this is a small pill: the orb at its left edge and the wordmark beside
it. A LEFT wing then opens carrying what the user said (or the job that is
running); the RIGHT wing carries what the app is doing. Each is sized to its own
MEASURED content and animated by a lightly under-damped spring, and because the
island stays centred on the screen the ORB slides whenever the two wings differ
- which is the movement the whole design is built on. See `_compose`,
`_wing_targets` and `_advance`.

The sizer (`_wing_targets`) and the painter (`_draw_*_wing`) must agree exactly.
They share PAD/ICON_W/DOT_W/METER_W/DIVIDER_W/COL_GAP and `_col_b_width`, plus
SLACK for the spring's asymptotic approach. When they drifted apart, wings came
out a few pixels short and text elided inside a box that had room for it.

WHY A LAYERED WINDOW AND NOT A WEBVIEW
--------------------------------------
The island has soft glows that fade into the desktop wallpaper, and the build
spec requires it to be "non-focus-stealing". Two consequences:

  * Focus: the window is created with WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW, so it
    can never take focus from the app the user is dictating into. A normal
    WebView2/tkinter window cannot guarantee that.
  * Transparency: UpdateLayeredWindow with ULW_ALPHA gives true per-pixel alpha,
    so the glow blends with whatever is behind it. A colour-key window would
    leave a hard edge around every soft gradient.

Rendering is PIL -> premultiplied BGRA DIB -> UpdateLayeredWindow. The animation
runs on its own thread at a modest frame rate and stops when idle (the spec
forbids "a constantly animated idle screen"), and honours reduced motion.

Multi-monitor: the pill is positioned on the monitor containing the current
target window (falling back to the cursor), using the monitor's work area.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import math
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..logsetup import get as _log

log = _log("overlay")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---------------------------------------------------------------- constants
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
SW_SHOWNOACTIVATE = 4
SW_HIDE = 0
HWND_TOPMOST = -1
SWP_NOACTIVATE = 0x0010
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040
SWP_NOZORDER = 0x0004
# WM_NCHITTEST replies. HTTRANSPARENT sends the hit test on to the window
# beneath, which is what makes the pill click-through off its buttons.
HTTRANSPARENT = -1
HTCLIENT = 1
ULW_ALPHA = 0x00000002
AC_SRC_OVER, AC_SRC_ALPHA = 0x00, 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0
MONITOR_DEFAULTTONEAREST = 2

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
SM_CXSCREEN, SM_CYSCREEN = 0, 1

# Opacity of the pill body. Below 255 so the desktop and the orb's halo show
# through, which is what mockup.png does.
PILL_ALPHA = 232

# The product name shown on the island at rest, letterspaced like the mockup.
BRAND = "JARVIS"
BRAND_TAGLINE = "Ready when you are"

# Shared layout constants. `_wing_targets` sizes a wing with these and
# `_draw_*_wing` paints with the same ones - keep them in step or a wing ends
# up sized for one layout and drawn with another.
PAD = 18            # a wing's own left/right padding
ICON_W = 20         # the leading glyph on the left wing
DOT_W = 18          # the status dot plus its gap
METER_W = 96        # the live level meter on the right
DIVIDER_W = 14      # the hairline between the orb and the right wing
COL_GAP = 18        # between the status column and the meter/ESC column
# A wing is drawn at its ANIMATED width, and a spring approaches its target
# asymptotically - it settles a fraction under it and `_settled` tolerates
# 0.4 px. Sizing a wing to exactly its content therefore starved the painter by
# one pixel and elided "Dictating…" to "Dictatin…" inside a box that had been
# measured for it. This is the margin that makes the two agree.
SLACK = 8


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", RECT), ("rcWork", RECT),
                ("dwFlags", wt.DWORD)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT), ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR),
    ]


user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.UpdateLayeredWindow.argtypes = [
    wt.HWND, wt.HDC, ctypes.POINTER(POINT), ctypes.POINTER(SIZE), wt.HDC,
    ctypes.POINTER(POINT), wt.COLORREF, ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
user32.UpdateLayeredWindow.restype = wt.BOOL
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.MonitorFromPoint.argtypes = [POINT, wt.DWORD]
user32.MonitorFromPoint.restype = wt.HANDLE
user32.GetMonitorInfoW.argtypes = [wt.HANDLE, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wt.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
user32.PeekMessageW.restype = wt.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.IsWindow.argtypes = [wt.HWND]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.ScreenToClient.argtypes = [wt.HWND, ctypes.POINTER(POINT)]
user32.ScreenToClient.restype = wt.BOOL
user32.UpdateWindow.argtypes = [wt.HWND]
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]


def enable_dpi_awareness() -> str:
    """Per-monitor DPI awareness so the pill is crisp on scaled displays."""
    try:
        fn = user32.SetProcessDpiAwarenessContext
        fn.argtypes = [ctypes.c_void_p]
        fn.restype = wt.BOOL
        if fn(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except Exception:
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        return "per-monitor"
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
        return "system"
    except Exception:
        return "none"


# ------------------------------------------------------------------- theme
@dataclass
class Theme:
    # Frosted glass, not painted plastic. The reference island is a dark slate
    # panel with a single soft light hairline all the way round - the earlier
    # saturated blue border made it read as a button rather than a surface.
    bg_top: tuple = (48, 62, 82)
    bg_bottom: tuple = (10, 16, 27)
    border: tuple = (174, 202, 230)      # silver-blue glass highlight
    border_dim: tuple = (86, 105, 140)
    text: tuple = (232, 238, 248)
    muted: tuple = (146, 160, 186)
    dim: tuple = (112, 126, 152)
    accent: tuple = (52, 130, 250)
    accent2: tuple = (72, 148, 255)
    teal: tuple = (45, 215, 200)
    warn: tuple = (251, 191, 36)
    danger: tuple = (242, 96, 96)
    success: tuple = (52, 211, 153)


# state -> (label colour, orb colours, animation mode)
STATE_STYLE = {
    "sleeping":  (Theme.muted,   (44, 60, 100),  "still"),
    "dictating": (Theme.accent,  (48, 128, 250), "bars"),
    "listening": (Theme.accent,  (48, 128, 250), "bars"),
    "thinking":  (Theme.teal,    (96, 110, 240), "spin"),
    "working":   (Theme.teal,    (40, 205, 195), "spin"),
    "approval":  (Theme.warn,    (251, 191, 36), "pulse"),
    "error":     (Theme.danger,  (242, 96, 96),  "pulse"),
    "muted":     (Theme.muted,   (90, 100, 120), "still"),
}

STATE_LABELS = {
    "sleeping": "Sleeping", "dictating": "Dictating", "listening": "Listening",
    "thinking": "Thinking", "working": "Working", "approval": "Approval needed",
    "error": "Error", "muted": "Muted",
}

# States whose waveform actually moves. "sleeping" and "muted" are deliberately
# absent: the spec forbids a constantly animated idle screen.
ANIMATED_STATES = frozenset({
    "dictating", "listening", "thinking", "working", "approval", "error",
})

# States whose label takes a trailing ellipsis because something is still in
# progress. "Error..." and "Approval needed..." would both be lies.
PROGRESS_STATES = frozenset({"dictating", "listening", "thinking", "working"})

_FONTS: dict = {}


def _font(size: int, bold: bool = False):
    key = (size, bold)
    if key in _FONTS:
        return _FONTS[key]
    candidates = (
        ["segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"]
    )
    font = None
    for name in candidates:
        for base in (r"C:\Windows\Fonts\\", ""):
            try:
                font = ImageFont.truetype(base + name, size)
                break
            except Exception:
                continue
        if font:
            break
    if font is None:
        try:
            font = ImageFont.load_default(size)
        except Exception:
            font = ImageFont.load_default()
    _FONTS[key] = font
    return font


def _spaced(text: str) -> str:
    """'JARVIS' -> 'J A R V I S'. Letterspacing, the only way PIL offers it."""
    return " ".join(text)


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


# A throwaway Draw used ONLY to measure text. The island sizes its wings to
# their real content, which means measuring before there is anything to draw on.
_MEASURE = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

_SPHERE_CACHE: dict = {}
_GLOW_CACHE: dict = {}
_MASK_CACHE: dict = {}


def _bar_geometry(W: int, H: int, x0: float, island_w: float, top: float,
                  bot: float, radius: int):
    """Rounded-rect bar mask + its hairline edge, cached by geometry.

    WHY THIS IS CACHED: the supersampled mask resize and the MinFilter that
    derives the border were measured at 51% of a 14.8 ms frame (31% resize, 20%
    rankfilter) - and they depend ONLY on the island's geometry, which does not
    change at all once the springs have settled. Settled is the normal state:
    the springs only move for the first few hundred ms of a state change, so
    this removes roughly half the per-frame cost in the common case.

    The key is quantised to 0.1px so the mask still updates smoothly while the
    island IS animating; a sub-pixel difference is invisible at 3x supersampling.
    """
    key = (W, H, round(x0, 1), round(island_w, 1), round(top, 1), round(bot, 1),
           radius)
    hit = _MASK_CACHE.get(key)
    if hit is not None:
        return hit
    from PIL import ImageChops
    mask = Image.new("L", (W * 3, H * 3), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [x0 * 3, top * 3, (x0 + island_w - 1) * 3, (bot - 1) * 3],
        radius=radius * 3, fill=255)
    mask = mask.resize((W, H), Image.Resampling.LANCZOS)
    edge = ImageChops.subtract(mask, mask.filter(ImageFilter.MinFilter(3)))
    # The alarmed inward bloom is only used by the error state; it is built
    # here so the expensive MinFilter(9) is also paid once per geometry.
    bloom_edge = ImageChops.subtract(mask, mask.filter(ImageFilter.MinFilter(9)))
    hit = (mask, edge, bloom_edge)
    _MASK_CACHE[key] = hit
    if len(_MASK_CACHE) > 8:
        _MASK_CACHE.pop(next(iter(_MASK_CACHE)))
    return hit


def _build_sphere(size: int, orb_rgb: tuple) -> Image.Image:
    """Vectorised glass-sphere render, cached per colour.

    The mockup's orb is a DEEP saturated blue globe with a bright luminous rim,
    not a pale grey ball. An earlier version lerped toward white at the top,
    which desaturated it; this builds a dark core, a saturated body and a
    narrow bright rim, plus a specular highlight.

    A per-pixel Python loop over ~12k pixels per frame is far too slow for 30fps,
    so numpy builds it once and the image is reused until the colour changes.
    """
    import numpy as np
    key = (size, orb_rgb)
    cached = _SPHERE_CACHE.get(key)
    if cached is not None:
        return cached

    output_size = size
    size *= 3  # Cached antialiasing, not an extra per-frame sphere render.

    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    c = (size - 1) / 2.0
    r = size / 2.0
    dx = (xx - c) / r
    dy = (yy - c) / r
    dist = np.sqrt(dx * dx + dy * dy)

    # Measured off mockup.png down the orb's centre line: (63,123,191) near the
    # top edge, (24,147,253) through the middle, (20,61,180) low down, with a
    # bright (159,210,254) rim. That tonal range is what makes it read as glass
    # rather than as a flat disc - so the sphere is SHADED by a light direction,
    # not just a radial ramp. Two earlier attempts failed the opposite ways: a
    # near-black core with a white ring (a hole), then a flat exponent (a
    # uniform pale ball).
    core = np.array(orb_rgb, dtype=np.float32) * np.array((.16, .22, .34))
    body = np.array(orb_rgb, dtype=np.float32)                 # saturated azure
    # Rim = the orb colour lifted 55% toward white, NOT a multiply-and-clip:
    # multiplying drove every bright state (error red, muted grey, approval
    # amber) to pure white, so all three orbs lost their identity.
    _orb = np.array(orb_rgb, dtype=np.float32)
    rim_c = _orb + (255.0 - _orb) * 0.55

    base = core + (body - core) * np.clip(dist, 0.0, 1.0)[..., None]

    # Lambert term across the sphere's own normal: light from the upper left.
    nz = np.sqrt(np.clip(1.0 - dist * dist, 0.0, 1.0))
    lam = np.clip(dx * -0.45 + dy * -0.52 + nz * 0.73, 0.0, 1.0)
    base = base * (0.52 + 0.66 * lam)[..., None]

    # Narrow luminous rim.
    rim = np.exp(-(((dist - 0.945) / 0.055) ** 2))
    base = base + (rim_c - base) * np.clip(rim, 0, 1)[..., None] * 0.95
    # Outer edge darkens again so the rim reads as a highlight, not a flat band.
    edge = np.clip((dist - 0.97) / 0.03, 0.0, 1.0)
    base = base + (core * 0.45 - base) * (edge * 0.6)[..., None]
    # Specular highlight, upper-left.
    hl = np.clip(1.0 - np.sqrt((dx + 0.38) ** 2 + (dy + 0.45) ** 2) / 0.42, 0, 1) ** 2
    base = base + (np.array((255.0, 255.0, 255.0), dtype=np.float32) - base) \
        * (hl * 0.55)[..., None]

    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    rgba[..., :3] = np.clip(base, 0, 255).astype(np.uint8)
    # Feather the very edge by one pixel so there is no hard aliased ring.
    a = np.clip((1.0 - dist) * r * 1.2, 0.0, 1.0)
    rgba[..., 3] = (a * 255).astype(np.uint8)
    img = Image.fromarray(rgba, "RGBA").resize(
        (output_size, output_size), Image.Resampling.LANCZOS)
    _SPHERE_CACHE[key] = img
    if len(_SPHERE_CACHE) > 12:
        _SPHERE_CACHE.pop(next(iter(_SPHERE_CACHE)))
    return img


def _build_glow(width: int, height: int, cx: int, cy: int, orb_d: int,
                orb_rgb: tuple) -> Image.Image:
    """Soft state-coloured halo.

    Built as a smooth numpy radial falloff rather than stacked ellipses: stacked
    ellipses left visible hard ring edges at the outer boundary (visible in the
    first screenshot), because the outermost ellipse had no falloff to zero.
    """
    import numpy as np
    key = (width, height, cx, cy, orb_d, orb_rgb)
    cached = _GLOW_CACHE.get(key)
    if cached is not None:
        return cached

    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    r_inner = orb_d * 0.5
    # Keep the halo TIGHT and circular. A wide radius made the glow read as a
    # horizontal lens smudge across the whole pill rather than a bloom around
    # the orb, because the falloff ellipse was much wider than the pill height.
    #
    # The reference bloom is nonetheless SOFTER than a hard ring: a narrow
    # high-amplitude ring term (the old 0.34) drew a visible circle at the orb's
    # edge, so the tight term is now lower and wider and the wide falloff takes
    # more of the weight. This function is cached, so the extra softness costs
    # nothing per frame.
    r_outer = r_inner + 64.0
    falloff = np.clip((r_outer - dist) / (r_outer - r_inner), 0.0, 1.0) ** 2.4
    tight = np.exp(-(((dist - r_inner * 0.90) / (r_inner * 0.40)) ** 2)) * 0.20
    alpha = np.clip(falloff * 0.32 + tight, 0.0, 1.0)

    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgba[..., 0] = orb_rgb[0]
    rgba[..., 1] = orb_rgb[1]
    rgba[..., 2] = orb_rgb[2]
    rgba[..., 3] = (alpha * 255).astype(np.uint8)
    glow = Image.fromarray(rgba, "RGBA").filter(ImageFilter.GaussianBlur(7))
    _GLOW_CACHE[key] = glow
    if len(_GLOW_CACHE) > 24:
        _GLOW_CACHE.pop(next(iter(_GLOW_CACHE)))
    return glow


class _Canvas:
    """A 32-bit top-down DIB section for UpdateLayeredWindow."""

    def __init__(self, width: int, height: int):
        self.width, self.height = int(width), int(height)
        self.screen_dc = user32.GetDC(None)
        self.mem_dc = gdi32.CreateCompatibleDC(self.screen_dc)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = self.width
        bmi.bmiHeader.biHeight = -self.height      # negative = top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        self.bits = ctypes.c_void_p()
        self.bitmap = gdi32.CreateDIBSection(
            self.mem_dc, ctypes.byref(bmi), DIB_RGB_COLORS,
            ctypes.byref(self.bits), None, 0)
        self.old = gdi32.SelectObject(self.mem_dc, self.bitmap)

    def blit(self, image: Image.Image) -> None:
        """Copy an RGBA PIL image into the DIB as premultiplied BGRA."""
        if image.size != (self.width, self.height):
            image = image.resize((self.width, self.height), Image.LANCZOS)
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        import numpy as np
        arr = np.asarray(image, dtype=np.uint8).astype(np.uint16)
        alpha = arr[:, :, 3:4]
        # Premultiply: UpdateLayeredWindow with AC_SRC_ALPHA expects premultiplied.
        rgb = (arr[:, :, :3] * alpha) // 255
        bgra = np.empty((self.height, self.width, 4), dtype=np.uint8)
        bgra[:, :, 0] = rgb[:, :, 2]
        bgra[:, :, 1] = rgb[:, :, 1]
        bgra[:, :, 2] = rgb[:, :, 0]
        bgra[:, :, 3] = arr[:, :, 3]
        ctypes.memmove(self.bits, bgra.tobytes(), self.width * self.height * 4)

    def release(self) -> None:
        try:
            if self.old:
                gdi32.SelectObject(self.mem_dc, self.old)
            if self.bitmap:
                gdi32.DeleteObject(self.bitmap)
            if self.mem_dc:
                gdi32.DeleteDC(self.mem_dc)
            if self.screen_dc:
                user32.ReleaseDC(None, self.screen_dc)
        except Exception:
            pass


@dataclass
class PillState:
    state: str = "sleeping"
    transcript: str = ""
    detail: str = ""
    actions: list[str] = field(default_factory=list)
    esc_hint: str = ""
    session_seconds: float = 0.0
    spend_usd: float = 0.0
    level: float = 0.0
    # An idle nudge ("Say Hey Jarvis…"). Separate from `transcript` so the
    # island can decide to stay collapsed rather than open a wing for a hint.
    hint: str = ""
    # True while the ASSISTANT is speaking, as opposed to the user. Drives both
    # the orb animation and the status word, so the two are never confused.
    speaking: bool = False
    # A named job on the LEFT of the island, with its own progress bar - the
    # "Opening Quarterly Report" row in the mockup. Distinct from `transcript`,
    # which is what the user said, and from `detail`, which is the status
    # sub-line on the right.
    task: str = ""
    progress: float = -1.0      # 0..1; negative means "no bar"


class Overlay:
    """The floating status pill."""

    # DYNAMIC ISLAND GEOMETRY.
    #
    # `width`/`height` are the WINDOW, which never changes size - resizing a
    # layered window every frame is expensive and flickers. The island drawn
    # inside it does change size, every frame, and is what the user sees.
    #
    # At rest only the orb is drawn (76 px). The left wing grows when there is
    # something the user said to show; the right wing grows when the app is doing
    # something. The island stays centred on the screen, so the ORB itself
    # slides as the wings grow unevenly - that is the whole effect.
    def __init__(self, width: int = 940, pill_height: int = 70,
                 orb_diameter: int = 90, fps: int = 60,
                 reduced_motion: bool = False,
                 offset_y: int = 10,
                 on_action: Optional[Callable[[int, str], None]] = None):
        self.width = width
        self.pill_h = pill_height
        self.orb_d = orb_diameter
        self.globe = int(orb_diameter * 0.86)
        self.fps = max(1, min(60, fps))
        self.reduced_motion = reduced_motion
        self.offset_y = offset_y
        self.on_action = on_action

        # How far each wing may grow. The island can never exceed the window.
        self.left_max = 350
        self.right_max = 350

        # Headroom above and below for the orb bulge and its glow. The orb is
        # deliberately LARGER than the bar (90 vs 70 => 1.29x): in the reference
        # design the globe overflows the pill's top and bottom edges, and a
        # contained orb reads as a dot rather than as the app's centrepiece.
        # Measured off the reference pill, the ratio there is ~1.4; the encoded
        # bound in tests/test_overlay.py ("compact enough for a status bar",
        # orb <= 90 and height <= 170) caps it at 1.29, and that bound is
        # respected rather than loosened. Raising it is a deliberate design
        # decision for a future agent, not something to do by accident.
        self.height = orb_diameter + 80
        self.pill_top = (self.height - pill_height) // 2

        # --- animation state (all in pixels / 0..1, all spring-driven) -----
        self._left = 0.0            # current left wing width
        self._right = 0.0           # current right wing width
        self._left_v = 0.0          # spring velocities
        self._right_v = 0.0
        self._appear = 0.0          # 0 hidden -> 1 fully shown
        self._appear_v = 0.0
        self._ripple = 0.0          # expands outward on a loud peak
        self._last_peak = 0.0

        self._hwnd = 0
        self._canvas: Optional[_Canvas] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._visible = False
        self._state = PillState()
        self._phase = 0.0
        self._level_smooth = 0.0
        self._action_rects: list[tuple[int, int, int, int, str]] = []
        self.click_through = False
        self._proc_ref = None

    # ------------------------------------------------------------- lifecycle
    def create(self) -> bool:
        if self._hwnd:
            return True
        hinst = kernel32.GetModuleHandleW(None)
        cls = "JarvisPill"

        def _wndproc(hwnd, msg, wparam, lparam):
            if msg == 0x0002:      # WM_DESTROY
                user32.PostQuitMessage(0)
                return 0
            if msg == 0x0201:      # WM_LBUTTONDOWN - user clicked an action
                x = ctypes.c_short(lparam & 0xFFFF).value
                y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
                self._handle_click(x, y)
                return 0
            if msg == 0x0084:      # WM_NCHITTEST
                # The pill is a 1100x240 strip pinned to the top of the screen.
                # This used to answer HTCLIENT (1) for EVERY point, so while the
                # pill was up it swallowed every click across that strip - the
                # browser tab bar, a window's close button - and did nothing
                # with them. Answer HTTRANSPARENT everywhere except on an actual
                # chip, so clicks fall through to whatever is underneath.
                if self.click_through:
                    return HTTRANSPARENT
                pt = POINT(ctypes.c_short(lparam & 0xFFFF).value,
                           ctypes.c_short((lparam >> 16) & 0xFFFF).value)
                user32.ScreenToClient(wt.HWND(hwnd), ctypes.byref(pt))
                return HTCLIENT if self.hit_action(pt.x, pt.y) else HTTRANSPARENT
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT,
                                     wt.WPARAM, wt.LPARAM)
        self._proc_ref = WNDPROC(_wndproc)
        wc = WNDCLASSW()
        wc.lpfnWndProc = ctypes.cast(self._proc_ref, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.lpszClassName = cls
        user32.RegisterClassW(ctypes.byref(wc))

        ex = WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
        if self.click_through:
            ex |= WS_EX_TRANSPARENT
        self._hwnd = int(user32.CreateWindowExW(
            ex, cls, "Jarvis", WS_POPUP,
            0, 0, self.width, self.height, None, None, hinst, None) or 0)
        if not self._hwnd:
            log.error("failed to create the overlay window: %s",
                      ctypes.get_last_error())
            return False
        self._canvas = _Canvas(self.width, self.height)
        log.info("overlay created: %dx%d", self.width, self.height)
        return True

    def start(self) -> None:
        if not self.create():
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="overlay",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)
        if self._hwnd:
            try:
                user32.DestroyWindow(wt.HWND(self._hwnd))
            except Exception:
                pass
            self._hwnd = 0
        if self._canvas:
            self._canvas.release()
            self._canvas = None

    # ------------------------------------------------------------------ show
    def show(self, state: Optional[PillState] = None) -> None:
        with self._lock:
            if state:
                self._state = state
            if not self._visible:
                # Grow in from nothing every time it appears - but ONLY when a
                # render loop is actually running to animate it. Without that
                # check, show() on a created-but-not-started Overlay left
                # `_appear` at 0 forever and _compose returned a blank frame:
                # a window that is genuinely on screen and genuinely invisible.
                animating = bool(self._thread and self._thread.is_alive()
                                 and not self.reduced_motion)
                self._appear = 0.0 if animating else 1.0
                self._appear_v = 0.0
                self._left = self._right = 0.0 if animating else self._left
                self._left_v = self._right_v = 0.0
            self._visible = True
        if not self._hwnd and not self.create():
            return
        self._position()
        user32.ShowWindow(wt.HWND(self._hwnd), SW_SHOWNOACTIVATE)
        user32.SetWindowPos(wt.HWND(self._hwnd), wt.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        self._render()

    def hide(self) -> None:
        with self._lock:
            self._visible = False
        if self._hwnd:
            user32.ShowWindow(wt.HWND(self._hwnd), SW_HIDE)

    @property
    def visible(self) -> bool:
        return self._visible

    def set_state(self, state: Optional[str] = None,
                  transcript: Optional[str] = None,
                  detail: Optional[str] = None,
                  actions: Optional[list[str]] = None,
                  esc_hint: Optional[str] = None,
                  session_seconds: Optional[float] = None,
                  spend_usd: Optional[float] = None,
                  hint: Optional[str] = None,
                  speaking: Optional[bool] = None,
                  task: Optional[str] = None,
                  progress: Optional[float] = None) -> None:
        with self._lock:
            if state is not None:
                self._state.state = state
            if transcript is not None:
                self._state.transcript = transcript
            if detail is not None:
                self._state.detail = detail
            if actions is not None:
                self._state.actions = list(actions)
            if esc_hint is not None:
                self._state.esc_hint = esc_hint
            if session_seconds is not None:
                self._state.session_seconds = float(session_seconds)
            if spend_usd is not None:
                self._state.spend_usd = float(spend_usd)
            if hint is not None:
                self._state.hint = hint
            if speaking is not None:
                self._state.speaking = bool(speaking)
            if task is not None:
                self._state.task = task
            if progress is not None:
                self._state.progress = float(progress)
        if self._visible:
            self._render()

    def set_level(self, rms: float) -> None:
        """Feed the microphone (or speaker) level for the waveform."""
        with self._lock:
            rms = float(rms)
            # Smooth so the bars do not jitter frame to frame.
            self._level_smooth = max(rms, self._level_smooth * 0.80)
            # A sharp rise fires a ripple ring - the "it heard that" cue.
            if rms > self._last_peak * 1.6 and rms > 0.06:
                self._ripple = 1.0
            self._last_peak = max(rms, self._last_peak * 0.90)

    def set_reduced_motion(self, reduced: bool) -> None:
        self.reduced_motion = bool(reduced)

    # ---------------------------------------------------------------- clicks
    def hit_action(self, x: int, y: int) -> Optional[str]:
        """Which action chip is at this CLIENT point, if any.

        Used both by the hit test (so the rest of the pill is click-through)
        and by the click handler, so the two can never disagree about where a
        button is.
        """
        for (x0, y0, x1, y1, name) in list(self._action_rects):
            if x0 <= x <= x1 and y0 <= y <= y1:
                return name
        return None

    def _handle_click(self, x: int, y: int) -> None:
        for (x0, y0, x1, y1, name) in self._action_rects:
            if x0 <= x <= x1 and y0 <= y <= y1:
                if self.on_action:
                    try:
                        threading.Thread(target=self.on_action, args=(0, name),
                                         name="overlay-action", daemon=True).start()
                    except Exception as exc:
                        log.debug("overlay action error: %s", exc)
                return

    # -------------------------------------------------------------- geometry
    def _target_monitor_rect(self) -> RECT:
        pt = POINT()
        if not user32.GetCursorPos(ctypes.byref(pt)):
            pt = POINT(0, 0)
        mon = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if mon and user32.GetMonitorInfoW(mon, ctypes.byref(info)):
            return info.rcWork
        return RECT(0, 0, user32.GetSystemMetrics(SM_CXSCREEN),
                    user32.GetSystemMetrics(SM_CYSCREEN))

    def _position(self) -> None:
        area = self._target_monitor_rect()
        width = area.right - area.left
        x = area.left + max(0, (width - self.width) // 2)
        y = area.top + self.offset_y
        user32.SetWindowPos(wt.HWND(self._hwnd), wt.HWND(HWND_TOPMOST), x, y,
                            self.width, self.height,
                            SWP_NOACTIVATE | SWP_NOZORDER)

    # -------------------------------------------------------------- rendering
    # ------------------------------------------------------------- animation
    @staticmethod
    def _spring(cur: float, vel: float, target: float,
                stiffness: float = 0.30, damping: float = 0.68) -> tuple:
        """One step of a lightly under-damped spring.

        Under-damped on purpose: the small overshoot as a wing opens is what
        makes it read as a physical island rather than a box being resized.
        """
        vel = vel * damping + (target - cur) * stiffness
        return cur + vel, vel

    def _wing_targets(self, st: "PillState") -> tuple:
        """How wide each wing WANTS to be, in pixels.

        Left  = what the user said (transcript).
        Right = what the app is doing (status, detail, chips).
        Both 0 means the island collapses to a bare orb, which is the resting
        state the whole design is built around.
        """
        # MEASURE, do not guess. A wing that always jumps to its maximum reads
        # as a box being resized; a wing that is exactly as wide as its own
        # content is what makes this feel like a Dynamic Island.
        d = _MEASURE
        idle = self._is_idle(st)

        # ---- left: what the user said, or the named job, or nothing ---------
        left = 0.0
        if not idle:
            if st.task:
                left = (self._text_width(d, st.task, _font(15))
                        + PAD * 2 + ICON_W + 12)
            elif st.transcript:
                left = (self._text_width(d, st.transcript, _font(16))
                        + PAD * 2 + 14 + SLACK)
            elif st.state == "error":
                left = (max(self._text_width(d, "Error", _font(15, bold=True)),
                            self._text_width(d, st.detail or "Something went wrong",
                                             _font(12)))
                        + PAD * 2 + ICON_W + 12)
            if left:
                left = float(max(130.0, min(self.left_max, left)))

        # ---- right: at rest this is the brand block, otherwise the status --
        if idle:
            w = max(self._text_width(d, _spaced(BRAND), _font(11, bold=True)),
                    self._text_width(d, st.hint or BRAND_TAGLINE, _font(14)))
            return 0.0, float(min(self.right_max, w + PAD * 2 + 8))

        col_a = 0
        if self._shows_status(st):
            # Both rows start at the same x, one dot-width in, so the dot has
            # to be added ONCE around the wider of them - not only to row one.
            # Adding it to row one alone is what elided every sub-line to
            # "Processing your re...".
            col_a = DOT_W + max(
                self._text_width(d, self._status_label(st), _font(16)),
                self._text_width(d, st.detail or "", _font(12)))

        col_b = self._col_b_width(st, d)
        right = (col_a + (COL_GAP + col_b if col_b else 0)
                 + PAD * 2 + DIVIDER_W + SLACK)
        right = float(min(self.right_max, right))
        if left and right:
            # The reference keeps the orb centred between two balanced wings.
            extent = min(max(left, right), self.left_max, self.right_max)
            return extent, extent
        return left, right

    # ---- predicates the layout and the width maths MUST agree on ---------
    # Both `_wing_targets` and `_draw_right_wing` branch on these. When they
    # disagree the wing is sized for one layout and painted with another, and
    # the text elides inside a box that had room for it.
    @staticmethod
    def _is_idle(st: "PillState") -> bool:
        """At rest: nothing said, nothing running, nothing to acknowledge."""
        return (st.state in ("sleeping", "muted") and not st.transcript
                and not st.task and not st.detail and not st.esc_hint)

    def _col_b_width(self, st: "PillState", d) -> int:
        """Width of the right wing's second column: the meter, or the keycap.

        THE SIZER AND THE PAINTER BOTH CALL THIS. They used to compute it
        separately with slightly different padding, so the wing came out four
        pixels short and the status elided to "Dictatin…" inside a box that
        had been sized for it.
        """
        # Priority matters. An action the app is offering MUST be clickable, so
        # it outranks the meter, which is decoration. Losing that ordering once
        # left "Copy"/"Insert" with no hit rectangle at all.
        chips = self._chip_labels(st)
        if chips:
            w = sum(self._text_width(d, c, _font(11)) + 22 for c in chips)
            return int(w + 8 * (len(chips) - 1))
        if self._shows_meter(st):
            w = METER_W
            cost = self._cost_line(st)
            if cost:
                w = max(w, self._text_width(d, cost, _font(10)))
            return int(w)
        if st.esc_hint:
            cap, rest = self._split_hint(st.esc_hint)
            w = self._text_width(d, cap, _font(10, bold=True)) + 20
            if rest:
                w += 8 + self._text_width(d, rest, _font(12))
            return int(w)
        return 0

    @staticmethod
    def _chip_labels(st: "PillState") -> list:
        return [str(a) for a in (st.actions or [])[:2] if str(a).strip()]

    @staticmethod
    def _shows_status(st: "PillState") -> bool:
        """False when the left wing already carries the headline, so the state
        is never printed twice on the same island (the error row did that)."""
        return not (st.state == "error" and not st.transcript and not st.task)

    @staticmethod
    def _status_label(st: "PillState") -> str:
        if st.speaking:
            return "Speaking…"
        label = STATE_LABELS.get(st.state, st.state.capitalize())
        return label + "…" if st.state in PROGRESS_STATES else label

    @staticmethod
    def _shows_meter(st: "PillState") -> bool:
        """A live level meter belongs only where audio is actually moving AND
        there is nothing more useful to put there. Dictating shows the ESC
        keycap instead: mid-dictation, how to stop matters more than a meter."""
        return bool(st.speaking) or st.state == "listening"

    @staticmethod
    def _cost_line(st: "PillState") -> str:
        bits = []
        if st.session_seconds > 0:
            bits.append(Overlay._clock(st.session_seconds))
        if st.spend_usd > 0:
            bits.append(f"≈ ${st.spend_usd:.4f}")
        return "  ·  ".join(bits)

    @staticmethod
    def _split_hint(hint: str) -> tuple:
        """'ESC to stop' -> ('ESC', 'to stop'), so ESC can be a real keycap."""
        text = (hint or "").strip()
        if not text:
            return "", ""
        head, _, tail = text.partition(" ")
        return head, tail.strip()

    def _advance(self, st: "PillState", dt: float) -> None:
        left_t, right_t = self._wing_targets(st)
        if self.reduced_motion:
            self._left, self._right, self._appear = left_t, right_t, 1.0
            self._left_v = self._right_v = self._appear_v = 0.0
            self._ripple = 0.0
            return
        self._left, self._left_v = self._spring(self._left, self._left_v, left_t)
        self._right, self._right_v = self._spring(self._right, self._right_v, right_t)
        self._appear, self._appear_v = self._spring(
            self._appear, self._appear_v, 1.0, stiffness=0.34, damping=0.62)
        self._appear = max(0.0, min(1.06, self._appear))
        self._ripple = max(0.0, self._ripple - dt * 1.7)
        self._level_smooth *= 0.90

    def _settled(self, st: "PillState") -> bool:
        """True when nothing is moving, so the loop can idle instead of redraw."""
        left_t, right_t = self._wing_targets(st)
        return (abs(self._left - left_t) < 0.4 and abs(self._right - right_t) < 0.4
                and abs(1.0 - self._appear) < 0.01 and self._ripple <= 0.0
                and self._level_smooth < 0.005)

    def _loop(self) -> None:
        frame = 1.0 / self.fps
        while not self._stop.is_set():
            # `animated` MUST be initialised here, outside the `if visible`
            # branch. It used to be assigned only when the pill was visible, and
            # the pill starts hidden - so the very first iteration raised
            # UnboundLocalError, which the except below swallowed as a debug
            # line, five times a second, for as long as the pill stayed hidden.
            animated = False
            try:
                with self._lock:
                    visible = self._visible
                    st = PillState(**vars(self._state))
                    reduced = self.reduced_motion
                if visible:
                    # Animate while a wing is still moving, the orb is reacting,
                    # or the state itself is a live one. A settled idle island
                    # stops redrawing entirely - the spec forbids a constantly
                    # animated idle screen.
                    live_state = (not reduced) and st.state in ANIMATED_STATES
                    animated = live_state or not self._settled(st)
                    if animated:
                        self._phase += frame
                    self._advance(st, frame)
                    self._render()
                self._stop.wait(frame if animated else 0.2)
            except Exception as exc:
                log.debug("overlay loop error: %s", exc)
                self._stop.wait(0.2)

    def _render(self) -> None:
        if not self._hwnd or not self._canvas:
            return
        with self._lock:
            st = PillState(**vars(self._state))
            level = self._level_smooth
            phase = self._phase
            try:
                img = self._compose(st, level, phase)
                self._canvas.blit(img)
                self._update_window()
            except Exception as exc:
                log.error("overlay render failed: %s", exc)

    def _compose(self, st: PillState, level: float, phase: float) -> Image.Image:
        """Draw one frame of the island.

        THE SHAPE IS THE STATE. At rest the island is just the orb. The left
        wing carries what the user said; the right wing carries what the app is
        doing. Both are spring-animated in `_advance`, and because the island
        stays centred on the screen the ORB slides whenever the wings differ -
        which is the movement the design is built on.
        """
        W, H = self.width, self.height
        base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(base)

        label_rgb, orb_rgb, mode = STATE_STYLE.get(st.state, STATE_STYLE["sleeping"])
        if st.speaking:
            label_rgb, orb_rgb, mode = Theme.teal, (40, 205, 195), "speak"

        left = max(0.0, self._left)
        right = max(0.0, self._right)
        appear = max(0.0, min(1.0, self._appear))

        r_orb = self.orb_d / 2.0
        # Scale + settle on appear, so it pops in rather than blinking on.
        scale = 0.62 + 0.38 * min(1.0, self._appear)
        r_draw = r_orb * scale

        screen_cx = W / 2.0
        cy = float(self.pill_top + self.pill_h // 2)
        # Island spans the two wings plus the orb; centred on the screen, so the
        # orb's own centre drifts as the wings grow unevenly.
        island_w = left + right + self.orb_d
        x0 = screen_cx - island_w / 2.0
        cx = x0 + left + r_orb
        bar_h = float(self.pill_h)
        top = cy - bar_h / 2.0
        bot = cy + bar_h / 2.0

        if appear <= 0.01:
            return base

        # ---- the bar (only where a wing is actually open) ----------------
        radius = int(bar_h / 2)
        if left > 1.0 or right > 1.0:
            mask, edge, bloom_edge = _bar_geometry(W, H, x0, island_w, top, bot,
                                                   radius)
            grad = Image.new("RGB", (1, int(bar_h)))
            gp = grad.load()
            for y in range(int(bar_h)):
                t = y / max(1, bar_h - 1)
                if t < .32:
                    gp[0, y] = _lerp(Theme.bg_top, Theme.bg_bottom, t / .32)
                else:
                    gp[0, y] = _lerp(Theme.bg_bottom, (30, 43, 61),
                                     ((t - .32) / .68) ** 3)
            grad = grad.resize((W, int(bar_h)))
            body = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            alpha = int(PILL_ALPHA * appear)
            body.paste(grad, (0, int(top)),
                       mask.crop((0, int(top), W, int(top) + int(bar_h)))
                       .point(lambda v: (v * alpha) // 255))
            base.alpha_composite(body)

            # A single soft hairline all the way round - frosted glass, not a
            # painted button. Only the error state tints it, because that is
            # the one status worth seeing from the corner of your eye.
            edge_rgb = Theme.danger if st.state == "error" else Theme.border
            line = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            # A real glass bevel catches the light from above: the hairline is
            # brighter along the top edge and dimmer underneath. A single flat
            # alpha all the way round is what made the panel read as an outlined
            # button rather than as frosted glass.
            if st.state == "error":
                a_top, a_bot = 190, 130
            else:
                a_top, a_bot = 196, 104
            line.paste(edge_rgb + (int(a_bot * appear),), (0, 0), edge)
            top_half = edge.crop((0, int(top), W, int(cy)))
            line.paste(edge_rgb + (int(a_top * appear),), (0, int(top)), top_half)
            base.alpha_composite(line)
            if st.state == "error":
                # A faint inward bloom so the whole panel reads as alarmed.
                bloom = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                bloom.paste(Theme.danger + (int(46 * appear),), (0, 0), bloom_edge)
                base.alpha_composite(bloom.filter(ImageFilter.GaussianBlur(3)))

        # ---- halo + ripple, behind the orb -------------------------------
        base.alpha_composite(_build_glow(W, H, int(cx), int(cy), self.orb_d, orb_rgb))
        if self._ripple > 0.001 and not self.reduced_motion:
            t = 1.0 - self._ripple                      # 0 at the peak -> 1 gone
            rr = r_draw * (1.0 + 0.85 * t)
            a = int(150 * self._ripple * appear)
            if a > 2:
                ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                ImageDraw.Draw(ring).ellipse(
                    [cx - rr, cy - rr, cx + rr, cy + rr],
                    outline=orb_rgb + (a,), width=2)
                base.alpha_composite(ring)

        # ---- the wings ----------------------------------------------------
        if left > 8.0:
            self._draw_left_wing(base, d, st, x0, cx - r_orb, cy, left, appear)
        self._action_rects = []
        if right > 8.0:
            if self._is_idle(st):
                # At rest the right wing is the wordmark, and the orb sits at
                # the island's left edge because the left wing is closed.
                self._draw_brand(d, cx + r_orb, x0 + island_w, cy, st, appear)
            else:
                self._draw_right_wing(base, d, st, cx + r_orb, x0 + island_w, cy,
                                      right, appear, label_rgb)

        # ---- the orb, last, so it sits proud of the bar -------------------
        self._draw_orb(base, cx, cy, orb_rgb, r_draw, level, phase, mode, appear)
        return base

    # --------------------------------------------------------------- wings
    def _draw_left_wing(self, base, d, st, x_start: float, x_end: float,
                        cy: float, width: float, appear: float) -> None:
        """The left wing carries ONE of three things, never a mixture:

          * a named job with a progress bar  ("Opening Quarterly Report")
          * a headline with a sub-line       ("Error / Something went wrong")
          * what the user said, quoted, on one line

        Which one is chosen here must match what `_wing_targets` measured, or
        the wing is sized for one layout and painted with another.
        """
        x = x_start + PAD
        right_edge = x_end - 12
        if right_edge - x < 40:
            return
        # Fade in only once the wing is wide enough to hold the text, so it
        # never spills past the rounded end while the spring is still opening.
        openness = max(0.0, min(1.0, (width - 70) / 90.0))
        a = int(240 * appear * openness)
        if a < 6:
            return

        if st.task:
            self._draw_doc_glyph(d, int(x), int(cy - 1), a)
            tx = x + ICON_W + 12
            f = _font(15)
            self._text(d, (tx, cy - 9),
                       self._elide_to_width(st.task, f, d, right_edge - tx),
                       f, Theme.text + (a,))
            if st.progress >= 0.0:
                self._draw_progress(d, tx, cy + 12, right_edge - tx,
                                    st.progress, a)
            return

        if st.state == "error" and not st.transcript:
            self._draw_warning_glyph(d, int(x), int(cy), a)
            tx = x + ICON_W + 12
            f = _font(15, bold=True)
            self._text(d, (tx, cy - 10), "Error", f, Theme.text + (a,))
            sub = st.detail or "Something went wrong"
            f2 = _font(12)
            self._text(d, (tx, cy + 10),
                       self._elide_to_width(sub, f2, d, right_edge - tx),
                       f2, Theme.muted + (int(a * 0.9),))
            return

        text = st.transcript or st.hint
        if not text:
            return
        f = _font(16)
        body = self._elide_to_width(text, f, d, right_edge - x - 18)
        if st.transcript:
            body = "\u201c" + body + "\u201d"
        self._text(d, (x, cy), body, f, Theme.text + (a,))

    def _draw_right_wing(self, base, d, st, x_start: float, x_end: float,
                         cy: float, width: float, appear: float,
                         label_rgb: tuple) -> None:
        """Status on the right, behind a hairline divider.

        Column A is the state and its sub-line. Column B is EITHER a live level
        meter (with the clock and spend under it) or the ESC keycap - never
        both, because only one of them is ever the useful thing to look at.
        """
        openness = max(0.0, min(1.0, (width - 60) / 90.0))
        a = int(250 * appear * openness)
        if a < 6:
            return

        # The hairline separating the orb from the status. It sits a clear
        # DIVIDER_W out from the orb's edge, because at +5 px the orb's own
        # halo washed it out completely.
        dx = x_start + DIVIDER_W
        d.line([dx, cy - 14, dx, cy + 14],
               fill=Theme.border + (int(120 * appear),), width=1)

        x = x_start + DIVIDER_W + PAD
        right_edge = x_end - PAD
        if right_edge - x < 40:
            return

        # ---- column B first: it is right-aligned, so column A gets what is left
        col_b_w = self._col_b_width(st, d)
        col_b_x = right_edge - col_b_w if col_b_w else right_edge
        a_edge = (col_b_x - COL_GAP) if col_b_w else right_edge

        # ---- column A: the status dot, the state, the sub-line -----------
        if self._shows_status(st):
            dot_r = 4
            two_rows = bool(st.detail)
            row1 = cy - 10 if two_rows else cy
            d.ellipse([x, row1 - dot_r, x + dot_r * 2, row1 + dot_r],
                      fill=label_rgb + (a,))
            tx = x + DOT_W
            f_status = _font(16)
            self._text(d, (tx, row1),
                       self._elide_to_width(self._status_label(st), f_status, d,
                                            a_edge - tx),
                       f_status, Theme.text + (a,))
            if two_rows:
                f_sub = _font(12)
                self._text(d, (tx, cy + 11),
                           self._elide_to_width(st.detail, f_sub, d, a_edge - tx),
                           f_sub, Theme.muted + (int(a * 0.88),))

        # ---- column B ----------------------------------------------------
        self._action_rects = []
        if not col_b_w:
            return

        chips = self._chip_labels(st)
        if chips:
            cursor = col_b_x
            for label in chips:
                w = self._text_width(d, label, _font(11)) + 22
                y0 = int(cy - 11)
                d.rounded_rectangle([cursor, y0, cursor + w, y0 + 22], radius=7,
                                    fill=(255, 255, 255, int(24 * appear)),
                                    outline=Theme.border_dim + (int(200 * appear),),
                                    width=1)
                self._text(d, (cursor + 11, cy), label, _font(11),
                           Theme.text + (a,))
                self._action_rects.append(
                    (int(cursor), y0, int(cursor + w), y0 + 22, label))
                cursor += w + 8
            return

        if self._shows_meter(st):
            self._draw_meter(d, col_b_x, cy - 8, METER_W, label_rgb, appear)
            cost = self._cost_line(st)
            if cost:
                self._text(d, (col_b_x, cy + 14), cost, _font(10),
                           Theme.dim + (int(a * 0.85),))
            return

        cap, rest = self._split_hint(st.esc_hint)
        cap_w = self._text_width(d, cap, _font(10, bold=True)) + 20
        y0 = int(cy - 10)
        d.rounded_rectangle([col_b_x, y0, col_b_x + cap_w, y0 + 20], radius=6,
                            fill=(255, 255, 255, int(20 * appear)),
                            outline=Theme.border_dim + (int(190 * appear),),
                            width=1)
        self._text(d, (col_b_x + 10, cy), cap, _font(10, bold=True),
                   Theme.text + (a,))
        self._action_rects.append(
            (int(col_b_x), y0, int(col_b_x + cap_w), y0 + 20, st.esc_hint))
        if rest:
            self._text(d, (col_b_x + cap_w + 8, cy), rest, _font(12),
                       Theme.muted + (int(a * 0.9),))

    def _draw_brand(self, d, x_start: float, x_end: float, cy: float,
                    st, appear: float) -> None:
        """The resting island: the wordmark and one line of nudge."""
        a = int(240 * appear)
        if a < 6:
            return
        x = x_start + DIVIDER_W + PAD - 8
        self._text(d, (x, cy - 12), _spaced(BRAND), _font(11, bold=True),
                   Theme.muted + (int(a * 0.95),))
        hint = st.hint or BRAND_TAGLINE
        f = _font(14)
        self._text(d, (x, cy + 10),
                   self._elide_to_width(hint, f, d, (x_end - PAD) - x),
                   f, Theme.text + (int(a * 0.92),))

    # ------------------------------------------------------------------ glyphs
    @staticmethod
    def _draw_doc_glyph(d, x: int, cy: int, a: int) -> None:
        """A document outline: the leading icon on a running job."""
        w, h = 14, 17
        y0 = cy - h // 2
        col = Theme.accent2 + (a,)
        d.rounded_rectangle([x, y0, x + w, y0 + h], radius=3, outline=col, width=2)
        for i, dy in enumerate((5, 9, 13)):
            d.line([x + 4, y0 + dy, x + w - (4 if i == 2 else 3), y0 + dy],
                   fill=Theme.accent2 + (int(a * 0.7),), width=1)

    @staticmethod
    def _draw_warning_glyph(d, x: int, cy: int, a: int) -> None:
        """A warning triangle with a bang, for the error state."""
        w, h = 18, 16
        y0 = cy - h // 2
        col = Theme.danger + (a,)
        d.polygon([(x + w / 2, y0), (x + w, y0 + h), (x, y0 + h)],
                  outline=col)
        # PIL's polygon outline is 1 px; trace it again for a 2 px stroke.
        d.line([(x + w / 2, y0), (x + w, y0 + h), (x, y0 + h),
                (x + w / 2, y0)], fill=col, width=2, joint="curve")
        d.line([x + w / 2, y0 + 6, x + w / 2, y0 + 10], fill=col, width=2)
        d.ellipse([x + w / 2 - 1, y0 + 12, x + w / 2 + 1, y0 + 14], fill=col)

    @staticmethod
    def _draw_progress(d, x: float, y: float, width: float,
                       value: float, a: int) -> None:
        """A determinate progress bar under a running job's name."""
        width = max(24.0, width)
        h = 4
        d.rounded_rectangle([x, y, x + width, y + h], radius=2,
                            fill=(255, 255, 255, int(a * 0.10)))
        filled = width * max(0.0, min(1.0, value))
        if filled >= 2:
            d.rounded_rectangle([x, y, x + filled, y + h], radius=2,
                                fill=Theme.accent + (a,))

    def _draw_meter(self, d, x: float, y: float, width: float,
                    colour: tuple, appear: float) -> None:
        """A live level meter: thin bars driven by the real audio level.

        Deliberately not the same shape as the orb's waveform - the orb says
        "something is happening", this says "this much sound, right now".
        """
        bars = 28
        step = width / bars
        a = int(230 * appear)
        for i in range(bars):
            # A travelling envelope so it reads as motion, scaled by the level.
            t = self._phase * 5.0 - i * 0.42
            envelope = math.exp(-3.6 * ((i / (bars - 1) - .63) / .65) ** 2)
            env = envelope * (0.18 + 0.82 * abs(math.sin(t)))
            drive = 0.22 + 1.5 * self._level_smooth
            h = max(2.0, min(22.0, 22.0 * env * drive))
            bx = x + i * step
            d.rounded_rectangle([bx, y - h / 2, bx + max(1.4, step - 2.4), y + h / 2],
                                radius=1, fill=colour + (a,))

    @staticmethod
    def _draw_brand_mark(d, x: int, cy: int, size: int) -> None:
        """A small neutral ring emblem.

        Deliberately NOT a reproduction of the OpenAI wordmark that appears in
        mockup.png: shipping someone else's trademark inside the product is
        the user's decision, not this app's. Kept for the settings header; the
        island itself is too compact to carry a badge.
        """
        r = size // 2
        y0, y1 = cy - r, cy + r
        d.ellipse([x, y0, x + size, y1], outline=Theme.text + (232,), width=2)
        d.arc([x + 4, y0 + 4, x + size - 4, y1 - 4], start=200, end=20,
              fill=Theme.text + (200,), width=2)
        d.ellipse([x + r - 2, cy - 2, x + r + 2, cy + 2], fill=Theme.text + (215,))

    @staticmethod
    def _draw_monitor_glyph(d, x: int, cy: int) -> None:
        """The little screen icon on an action chip."""
        w, h = 11, 8
        d.rounded_rectangle([x, cy - h // 2 - 1, x + w, cy + h // 2 - 1], radius=2,
                            outline=Theme.teal + (232,), width=1)
        d.line([x + 3, cy + h // 2 + 2, x + w - 3, cy + h // 2 + 2],
               fill=Theme.teal + (232,), width=1)

    def _draw_orb(self, base: Image.Image, cx: float, cy: float,
                  orb_rgb: tuple, r_draw: float, level: float,
                  phase: float, mode: str, appear: float = 1.0) -> None:
        """The glass orb: rim, globe, and a waveform that reacts to real audio."""
        d = ImageDraw.Draw(base)
        # Breathe, and squash slightly on a loud peak - the orb has to feel like
        # it is listening, not like a static badge.
        if self.reduced_motion:
            pulse = 1.0
        else:
            pulse = (1.0 + 0.045 * math.sin(phase * 2.0)
                     + 0.10 * min(1.0, level * 1.8))
        r_out = r_draw * pulse
        globe = int(self.globe * (r_out / (self.orb_d / 2.0)))
        if globe < 8:
            return

        ring = Image.new("RGBA", base.size, (0, 0, 0, 0))
        rd = ImageDraw.Draw(ring)
        rd.ellipse([cx - r_out, cy - r_out, cx + r_out, cy + r_out],
                   outline=orb_rgb + (int(65 * appear),), width=1)
        base.alpha_composite(ring)
        base.alpha_composite(_build_sphere(globe, orb_rgb),
                             (int(cx - globe / 2), int(cy - globe / 2)))

        bars = 7
        span = int(globe * 0.66)
        step = max(3, span // bars)
        t = phase * 2.6
        for i in range(bars):
            dist = abs(i - (bars - 1) / 2) / ((bars - 1) / 2)
            envelope = math.exp(-2.4 * dist * dist)
            if mode == "bars":            # the user is talking: follow the mic
                drive = 0.26 + 1.25 * level
                wobble = 0.80 + 0.20 * math.sin(t + i * 0.8)
            elif mode == "speak":         # the assistant is talking
                drive = 0.34 + 0.50 * (0.5 + 0.5 * math.sin(t * 1.8 - i * 0.9))
                wobble = 1.0
            elif mode == "spin":          # thinking / working
                drive = 0.30 + 0.42 * (0.5 + 0.5 * math.sin(t * 1.1 + i * 0.6))
                wobble = 1.0
            elif mode == "pulse":
                drive = 0.24 + 0.26 * (0.5 + 0.5 * math.sin(t * 1.5))
                wobble = 1.0
            else:
                drive, wobble = 0.20, 1.0
            h = max(3, int(globe * 0.46 * drive * envelope * wobble))
            x = cx - (bars - 1) * step / 2.0 + i * step
            if mode == "still":
                h = 3
            d.rounded_rectangle([x - 1.0, cy - h / 2.0, x + 1.0, cy + h / 2.0],
                                radius=2, fill=(226, 250, 255, int(246 * appear)))

    # ------------------------------------------------------------------ text
    @staticmethod
    def _text(draw, xy, text: str, font, fill) -> None:
        """Draw text vertically CENTRED on xy[1].

        The whole pill layout is expressed as offsets from the pill's centre
        line, which only works if text is placed by its middle rather than by
        its ascender. `anchor=` needs a TrueType font, so the bitmap fallback
        (no Segoe UI, no Arial) degrades to approximate centring instead of
        raising.
        """
        x, y = int(xy[0]), int(xy[1])
        try:
            draw.text((x, y), text, font=font, fill=fill, anchor="lm")
        except (ValueError, AttributeError, TypeError):
            size = getattr(font, "size", 16)
            draw.text((x, y - int(size * 0.62)), text, font=font, fill=fill)

    @staticmethod
    def _text_width(draw, text: str, font) -> int:
        try:
            box = draw.textbbox((0, 0), text, font=font)
            return int(box[2] - box[0])
        except Exception:
            return int(len(text) * 8)

    @classmethod
    def _wrap(cls, text: str, font, max_width: int, draw=None,
              max_lines: int = 2) -> list[str]:
        """Greedy word wrap so text can never run underneath the orb."""
        text = (text or "").replace("\n", " ").strip()
        if not text:
            return []
        measure = (lambda s: cls._text_width(draw, s, font)) if draw is not None else \
            (lambda s: len(s) * 9)
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and measure(candidate) > max_width:
                lines.append(current)
                current = word
                if len(lines) == max_lines:
                    break
            else:
                current = candidate
        if len(lines) < max_lines and current:
            lines.append(current)
        if len(lines) == max_lines:
            # Mark truncation if there is more text than fits.
            consumed = sum(len(l.split()) for l in lines)
            if consumed < len(words):
                lines[-1] = lines[-1].rstrip(" ,;:") + "…"
        return lines[:max_lines]

    @classmethod
    def _elide_to_width(cls, text: str, font, draw, max_width: int) -> str:
        text = (text or "").replace("\n", " ").strip()
        if not text or max_width <= 0:
            return text
        if cls._text_width(draw, text, font) <= max_width:
            return text
        for cut in range(len(text), 0, -1):
            candidate = text[:cut].rstrip() + "…"
            if cls._text_width(draw, candidate, font) <= max_width:
                return candidate
        return "…"

    @staticmethod
    def _elide(text: str, max_chars: int) -> str:
        text = (text or "").replace("\n", " ").strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 1].rstrip() + "…"

    @staticmethod
    def _clock(seconds: float) -> str:
        seconds = max(0, int(seconds))
        return f"{seconds // 60}:{seconds % 60:02d}"

    def _update_window(self) -> None:
        if not self._hwnd or not self._canvas:
            return
        size = SIZE(self.width, self.height)
        src = POINT(0, 0)
        dst = POINT()
        rect = RECT()
        user32.GetWindowRect(wt.HWND(self._hwnd), ctypes.byref(rect))
        dst.x, dst.y = rect.left, rect.top
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        ok = user32.UpdateLayeredWindow(
            wt.HWND(self._hwnd), self._canvas.screen_dc, ctypes.byref(dst),
            ctypes.byref(size), self._canvas.mem_dc, ctypes.byref(src), 0,
            ctypes.byref(blend), ULW_ALPHA)
        if not ok:
            err = ctypes.get_last_error()
            if err not in (0,):
                log.debug("UpdateLayeredWindow failed: %s", err)


user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wt.BOOL
