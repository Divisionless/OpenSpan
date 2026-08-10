"""Hold a modifier and scroll to magnify the Windows desktop.

The gesture and zoom arithmetic are pure.  Native magnification and the
``WH_MOUSE_LL`` hook are explicit lifetime objects: importing this module and
constructing any class install nothing.  ``ScreenZoomModule.start`` is the sole
feature boundary that can start the mouse hook.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import dataclasses
import enum
import logging
import math
import os
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from keyboard_interception import (
    ChordModifiers,
    KeyboardInterceptionService,
    KeyboardRoutingVerdict,
    RawKeyboardEvent,
)
from monitor_identity import MonitorIdentity, attached_identities
from settings_service import FeatureDeclaration, SettingsService


FEATURE_ID = "accessibility-zoom"
CONSUMER_ID = "OpenSpan.ZoomModifierObserver"
MODIFIER_SETTING = "gestureModifier"
SCROLL_STEP_SETTING = "scrollStepFactor"

FEATURE_DECLARATION = FeatureDeclaration(
    FEATURE_ID,
    "Hold a modifier and scroll to zoom the screen",
    "Features",
    False,
    {},
    {MODIFIER_SETTING: "Alt", SCROLL_STEP_SETTING: 1.25},
    ("WH_MOUSE_LL mouse-wheel hook; modifier observation through the central "
     "KeyboardInterceptionService",),
)


class ZoomVerdict(enum.Enum):
    PASS_THROUGH = enum.auto()
    SWALLOW_AND_ZOOM = enum.auto()


def modifier_name(modifier: ChordModifiers) -> str:
    if modifier is ChordModifiers.ALT:
        return "Alt"
    if modifier is ChordModifiers.SHIFT:
        return "Shift"
    if modifier is ChordModifiers.WIN:
        return "Win"
    return "Ctrl"


class ZoomGesture:
    """Pure decision core translated from ``ZoomGesture.cs``."""

    def __init__(self, modifier: ChordModifiers = ChordModifiers.ALT) -> None:
        self.modifier = modifier
        self.enabled = True
        self._held: set[str] = set()
        self._injected_moves = 0
        self._last_real_move_ms = 0
        self._last_injected_move_ms = 0

    def on_key(self, canonical_key: str, is_down: bool) -> None:
        if canonical_key not in {"Ctrl", "Alt", "Shift", "Win"}:
            return
        if is_down:
            self._held.add(canonical_key)
        else:
            self._held.discard(canonical_key)

    def reset(self) -> None:
        self._held.clear()
        self._injected_moves = 0

    def on_pointer_move(self, is_injected: bool, now_ms: int) -> None:
        if is_injected:
            if now_ms - self._last_injected_move_ms > 500:
                self._injected_moves = 0
            self._last_injected_move_ms = now_ms
            self._injected_moves = min(1000, self._injected_moves + 1)
        else:
            self._last_real_move_ms = now_ms
            self._injected_moves = 0

    def pointer_driven_elsewhere(self, now_ms: int) -> bool:
        return (
            self._injected_moves >= 4
            and now_ms - self._last_injected_move_ms < 500
            and now_ms - self._last_real_move_ms > 250
        )

    def sync_held(self, is_physically_down: Callable[[str], bool]) -> None:
        required = modifier_name(self.modifier)
        if is_physically_down(required):
            self._held.clear()
            self._held.add(required)
        else:
            self._held.discard(required)

    @property
    def is_armed(self) -> bool:
        if not self.enabled:
            return False
        required = modifier_name(self.modifier)
        return len(self._held) == 1 and required in self._held

    def on_wheel(
            self, notches: int, is_injected: bool,
            now_ms: int = 0) -> ZoomVerdict:
        if is_injected or notches == 0:
            return ZoomVerdict.PASS_THROUGH
        if self.pointer_driven_elsewhere(now_ms):
            return ZoomVerdict.PASS_THROUGH
        return (ZoomVerdict.SWALLOW_AND_ZOOM if self.is_armed
                else ZoomVerdict.PASS_THROUGH)

    @staticmethod
    def parse_modifier(value: str | None) -> ChordModifiers:
        normalized = "" if value is None else value.strip().lower()
        if normalized in {"ctrl", "control"}:
            return ChordModifiers.CTRL
        if normalized == "shift":
            return ChordModifiers.SHIFT
        if normalized in {"win", "windows", "super", "cmd", "command"}:
            return ChordModifiers.WIN
        return ChordModifiers.ALT


MIN_LEVEL = 1.0
MAX_LEVEL = 12.0
DEFAULT_STEP_FACTOR = 1.25
RAMP_TIME_CONSTANT_MS = 55.0
RAMP_SETTLED = 0.005


def clamp_level(level: float) -> float:
    if math.isnan(level):
        return MIN_LEVEL
    return min(MAX_LEVEL, max(MIN_LEVEL, level))


def step_level(
        level: float, notches: float,
        step_factor: float = DEFAULT_STEP_FACTOR) -> float:
    if notches == 0:
        return clamp_level(level)
    if step_factor <= 1.0:
        step_factor = DEFAULT_STEP_FACTOR
    return clamp_level(level * math.pow(step_factor, notches))


@dataclasses.dataclass(frozen=True, slots=True)
class ScreenRect:
    x: int
    y: int
    width: int
    height: int

    @property
    def is_empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    def contains(self, x: int, y: int) -> bool:
        return (self.x <= x < self.x + self.width
                and self.y <= y < self.y + self.height)


def viewport_offset(
        screen: ScreenRect, anchor: tuple[int, int],
        level: float) -> tuple[int, int]:
    level = clamp_level(level)
    if level <= MIN_LEVEL or screen.is_empty:
        return screen.x, screen.y
    view_width = max(1, round(screen.width / level))
    view_height = max(1, round(screen.height / level))
    anchor_x, anchor_y = anchor
    relative_x = ((anchor_x - screen.x) / screen.width
                  if screen.width else 0.5)
    relative_y = ((anchor_y - screen.y) / screen.height
                  if screen.height else 0.5)
    offset_x = round(anchor_x - relative_x * view_width)
    offset_y = round(anchor_y - relative_y * view_height)
    offset_x = min(screen.x + screen.width - view_width,
                   max(screen.x, offset_x))
    offset_y = min(screen.y + screen.height - view_height,
                   max(screen.y, offset_y))
    return offset_x, offset_y


def viewport(
        screen: ScreenRect, offset_x: float, offset_y: float,
        level: float) -> ScreenRect:
    """Round a fractional viewport only at the Windows API boundary."""
    level = clamp_level(level)
    return ScreenRect(
        round(offset_x), round(offset_y),
        max(1, round(screen.width / level)),
        max(1, round(screen.height / level)),
    )


def _clamp_offset(
        screen: ScreenRect, offset_x: float, offset_y: float,
        level: float) -> tuple[float, float]:
    level = clamp_level(level)
    if screen.is_empty:
        return offset_x, offset_y
    view_width = max(1, round(screen.width / level))
    view_height = max(1, round(screen.height / level))
    return (
        min(screen.x + screen.width - view_width,
            max(screen.x, offset_x)),
        min(screen.y + screen.height - view_height,
            max(screen.y, offset_y)),
    )


def rescale_about(
        screen: ScreenRect, anchor: tuple[int, int],
        offset_x: float, offset_y: float,
        from_level: float, to_level: float) -> tuple[float, float]:
    """Preserve an anchor pixel while carrying fractional viewport state."""
    from_level = clamp_level(from_level)
    to_level = clamp_level(to_level)
    if screen.is_empty:
        return offset_x, offset_y
    ratio = from_level / to_level
    x = anchor[0] - (anchor[0] - offset_x) * ratio
    y = anchor[1] - (anchor[1] - offset_y) * ratio
    return _clamp_offset(screen, x, y, to_level)


def edge_push(
        screen: ScreenRect, pointer: tuple[int, int],
        offset_x: float, offset_y: float,
        level: float) -> tuple[float, float]:
    """Move by exactly the pointer's overshoot, and only outside the view."""
    level = clamp_level(level)
    if screen.is_empty or level <= MIN_LEVEL:
        return offset_x, offset_y
    view_width = max(1, round(screen.width / level))
    view_height = max(1, round(screen.height / level))
    pointer_x, pointer_y = pointer
    if pointer_x < offset_x:
        offset_x = float(pointer_x)
    elif pointer_x > offset_x + view_width - 1:
        offset_x = float(pointer_x - view_width + 1)
    if pointer_y < offset_y:
        offset_y = float(pointer_y)
    elif pointer_y > offset_y + view_height - 1:
        offset_y = float(pointer_y - view_height + 1)
    return _clamp_offset(screen, offset_x, offset_y, level)


@dataclasses.dataclass(frozen=True, slots=True)
class MagnifierTransform:
    v00: float
    v01: float
    v02: float
    v10: float
    v11: float
    v12: float
    v20: float
    v21: float
    v22: float


def window_transform(
        monitor: ScreenRect, view_x: float, view_y: float,
        level: float) -> MagnifierTransform:
    """Map desktop source coordinates into the target monitor's desktop space."""
    level = clamp_level(level)
    return MagnifierTransform(
        level, 0.0, monitor.x - view_x * level,
        0.0, level, monitor.y - view_y * level,
        0.0, 0.0, 1.0,
    )


def ease_level(current: float, target: float, elapsed_ms: float) -> float:
    """Advance a time-based zoom ramp in perceptually uniform log space."""
    from_log = math.log(clamp_level(current))
    to_log = math.log(clamp_level(target))
    if abs(to_log - from_log) < RAMP_SETTLED:
        return clamp_level(target)
    closed = 1.0 - math.exp(
        -max(elapsed_ms, 1.0) / RAMP_TIME_CONSTANT_MS)
    return clamp_level(math.exp(from_log + (to_log - from_log) * closed))


def fullscreen_offsets(
        screen: ScreenRect, view: ScreenRect, level: float) -> tuple[int, int]:
    """Translate a virtual-desktop viewport to fullscreen API offsets.

    MagSetFullscreenTransform measures its unmagnified offsets from the
    PRIMARY monitor's top-left, not the virtual desktop's top-left.  The SDK
    therefore compensates a negative virtual origin by origin / level.
    Python's int(), like C#'s int cast, truncates that division toward zero.
    """
    level = clamp_level(level)
    return (
        view.x - int(screen.x / level),
        view.y - int(screen.y / level),
    )


def screen_for_point(
        monitors: Sequence[MonitorIdentity], x: int, y: int) -> ScreenRect | None:
    """Use landed monitor identities to find the display beneath a pointer."""
    for monitor in monitors:
        screen = ScreenRect(
            monitor.virtual_x, monitor.virtual_y,
            monitor.native_width, monitor.native_height)
        if screen.contains(x, y):
            return screen
    return None


class ZoomModifierObserver:
    """Priority-zero keyboard observer.  Its verdict is always pass-through."""

    consumer_id = CONSUMER_ID
    priority = 0

    _VK_TO_MODIFIER = {
        0x11: "Ctrl", 0xA2: "Ctrl", 0xA3: "Ctrl",
        0x12: "Alt", 0xA4: "Alt", 0xA5: "Alt",
        0x10: "Shift", 0xA0: "Shift", 0xA1: "Shift",
        0x5B: "Win", 0x5C: "Win",
    }

    def __init__(self, gesture: ZoomGesture) -> None:
        self.gesture = gesture

    def process_key_event(
            self, event: RawKeyboardEvent,
            current_modifiers: ChordModifiers) -> KeyboardRoutingVerdict:
        del current_modifiers
        key = self._VK_TO_MODIFIER.get(event.vk_code)
        if key is not None:
            self.gesture.on_key(key, event.is_down)
        return KeyboardRoutingVerdict.pass_through()


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


class _MAGTRANSFORM(ctypes.Structure):
    _fields_ = [(name, ctypes.c_float) for name in (
        "v00", "v01", "v02", "v10", "v11", "v12",
        "v20", "v21", "v22")]


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT), ("style", wt.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
        ("hCursor", ctypes.c_void_p), ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR),
        ("hIconSm", wt.HICON),
    ]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("rcMonitor", _RECT),
        ("rcWork", _RECT), ("dwFlags", wt.DWORD),
        ("szDevice", wt.WCHAR * 32),
    ]


class _CursorVisibility:
    """Track cursor ownership only after MagShowSystemCursor succeeds."""

    def __init__(self, show: Callable[[bool], bool],
                 logger: logging.Logger) -> None:
        self._show = show
        self._logger = logger
        self.hidden = False

    def restore_at_startup(self) -> bool:
        restored = bool(self._show(True))
        if restored:
            self.hidden = False
        else:
            # Unknown session state is treated as hidden so the next tick retries.
            self.hidden = True
            self._logger.warning("Could not restore the system cursor at startup.")
        return restored

    def set_hidden(self, hidden: bool) -> bool:
        if hidden == self.hidden:
            return True
        changed = bool(self._show(not hidden))
        if changed:
            self.hidden = hidden
        else:
            self._logger.warning(
                "Could not %s the system cursor; the next frame will retry.",
                "hide" if hidden else "restore")
        return changed


class _MagnificationBindings:
    """The native surface used only by the dedicated magnifier STA thread."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("The Windows Magnification API is available only on Windows.")
        self.dll = ctypes.WinDLL("Magnification.dll", use_last_error=True)
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self.winmm = ctypes.WinDLL("winmm", use_last_error=True)
        self.ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        self.wnd_proc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

        self.dll.MagInitialize.restype = wt.BOOL
        self.dll.MagUninitialize.restype = wt.BOOL
        self.dll.MagSetWindowSource.restype = wt.BOOL
        self.dll.MagSetWindowSource.argtypes = [wt.HWND, _RECT]
        self.dll.MagSetWindowTransform.restype = wt.BOOL
        self.dll.MagSetWindowTransform.argtypes = [
            wt.HWND, ctypes.POINTER(_MAGTRANSFORM)]
        self.dll.MagSetWindowFilterList.restype = wt.BOOL
        self.dll.MagSetWindowFilterList.argtypes = [
            wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.POINTER(wt.HWND)]
        self.dll.MagSetFullscreenTransform.restype = wt.BOOL
        self.dll.MagSetFullscreenTransform.argtypes = [
            ctypes.c_float, ctypes.c_int, ctypes.c_int]
        self.dll.MagShowSystemCursor.restype = wt.BOOL
        self.dll.MagShowSystemCursor.argtypes = [wt.BOOL]

        self.user32.RegisterClassExW.restype = wt.ATOM
        self.user32.RegisterClassExW.argtypes = [ctypes.POINTER(_WNDCLASSEXW)]
        self.user32.CreateWindowExW.restype = wt.HWND
        self.user32.CreateWindowExW.argtypes = [
            wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
        self.user32.DefWindowProcW.restype = ctypes.c_ssize_t
        self.user32.DefWindowProcW.argtypes = [
            wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        self.user32.DestroyWindow.restype = wt.BOOL
        self.user32.DestroyWindow.argtypes = [wt.HWND]
        self.user32.ShowWindow.restype = wt.BOOL
        self.user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
        self.user32.SetLayeredWindowAttributes.restype = wt.BOOL
        self.user32.SetLayeredWindowAttributes.argtypes = [
            wt.HWND, wt.COLORREF, wt.BYTE, wt.DWORD]
        self.user32.SetWindowPos.restype = wt.BOOL
        self.user32.SetWindowPos.argtypes = [
            wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wt.UINT]
        self.user32.GetMessageW.restype = ctypes.c_int
        self.user32.GetMessageW.argtypes = [
            ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
        self.user32.TranslateMessage.restype = wt.BOOL
        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
        self.user32.DispatchMessageW.restype = ctypes.c_ssize_t
        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
        self.user32.PostMessageW.restype = wt.BOOL
        self.user32.PostMessageW.argtypes = [
            wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        self.user32.PostThreadMessageW.restype = wt.BOOL
        self.user32.PostThreadMessageW.argtypes = [
            wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
        self.user32.SendMessageW.restype = ctypes.c_ssize_t
        self.user32.SendMessageW.argtypes = [
            wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        self.user32.SetTimer.restype = ctypes.c_size_t
        self.user32.SetTimer.argtypes = [
            wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]
        self.user32.KillTimer.restype = wt.BOOL
        self.user32.KillTimer.argtypes = [wt.HWND, ctypes.c_size_t]
        self.user32.GetCursorPos.restype = wt.BOOL
        self.user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
        self.user32.MonitorFromPoint.restype = wt.HMONITOR
        self.user32.MonitorFromPoint.argtypes = [wt.POINT, wt.DWORD]
        self.user32.GetMonitorInfoW.restype = wt.BOOL
        self.user32.GetMonitorInfoW.argtypes = [
            wt.HMONITOR, ctypes.POINTER(_MONITORINFOEXW)]
        self.kernel32.GetCurrentThreadId.restype = wt.DWORD
        self.kernel32.GetCurrentThreadId.argtypes = []
        self.kernel32.GetModuleHandleW.restype = wt.HMODULE
        self.kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
        self.gdi32.CreateDCW.restype = wt.HDC
        self.gdi32.CreateDCW.argtypes = [
            wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p]
        self.gdi32.GetDeviceCaps.restype = ctypes.c_int
        self.gdi32.GetDeviceCaps.argtypes = [wt.HDC, ctypes.c_int]
        self.gdi32.DeleteDC.restype = wt.BOOL
        self.gdi32.DeleteDC.argtypes = [wt.HDC]
        self.winmm.timeBeginPeriod.restype = wt.UINT
        self.winmm.timeBeginPeriod.argtypes = [wt.UINT]
        self.winmm.timeEndPeriod.restype = wt.UINT
        self.winmm.timeEndPeriod.argtypes = [wt.UINT]
        self.ole32.CoInitializeEx.restype = ctypes.c_long
        self.ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wt.DWORD]
        self.ole32.CoUninitialize.argtypes = []


@dataclasses.dataclass(slots=True)
class _LoopStats:
    frames: int = 0
    interval_total: float = 0.0
    interval_best: float = math.inf
    interval_worst: float = 0.0
    work_total: float = 0.0
    work_worst: float = 0.0
    late: int = 0

    def add(self, interval_ms: float, work_ms: float) -> None:
        self.frames += 1
        self.interval_total += interval_ms
        self.interval_best = min(self.interval_best, interval_ms)
        self.interval_worst = max(self.interval_worst, interval_ms)
        self.work_total += work_ms
        self.work_worst = max(self.work_worst, work_ms)
        if (self.interval_best != math.inf
                and interval_ms > self.interval_best * 1.5):
            self.late += 1


class ScreenMagnifier:
    """Per-display magnifier owned and pumped by one dedicated STA thread."""

    WS_POPUP = 0x80000000
    WS_VISIBLE = 0x10000000
    WS_CHILD = 0x40000000
    WS_EX_TOPMOST = 0x00000008
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    MS_SHOWMAGNIFIEDCURSOR = 0x0001
    LWA_ALPHA = 0x00000002
    SW_HIDE = 0
    SW_SHOWNA = 8
    SWP_NOACTIVATE = 0x0010
    MW_FILTERMODE_EXCLUDE = 0
    WM_APP_APPLY = 0x8000 + 11
    WM_APP_HIDE = 0x8000 + 12
    WM_APP_RAMP_TICK = 0x8000 + 13
    WM_TIMER = 0x0113
    WM_QUIT = 0x0012
    REFRESH_TIMER_ID = 1
    VREFRESH = 116
    MONITOR_DEFAULTTONEAREST = 2
    FALLBACK_REFRESH_MS = 16

    def __init__(
            self, *, scope: str = "display",
            bindings_factory: Callable[[], Any] | None = None,
            logger: logging.Logger | None = None,
            clock: Callable[[], float] = time.monotonic) -> None:
        if scope not in {"display", "desktop"}:
            raise ValueError("zoom scope must be 'display' or 'desktop'")
        self.scope = scope
        self._bindings_factory = bindings_factory or _MagnificationBindings
        self._logger = logger or logging.getLogger(__name__)
        self._clock = clock
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._initialized = False
        self._unavailable = False
        self._closed = False
        self._startup_error: BaseException | None = None
        self._gate = threading.RLock()
        self._level = MIN_LEVEL
        self._requested_level = MIN_LEVEL
        self._requested_monitor = ScreenRect(0, 0, 0, 0)
        self._requested_anchor = (0, 0)
        self._requested_view: tuple[float, float] | None = None
        self._bindings: Any = None
        self._cursor: _CursorVisibility | None = None
        self._wnd_proc: Any = None
        self._thread_id = 0
        self._host = 0
        self._control = 0
        self._placed_monitor: ScreenRect | None = None
        self._applied_level = MIN_LEVEL
        self._view_x = 0.0
        self._view_y = 0.0
        self._refreshing = False
        self._full_screen = False
        self._high_resolution_timer = False
        self._refresh_period_ms = self.FALLBACK_REFRESH_MS
        self._display_hz = 0
        self._last_render_ms = 0.0
        self._last_tick_ms = 0.0
        self._ramp_stats = _LoopStats()
        self._settled_stats = _LoopStats()

    @property
    def level(self) -> float:
        with self._gate:
            return self._level

    def _ensure_thread(self) -> bool:
        with self._gate:
            if self._closed or self._unavailable:
                return False
            if self._thread is None:
                self._ready.clear()
                self._thread = threading.Thread(
                    target=self._thread_main,
                    name="OpenSpan.DisplayMagnifier", daemon=True)
                self._thread.start()
        if not self._ready.wait(5.0):
            self._unavailable = True
            return False
        return self._initialized and self._startup_error is None

    def probe(self) -> bool:
        try:
            return self._ensure_thread()
        except BaseException as exc:
            self._logger.warning("Screen magnification probe failed: %s", exc)
            self._unavailable = True
            return False

    def apply(self, level: float, screen: ScreenRect,
              anchor: tuple[int, int]) -> bool:
        if not self._ensure_thread():
            return False
        with self._gate:
            self._requested_level = clamp_level(level)
            self._requested_monitor = screen
            self._requested_anchor = anchor
            self._requested_view = None
            self._level = self._requested_level
        return bool(self._bindings.user32.SendMessageW(
            self._host, self.WM_APP_APPLY, 0, 0))

    def apply_viewport(self, level: float, screen: ScreenRect,
                       view: ScreenRect) -> bool:
        """Compatibility seam; production updates use apply and persistent state."""
        if not self._ensure_thread():
            return False
        with self._gate:
            self._requested_level = clamp_level(level)
            self._requested_monitor = screen
            self._requested_anchor = (view.x, view.y)
            self._requested_view = (float(view.x), float(view.y))
            self._level = self._requested_level
        return bool(self._bindings.user32.SendMessageW(
            self._host, self.WM_APP_APPLY, 0, 0))

    def apply_fullscreen(self, level: float, screen: ScreenRect,
                         anchor: tuple[int, int]) -> bool:
        original = self.scope
        self.scope = "desktop"
        try:
            return self.apply(level, screen, anchor)
        finally:
            self.scope = original

    def reset(self) -> bool:
        if not self._ensure_thread():
            return False
        with self._gate:
            self._requested_level = MIN_LEVEL
            self._level = MIN_LEVEL
        return bool(self._bindings.user32.SendMessageW(
            self._host, self.WM_APP_APPLY, 0, 0))

    def _thread_main(self) -> None:
        bindings = None
        com_initialized = False
        try:
            bindings = self._bindings_factory()
            self._bindings = bindings
            self._thread_id = int(bindings.kernel32.GetCurrentThreadId())
            bindings.ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
            com_initialized = True
            if not bindings.dll.MagInitialize():
                raise OSError(ctypes.get_last_error(), "MagInitialize failed")
            self._initialized = True
            self._cursor = _CursorVisibility(
                lambda show: bool(bindings.dll.MagShowSystemCursor(show)),
                self._logger)
            for _ in range(3):
                if self._cursor.restore_at_startup():
                    break
            if not self._create_windows():
                raise OSError(ctypes.get_last_error(),
                              "magnifier windows could not be created")
            self._ready.set()
            message = wt.MSG()
            while bindings.user32.GetMessageW(
                    ctypes.byref(message), None, 0, 0) > 0:
                bindings.user32.TranslateMessage(ctypes.byref(message))
                bindings.user32.DispatchMessageW(ctypes.byref(message))
        except BaseException as exc:
            self._startup_error = exc
            self._unavailable = True
            self._logger.warning("Display magnifier stopped: %s", exc)
        finally:
            self._ready.set()
            if bindings is not None and self._initialized:
                self._cleanup_on_thread()
                bindings.dll.MagUninitialize()
            self._initialized = False
            if bindings is not None and com_initialized:
                bindings.ole32.CoUninitialize()

    def _create_windows(self) -> bool:
        b = self._bindings
        instance = b.kernel32.GetModuleHandleW(None)
        self._wnd_proc = b.wnd_proc_type(self._host_wnd_proc)
        class_name = f"OpenSpan.MagnifierHost.{id(self):x}"
        wnd_class = _WNDCLASSEXW()
        wnd_class.cbSize = ctypes.sizeof(_WNDCLASSEXW)
        wnd_class.lpfnWndProc = ctypes.cast(
            self._wnd_proc, ctypes.c_void_p).value
        wnd_class.hInstance = instance
        wnd_class.lpszClassName = class_name
        if not b.user32.RegisterClassExW(ctypes.byref(wnd_class)):
            return False
        self._host = b.user32.CreateWindowExW(
            self.WS_EX_TOPMOST | self.WS_EX_LAYERED | self.WS_EX_TRANSPARENT
            | self.WS_EX_TOOLWINDOW | self.WS_EX_NOACTIVATE,
            class_name, None, self.WS_POPUP,
            0, 0, 16, 16, None, None, instance, None)
        if not self._host:
            return False
        b.user32.SetLayeredWindowAttributes(
            self._host, 0, 255, self.LWA_ALPHA)
        self._control = b.user32.CreateWindowExW(
            0, "Magnifier", None,
            self.WS_CHILD | self.WS_VISIBLE | self.MS_SHOWMAGNIFIEDCURSOR,
            0, 0, 16, 16, self._host, None, instance, None)
        if not self._control:
            return False
        excluded = (wt.HWND * 1)(self._host)
        if not b.dll.MagSetWindowFilterList(
                self._control, self.MW_FILTERMODE_EXCLUDE, 1, excluded):
            return False
        return True

    def _requested(self) -> tuple[float, ScreenRect, tuple[int, int],
                                  tuple[float, float] | None, str]:
        with self._gate:
            return (self._requested_level, self._requested_monitor,
                    self._requested_anchor, self._requested_view, self.scope)

    def _on_apply(self) -> bool:
        target, monitor, anchor, requested_view, scope = self._requested()
        if monitor.is_empty:
            self._hide_on_thread()
            return False
        if target <= MIN_LEVEL + 0.001 and self._applied_level <= MIN_LEVEL + 0.001:
            self._hide_on_thread()
            return True
        if scope == "desktop":
            return self._apply_desktop_on_thread(monitor, anchor, target)
        if self._full_screen:
            self._hide_on_thread()

        needs_showing = monitor != self._placed_monitor
        if needs_showing:
            self._bindings.user32.ShowWindow(self._host, self.SW_HIDE)
            self._bindings.user32.SetWindowPos(
                self._host, None, monitor.x, monitor.y,
                monitor.width, monitor.height, self.SWP_NOACTIVATE)
            self._bindings.user32.SetWindowPos(
                self._control, None, 0, 0,
                monitor.width, monitor.height, self.SWP_NOACTIVATE)
            self._placed_monitor = monitor
            if requested_view is not None:
                self._view_x, self._view_y = requested_view
            elif self._applied_level <= MIN_LEVEL + 0.001:
                self._view_x, self._view_y = float(monitor.x), float(monitor.y)
            else:
                framed = viewport_offset(monitor, anchor, self._applied_level)
                self._view_x, self._view_y = map(float, framed)
            self._last_render_ms = 0.0
            source = _RECT(
                monitor.x, monitor.y,
                monitor.x + monitor.width, monitor.y + monitor.height)
            if not self._bindings.dll.MagSetWindowSource(
                    self._control, source):
                return False
            self._refresh_period_ms = self._refresh_period_for(monitor)

        if not self._refreshing or needs_showing:
            self._step_ramp(monitor, anchor, target)
        if needs_showing:
            self._bindings.user32.ShowWindow(self._host, self.SW_SHOWNA)
        if not self._refreshing or needs_showing:
            self._set_high_resolution_timer(True)
            self._bindings.user32.SetTimer(
                self._host, self.REFRESH_TIMER_ID,
                self._refresh_period_ms, None)
            self._refreshing = True
            self._last_tick_ms = 0.0
        return True

    def _apply_desktop_on_thread(
            self, monitor: ScreenRect, anchor: tuple[int, int],
            target: float) -> bool:
        if not self._full_screen:
            self._hide_on_thread()
            self._full_screen = True
            self._view_x, self._view_y = float(monitor.x), float(monitor.y)
            self._last_render_ms = 0.0
        self._step_ramp(monitor, anchor, target)
        if not self._refreshing:
            self._refresh_period_ms = self._refresh_period_for(monitor)
            self._set_high_resolution_timer(True)
            self._bindings.user32.SetTimer(
                self._host, self.REFRESH_TIMER_ID,
                self._refresh_period_ms, None)
            self._refreshing = True
        return True

    def _step_ramp(self, monitor: ScreenRect, anchor: tuple[int, int],
                   target: float) -> bool:
        now_ms = self._clock() * 1000.0
        elapsed_ms = (self._refresh_period_ms if self._last_render_ms == 0
                      else now_ms - self._last_render_ms)
        self._last_render_ms = now_ms
        next_level = ease_level(self._applied_level, target, elapsed_ms)
        self._view_x, self._view_y = rescale_about(
            monitor, anchor, self._view_x, self._view_y,
            self._applied_level, next_level)
        return self._render_level(monitor, next_level)

    def _render_level(self, monitor: ScreenRect, level: float) -> bool:
        if self._full_screen:
            view = viewport(monitor, self._view_x, self._view_y, level)
            offset_x, offset_y = fullscreen_offsets(monitor, view, level)
            applied = bool(self._bindings.dll.MagSetFullscreenTransform(
                level, offset_x, offset_y))
        else:
            values = window_transform(
                monitor, self._view_x, self._view_y, level)
            transform = _MAGTRANSFORM(*dataclasses.astuple(values))
            applied = bool(self._bindings.dll.MagSetWindowTransform(
                self._control, ctypes.byref(transform)))
        if applied:
            self._applied_level = level
        else:
            self._logger.warning(
                "Magnification frame was refused (error %s).",
                ctypes.get_last_error())
        return applied

    def _on_tick(self) -> None:
        target, monitor, anchor, _requested_view, _scope = self._requested()
        if monitor.is_empty:
            return
        work_started = self._clock()
        point = wt.POINT()
        have_pointer = bool(
            self._bindings.user32.GetCursorPos(ctypes.byref(point)))
        pointer = (int(point.x), int(point.y))
        if not self._full_screen and self._cursor is not None:
            self._cursor.set_hidden(
                have_pointer
                and self._applied_level > MIN_LEVEL + 0.001
                and monitor.contains(*pointer))

        ramping = self._applied_level != target
        if ramping:
            self._step_ramp(monitor, anchor, target)
            self._record_tick(True, (self._clock() - work_started) * 1000.0)
            if self._applied_level <= MIN_LEVEL + 0.001:
                self._hide_on_thread()
                return
            if self._refreshing and self._applied_level != target:
                self._bindings.user32.PostMessageW(
                    self._host, self.WM_APP_RAMP_TICK, 0, 0)
            return

        was_x, was_y = self._view_x, self._view_y
        if have_pointer:
            self._view_x, self._view_y = edge_push(
                monitor, pointer, self._view_x, self._view_y,
                self._applied_level)
        if (not self._full_screen
                or self._view_x != was_x or self._view_y != was_y):
            self._render_level(monitor, self._applied_level)
        self._record_tick(False, (self._clock() - work_started) * 1000.0)

    def _record_tick(self, ramping: bool, work_ms: float) -> None:
        now_ms = self._clock() * 1000.0
        if self._last_tick_ms:
            stats = self._ramp_stats if ramping else self._settled_stats
            stats.add(now_ms - self._last_tick_ms, work_ms)
        self._last_tick_ms = now_ms

    def _report_loop(self) -> None:
        for label, stats in (("ramping", self._ramp_stats),
                             ("settled", self._settled_stats)):
            if stats.frames >= 5:
                self._logger.info(
                    "Magnifier %s: %d frames, %.0f fps (mean %.1f ms, best %.1f ms, "
                    "worst %.1f ms), work %.1f ms mean / %.1f ms worst, %d slower "
                    "than 1.5x its own best frame (%.0f%%). Timer %d ms, panel %d Hz.",
                    label, stats.frames,
                    1000.0 * stats.frames / max(1.0, stats.interval_total),
                    stats.interval_total / stats.frames,
                    stats.interval_best, stats.interval_worst,
                    stats.work_total / stats.frames, stats.work_worst,
                    stats.late, 100.0 * stats.late / stats.frames,
                    self._refresh_period_ms, self._display_hz)
        self._ramp_stats = _LoopStats()
        self._settled_stats = _LoopStats()
        self._last_tick_ms = 0.0

    def _refresh_period_for(self, monitor: ScreenRect) -> int:
        self._display_hz = 0
        try:
            centre = wt.POINT(
                monitor.x + monitor.width // 2,
                monitor.y + monitor.height // 2)
            handle = self._bindings.user32.MonitorFromPoint(
                centre, self.MONITOR_DEFAULTTONEAREST)
            info = _MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
            if not handle or not self._bindings.user32.GetMonitorInfoW(
                    handle, ctypes.byref(info)):
                return self.FALLBACK_REFRESH_MS
            dc = self._bindings.gdi32.CreateDCW(
                None, info.szDevice, None, None)
            if not dc:
                return self.FALLBACK_REFRESH_MS
            try:
                self._display_hz = int(
                    self._bindings.gdi32.GetDeviceCaps(dc, self.VREFRESH))
            finally:
                self._bindings.gdi32.DeleteDC(dc)
        except BaseException as exc:
            self._logger.warning("Could not read display refresh rate: %s", exc)
            return self.FALLBACK_REFRESH_MS
        if self._display_hz <= 1:
            return self.FALLBACK_REFRESH_MS
        return min(33, max(4, 1000 // self._display_hz - 1))

    def _set_high_resolution_timer(self, wanted: bool) -> None:
        if wanted == self._high_resolution_timer:
            return
        if wanted:
            self._bindings.winmm.timeBeginPeriod(1)
        else:
            self._bindings.winmm.timeEndPeriod(1)
        self._high_resolution_timer = wanted

    def _hide_on_thread(self) -> None:
        if self._cursor is not None:
            for _ in range(3):
                if self._cursor.set_hidden(False):
                    break
        self._report_loop()
        self._set_high_resolution_timer(False)
        if self._host:
            self._bindings.user32.KillTimer(
                self._host, self.REFRESH_TIMER_ID)
            self._bindings.user32.ShowWindow(self._host, self.SW_HIDE)
        self._refreshing = False
        self._placed_monitor = None
        self._applied_level = MIN_LEVEL
        self._last_render_ms = 0.0
        if self._full_screen:
            self._bindings.dll.MagSetFullscreenTransform(MIN_LEVEL, 0, 0)
            self._full_screen = False

    def _cleanup_on_thread(self) -> None:
        self._hide_on_thread()
        if self._cursor is not None:
            # Retry a failed restore on teardown; never mark failure as success.
            for _ in range(3):
                if self._cursor.set_hidden(False):
                    break
        self._bindings.dll.MagSetFullscreenTransform(MIN_LEVEL, 0, 0)
        if self._control:
            self._bindings.user32.DestroyWindow(self._control)
        if self._host:
            self._bindings.user32.DestroyWindow(self._host)
        self._control = self._host = 0

    def _host_wnd_proc(self, hwnd: int, message: int,
                       w_param: int, l_param: int) -> int:
        if message == self.WM_APP_APPLY:
            return 1 if self._on_apply() else 0
        if message == self.WM_APP_HIDE:
            self._hide_on_thread()
            return 1
        if ((message == self.WM_TIMER
             and int(w_param) == self.REFRESH_TIMER_ID)
                or message == self.WM_APP_RAMP_TICK):
            self._on_tick()
            return 0
        return int(self._bindings.user32.DefWindowProcW(
            hwnd, message, w_param, l_param))

    def close(self) -> None:
        if self._closed:
            return
        thread = self._thread
        try:
            if thread is not None and thread.is_alive() and self._host:
                self._bindings.user32.SendMessageW(
                    self._host, self.WM_APP_HIDE, 0, 0)
        finally:
            self._closed = True
            if thread is not None and thread.is_alive():
                self._bindings.user32.PostThreadMessageW(
                    self._thread_id, self.WM_QUIT, 0, 0)
                thread.join(3.0)


class _MouseBindings:
    WH_MOUSE_LL = 14

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("pt", wt.POINT),
            ("mouseData", wt.DWORD),
            ("flags", wt.DWORD),
            ("time", wt.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("WH_MOUSE_LL is available only on Windows.")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.hook_proc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t,
            ctypes.c_ssize_t)
        self.user32.SetWindowsHookExW.restype = ctypes.c_void_p
        self.user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, self.hook_proc_type, ctypes.c_void_p, wt.DWORD]
        self.user32.UnhookWindowsHookEx.restype = wt.BOOL
        self.user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        self.user32.CallNextHookEx.restype = ctypes.c_ssize_t
        self.user32.CallNextHookEx.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t]
        self.user32.GetMessageW.restype = ctypes.c_int
        self.user32.GetMessageW.argtypes = [
            ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
        self.user32.TranslateMessage.restype = wt.BOOL
        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
        self.user32.DispatchMessageW.restype = ctypes.c_ssize_t
        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
        self.user32.PostThreadMessageW.restype = wt.BOOL
        self.user32.PostThreadMessageW.argtypes = [
            wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
        self.user32.GetAsyncKeyState.restype = ctypes.c_short
        self.user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.kernel32.GetCurrentThreadId.restype = wt.DWORD
        self.kernel32.GetCurrentThreadId.argtypes = []
        self.kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        self.kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]


class ZoomMouseHook:
    """A scoped low-level mouse hook with its own message-pump thread."""

    WM_MOUSEMOVE = 0x0200
    WM_MOUSEWHEEL = 0x020A
    WM_QUIT = 0x0012
    LLMHF_INJECTED = 0x01
    WHEEL_DELTA = 120

    _MODIFIER_VKS = {
        "Ctrl": (0x11, 0xA2, 0xA3),
        "Alt": (0x12, 0xA4, 0xA5),
        "Shift": (0x10, 0xA0, 0xA1),
        "Win": (0x5B, 0x5C),
    }

    def __init__(
            self, gesture: ZoomGesture,
            on_zoom: Callable[[float, int, int], None],
            is_suspended: Callable[[], bool],
            keyboard_service: KeyboardInterceptionService | Any | None = None,
            *, bindings_factory: Callable[[], Any] | None = None,
            submit: Callable[..., Any] | None = None,
            clock: Callable[[], float] = time.monotonic,
            logger: logging.Logger | None = None) -> None:
        self.gesture = gesture
        self._on_zoom = on_zoom
        self._is_suspended = is_suspended
        self._keyboard_service = keyboard_service
        self._bindings_factory = bindings_factory or _MouseBindings
        self._submit_override = submit
        self._clock = clock
        self._logger = logger or logging.getLogger(__name__)
        self._observer = ZoomModifierObserver(gesture)
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hook: int | None = None
        self._callback: Any = None
        self._bindings: Any = None
        self._executor: ThreadPoolExecutor | None = None
        self._started = threading.Event()
        self._startup_error: BaseException | None = None
        self._stopping = False
        self._observer_registered = False

    @property
    def installed(self) -> bool:
        return self._hook is not None

    def start(self) -> None:
        """Register the observer and start the only mouse-hook installation path."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopping = False
        self._startup_error = None
        self._started.clear()
        if self._keyboard_service is not None:
            self._keyboard_service.register_consumer(self._observer)
            self._observer_registered = True
        if self._submit_override is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="OpenSpan.ZoomSteps")
        self._thread = threading.Thread(
            target=self._thread_main,
            name="OpenSpan.ZoomMouseHook", daemon=True)
        try:
            self._thread.start()
            if not self._started.wait(5.0):
                raise TimeoutError("Zoom mouse hook did not start within 5 seconds.")
            if self._startup_error is not None:
                raise self._startup_error
        except BaseException:
            self.stop()
            raise

    def _thread_main(self) -> None:
        bindings = None
        try:
            bindings = self._bindings_factory()
            self._bindings = bindings
            message = wt.MSG()
            bindings.user32.PeekMessageW(
                ctypes.byref(message), None, 0, 0, 0)
            self._thread_id = int(bindings.kernel32.GetCurrentThreadId())
            self._callback = bindings.hook_proc_type(self._mouse_callback)
            module = bindings.kernel32.GetModuleHandleW(None)
            self._hook = bindings.user32.SetWindowsHookExW(
                bindings.WH_MOUSE_LL, self._callback, module, 0)
            if not self._hook:
                raise OSError(
                    ctypes.get_last_error(), "Zoom mouse hook could not be installed")
        except BaseException as exc:
            self._startup_error = exc
            self._started.set()
            return

        self._started.set()
        try:
            while not self._stopping:
                result = bindings.user32.GetMessageW(
                    ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                bindings.user32.TranslateMessage(ctypes.byref(message))
                bindings.user32.DispatchMessageW(ctypes.byref(message))
        finally:
            if self._hook:
                bindings.user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def _is_physically_down(self, canonical_key: str) -> bool:
        bindings = self._bindings
        if bindings is None:
            return False
        return any(
            bindings.user32.GetAsyncKeyState(vk) & 0x8000
            for vk in self._MODIFIER_VKS.get(canonical_key, ()))

    def _submit_zoom(self, notches: float, x: int, y: int) -> None:
        if self._submit_override is not None:
            self._submit_override(self._on_zoom, notches, x, y)
        elif self._executor is not None:
            self._executor.submit(self._on_zoom, notches, x, y)
        else:
            raise RuntimeError("Zoom hook is not started.")

    def dispatch_mouse_event(
            self, message: int, *, raw_delta: int = 0,
            is_injected: bool = False, x: int = 0, y: int = 0,
            now_ms: int | None = None,
            is_physically_down: Callable[[str], bool] | None = None,
            call_next: Callable[[], int] = lambda: 0) -> int:
        """Drive one decoded mouse event; this seam keeps tests wholly native-free."""
        now_ms = (round(self._clock() * 1000)
                  if now_ms is None else now_ms)
        if message == self.WM_MOUSEMOVE:
            self.gesture.on_pointer_move(is_injected, now_ms)
            return call_next()
        if message != self.WM_MOUSEWHEEL:
            return call_next()
        if self._is_suspended():
            self.gesture.reset()
            return call_next()

        self.gesture.sync_held(is_physically_down or self._is_physically_down)
        direction = (1 if raw_delta > 0 else -1 if raw_delta < 0 else 0)
        verdict = self.gesture.on_wheel(direction, is_injected, now_ms)
        if verdict is ZoomVerdict.PASS_THROUGH:
            return call_next()
        self._submit_zoom(raw_delta / self.WHEEL_DELTA, x, y)
        return 1

    def _mouse_callback(self, code: int, w_param: int, l_param: int) -> int:
        bindings = self._bindings
        if bindings is None:
            return 0

        def call_next() -> int:
            return int(bindings.user32.CallNextHookEx(
                self._hook, code, w_param, l_param))

        if code < 0:
            return call_next()
        try:
            data = ctypes.cast(
                l_param, ctypes.POINTER(bindings.MSLLHOOKSTRUCT)).contents
            raw_delta = ctypes.c_short(
                (int(data.mouseData) >> 16) & 0xFFFF).value
            return self.dispatch_mouse_event(
                int(w_param), raw_delta=raw_delta,
                is_injected=bool(data.flags & self.LLMHF_INJECTED),
                x=int(data.pt.x), y=int(data.pt.y), call_next=call_next)
        except BaseException as exc:
            self._logger.error("Zoom mouse hook failed; forwarding: %s", exc)
            return call_next()

    def stop(self) -> None:
        """Unregister observation, stop the pump, and forget modifier state."""
        self._stopping = True
        self.gesture.reset()
        if self._observer_registered and self._keyboard_service is not None:
            self._keyboard_service.unregister_consumer(self._observer.consumer_id)
            self._observer_registered = False
        bindings = self._bindings
        if bindings is not None and self._thread_id:
            bindings.user32.PostThreadMessageW(
                self._thread_id, self.WM_QUIT, 0, 0)
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(3.0)
        self._thread = None
        self._thread_id = 0
        executor = self._executor
        self._executor = None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)


class ScreenZoomModule:
    """Feature lifetime, settings, suspension, anchoring, and zoom steps."""

    GESTURE_GAP_MS = 600

    def __init__(
            self, magnifier: Any | None = None,
            keyboard_service: KeyboardInterceptionService | Any | None = None,
            settings_service: SettingsService | None = None,
            *, monitor_provider: Callable[[], Sequence[MonitorIdentity]] =
            attached_identities,
            hook_factory: Callable[..., ZoomMouseHook] = ZoomMouseHook,
            is_suspended: Callable[[], bool] | None = None,
            clock: Callable[[], float] = time.monotonic,
            logger: logging.Logger | None = None) -> None:
        self.magnifier = magnifier or ScreenMagnifier(logger=logger)
        self.keyboard_service = keyboard_service
        self.settings_service = settings_service
        self._monitor_provider = monitor_provider
        self._hook_factory = hook_factory
        self._external_suspension = is_suspended
        self._clock = clock
        self._logger = logger or logging.getLogger(__name__)
        self.gesture = ZoomGesture()
        self._hook: ZoomMouseHook | None = None
        self._level = MIN_LEVEL
        self._gesture_anchor = (0, 0)
        self._gesture_monitor: ScreenRect | None = None
        self._last_step_ms = 0
        self._suspended = False
        self._available: bool | None = None
        self._gate = threading.RLock()
        self.last_error: str | None = None

    @property
    def level(self) -> float:
        with self._gate:
            return self._level

    @property
    def available(self) -> bool | None:
        return self._available

    @property
    def is_running(self) -> bool:
        return self._hook is not None and self._hook.installed

    @property
    def modifier(self) -> ChordModifiers:
        return self.gesture.modifier

    def _is_suspended(self) -> bool:
        if self._suspended:
            return True
        if self._external_suspension is not None:
            return bool(self._external_suspension())
        router = getattr(self.keyboard_service, "router", None)
        return bool(getattr(router, "is_suspended", False))

    def set_suspended(self, suspended: bool) -> None:
        self._suspended = bool(suspended)
        if self._suspended:
            self.gesture.reset()

    def set_modifier(self, value: str | ChordModifiers) -> ChordModifiers:
        modifier = (value if isinstance(value, ChordModifiers)
                    else ZoomGesture.parse_modifier(value))
        self.gesture.modifier = modifier
        self.gesture.reset()
        if self.settings_service is not None:
            self.settings_service.set_setting(
                FEATURE_ID, MODIFIER_SETTING, modifier_name(modifier))
        return modifier

    def _load_settings(self) -> float:
        if self.settings_service is None:
            self.gesture.modifier = ChordModifiers.ALT
            return DEFAULT_STEP_FACTOR
        modifier = self.settings_service.get_setting(
            FEATURE_ID, MODIFIER_SETTING)
        self.gesture.modifier = ZoomGesture.parse_modifier(modifier)
        factor = self.settings_service.get_setting(
            FEATURE_ID, SCROLL_STEP_SETTING)
        return (float(factor) if isinstance(factor, (int, float))
                and not isinstance(factor, bool) else DEFAULT_STEP_FACTOR)

    def start(self) -> bool:
        """Probe magnification first, then and only then install the mouse hook."""
        if self._hook is not None:
            return self.is_running
        self.last_error = None
        try:
            self._available = bool(self.magnifier.probe())
        except BaseException as exc:
            self._available = False
            self.last_error = str(exc)
        if not self._available:
            return False

        step_factor = self._load_settings()
        hook = self._hook_factory(
            self.gesture,
            lambda notches, x, y: self.zoom_at(
                notches, (x, y), step_factor),
            self._is_suspended,
            self.keyboard_service,
            clock=self._clock,
            logger=self._logger,
        )
        try:
            hook.start()
        except BaseException as exc:
            self.last_error = str(exc)
            hook.stop()
            return False
        self._hook = hook
        return True

    def stop(self) -> bool:
        """Attempt the mandatory 1x restore before releasing the input hook."""
        errors: list[str] = []
        with self._gate:
            self._reset_state()
        try:
            restored = self.magnifier.reset()
            if restored is False:
                errors.append("magnifier refused the 1x restore")
        except BaseException as exc:
            errors.append(str(exc))

        hook = self._hook
        self._hook = None
        if hook is not None:
            try:
                hook.stop()
            except BaseException as exc:
                errors.append(str(exc))
        self.gesture.reset()
        self.last_error = "; ".join(errors) or None
        return not errors

    def _reset_state(self) -> None:
        self._level = MIN_LEVEL
        self._gesture_anchor = (0, 0)
        self._gesture_monitor = None
        self._last_step_ms = 0

    def zoom_at(
            self, notches: float, anchor: tuple[int, int],
            step_factor: float = DEFAULT_STEP_FACTOR) -> bool:
        now_ms = round(self._clock() * 1000)
        monitors = tuple(self._monitor_provider())
        pointer_monitor = screen_for_point(monitors, *anchor)
        with self._gate:
            continuing = (
                self._level > MIN_LEVEL
                and now_ms - self._last_step_ms < self.GESTURE_GAP_MS
                and self._gesture_monitor is not None
            )
            self._last_step_ms = now_ms
            if continuing:
                anchor = self._gesture_anchor
                source = self._gesture_monitor
            else:
                self._gesture_anchor = anchor
                self._gesture_monitor = pointer_monitor
                source = pointer_monitor or next(
                    (ScreenRect(m.virtual_x, m.virtual_y,
                                m.native_width, m.native_height)
                     for m in monitors
                     if m.virtual_x == 0 and m.virtual_y == 0),
                    ScreenRect(0, 0, 1920, 1080))
                self._gesture_monitor = source
            next_level = step_level(self._level, notches, step_factor)
            try:
                applied = bool(self.magnifier.apply(
                    next_level, source, anchor))
            except BaseException as exc:
                self.last_error = str(exc)
                return False
            if not applied:
                return False
            self._level = next_level
            if next_level <= MIN_LEVEL + 0.001:
                self._reset_state()
            return True

    def describe_active_hooks(self) -> tuple[str, ...]:
        if not self.is_running:
            return ()
        return (
            "WH_MOUSE_LL mouse-wheel hook; modifier observed through the "
            f"central keyboard service ({modifier_name(self.modifier)}).",
        )


def _probe() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only per-display transform probe by default.")
    parser.add_argument(
        "--live-primary", action="store_true",
        help="perform one live 2x-and-back demonstration on the primary")
    args = parser.parse_args()

    monitors = tuple(attached_identities())
    print("READ-ONLY TRANSFORM PROBE")
    for item in monitors:
        monitor = ScreenRect(
            item.virtual_x, item.virtual_y,
            item.native_width, item.native_height)
        anchor = (monitor.x + monitor.width // 2,
                  monitor.y + monitor.height // 2)
        view_x, view_y = rescale_about(
            monitor, anchor, float(monitor.x), float(monitor.y), 1.0, 2.0)
        transform = window_transform(monitor, view_x, view_y, 2.0)
        expected_x = monitor.x - view_x * 2.0
        expected_y = monitor.y - view_y * 2.0
        x_ok = math.isclose(transform.v02, expected_x, abs_tol=1e-9)
        y_ok = math.isclose(transform.v12, expected_y, abs_tol=1e-9)
        print(
            f"{item.device_name}: monitor=({monitor.x},{monitor.y},"
            f"{monitor.width},{monitor.height}) anchor={anchor} "
            f"view=({view_x:.3f},{view_y:.3f})")
        print(
            "  transform="
            f"[[{transform.v00:.3f},{transform.v01:.3f},{transform.v02:.3f}],"
            f"[{transform.v10:.3f},{transform.v11:.3f},{transform.v12:.3f}],"
            f"[{transform.v20:.3f},{transform.v21:.3f},{transform.v22:.3f}]]")
        print(
            f"  assert v02 == monitor.X - viewX * level: "
            f"{'PASS' if x_ok else 'FAIL'} "
            f"({transform.v02:.3f} == {expected_x:.3f})")
        print(
            f"  assert v12 == monitor.Y - viewY * level: "
            f"{'PASS' if y_ok else 'FAIL'} "
            f"({transform.v12:.3f} == {expected_y:.3f})")
        assert x_ok and y_ok

    if not args.live_primary:
        print("LIVE PRIMARY DEMONSTRATION: SKIPPED (use --live-primary)")
        return

    print("LIVE PRIMARY DEMONSTRATION")
    primary_item = next(
        (item for item in monitors
         if item.virtual_x == 0 and item.virtual_y == 0), None)
    if primary_item is None:
        print("  available=False (no primary display at desktop origin)")
        return
    primary = ScreenRect(
        primary_item.virtual_x, primary_item.virtual_y,
        primary_item.native_width, primary_item.native_height)
    anchor = (primary.x + primary.width // 2,
              primary.y + primary.height // 2)
    magnifier = ScreenMagnifier()
    try:
        available = magnifier.probe()
        print(f"  available={available}")
        if not available:
            return
        print(f"  target-before={magnifier.level:.3f}")
        up = magnifier.apply(2.0, primary, anchor)
        time.sleep(0.35)
        print(f"  ramp-to-2x={'PASS' if up else 'FAIL'}")
        down = magnifier.reset()
        time.sleep(0.35)
        print(f"  ramp-to-1x={'PASS' if down else 'FAIL'}")
    finally:
        reset = magnifier.reset()
        magnifier.close()
        cursor_restored = (
            magnifier._cursor is None or not magnifier._cursor.hidden)
        print(f"  finally-reset={'PASS' if reset else 'FAIL'}")
        print(
            f"  cursor-restored={'PASS' if cursor_restored else 'FAIL'}")


if __name__ == "__main__":
    _probe()


# Wiring notes (intentionally not wired into openspan.py in this slice):
# 1. Register FEATURE_DECLARATION in the shared FeatureRegistry.
# 2. Construct ScreenZoomModule with the shared SettingsService and the existing
#    KeyboardInterceptionService; do not create or start another keyboard hook.
# 3. Call start() only when the disabled-by-default feature is enabled, propagate
#    shortcut suspension through set_suspended(), and always call stop() at exit.
