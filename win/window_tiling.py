"""Pure halves-and-quarters tiling geometry with optional Windows placement.

The geometry and decision helpers have no platform dependencies.  Native calls
are loaded lazily by the monitor and placement helpers so importing this module
is safe in tests and on non-Windows hosts.
"""

from __future__ import annotations

import dataclasses
import enum
import threading
from collections.abc import Hashable, Sequence
from typing import Any


@dataclasses.dataclass(frozen=True, slots=True)
class Rect:
    """An integer rectangle in physical virtual-desktop pixels."""

    x: int
    y: int
    width: int
    height: int

    @classmethod
    def from_edges(cls, left: int, top: int, right: int, bottom: int) -> Rect:
        return cls(left, top, right - left, bottom - top)

    @property
    def left(self) -> int:
        return self.x

    @property
    def top(self) -> int:
        return self.y

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def is_empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    def intersect(self, other: Rect) -> Rect:
        left = max(self.left, other.left)
        top = max(self.top, other.top)
        right = min(self.right, other.right)
        bottom = min(self.bottom, other.bottom)
        if right <= left or bottom <= top:
            return Rect(0, 0, 0, 0)
        return Rect.from_edges(left, top, right, bottom)

    def intersection_area(self, other: Rect) -> int:
        intersection = self.intersect(other)
        return intersection.width * intersection.height

    def __str__(self) -> str:
        return f"({self.x},{self.y}) {self.width}x{self.height}"


PixelRect = Rect


class TileZone(enum.Enum):
    LEFT_HALF = "left-half"
    RIGHT_HALF = "right-half"
    TOP_HALF = "top-half"
    BOTTOM_HALF = "bottom-half"
    TOP_LEFT = "top-left"
    TOP_RIGHT = "top-right"
    BOTTOM_LEFT = "bottom-left"
    BOTTOM_RIGHT = "bottom-right"


class TileDirection(enum.Enum):
    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"


def compute(work_area: Rect, zone: TileZone) -> Rect:
    """Partition *work_area* into *zone*, assigning odd pixels right/bottom."""
    # C# integer division truncates toward zero. Monitor sizes are positive, but
    # retaining that rule also makes malformed synthetic rectangles port exactly.
    left_width = (work_area.width // 2 if work_area.width >= 0
                  else -((-work_area.width) // 2))
    right_width = work_area.width - left_width
    top_height = (work_area.height // 2 if work_area.height >= 0
                  else -((-work_area.height) // 2))
    bottom_height = work_area.height - top_height
    mid_x = work_area.x + left_width
    mid_y = work_area.y + top_height

    zones = {
        TileZone.LEFT_HALF:
            Rect(work_area.x, work_area.y, left_width, work_area.height),
        TileZone.RIGHT_HALF:
            Rect(mid_x, work_area.y, right_width, work_area.height),
        TileZone.TOP_HALF:
            Rect(work_area.x, work_area.y, work_area.width, top_height),
        TileZone.BOTTOM_HALF:
            Rect(work_area.x, mid_y, work_area.width, bottom_height),
        TileZone.TOP_LEFT:
            Rect(work_area.x, work_area.y, left_width, top_height),
        TileZone.TOP_RIGHT:
            Rect(mid_x, work_area.y, right_width, top_height),
        TileZone.BOTTOM_LEFT:
            Rect(work_area.x, mid_y, left_width, bottom_height),
        TileZone.BOTTOM_RIGHT:
            Rect(mid_x, mid_y, right_width, bottom_height),
    }
    try:
        return zones[zone]
    except KeyError:
        raise ValueError(f"unknown tile zone: {zone!r}") from None


_REFINEMENTS = {
    (TileZone.LEFT_HALF, TileDirection.UP): TileZone.TOP_LEFT,
    (TileZone.LEFT_HALF, TileDirection.DOWN): TileZone.BOTTOM_LEFT,
    (TileZone.RIGHT_HALF, TileDirection.UP): TileZone.TOP_RIGHT,
    (TileZone.RIGHT_HALF, TileDirection.DOWN): TileZone.BOTTOM_RIGHT,
    (TileZone.TOP_LEFT, TileDirection.DOWN): TileZone.LEFT_HALF,
    (TileZone.BOTTOM_LEFT, TileDirection.UP): TileZone.LEFT_HALF,
    (TileZone.TOP_RIGHT, TileDirection.DOWN): TileZone.RIGHT_HALF,
    (TileZone.BOTTOM_RIGHT, TileDirection.UP): TileZone.RIGHT_HALF,
}

_DIRECTION_ZONES = {
    TileDirection.LEFT: TileZone.LEFT_HALF,
    TileDirection.RIGHT: TileZone.RIGHT_HALF,
    TileDirection.UP: TileZone.TOP_HALF,
    TileDirection.DOWN: TileZone.BOTTOM_HALF,
}


def refine(current: TileZone, direction: TileDirection) -> TileZone | None:
    """Return the macOS-style cross-axis refinement, if one exists."""
    return _REFINEMENTS.get((current, direction))


def approximately(left: Rect, right: Rect, tolerance: int = 2) -> bool:
    """Compare bounds using the platform driver's two-pixel tolerance."""
    return (abs(left.x - right.x) <= tolerance
            and abs(left.y - right.y) <= tolerance
            and abs(left.width - right.width) <= tolerance
            and abs(left.height - right.height) <= tolerance)


def recognize_zone(current: Rect, work_area: Rect,
                   tolerance: int = 2) -> TileZone | None:
    """Recognize an unconstrained tile rect in a monitor work area."""
    return next((zone for zone in TileZone
                 if approximately(current, compute(work_area, zone), tolerance)),
                None)


def zone_for_direction(direction: TileDirection,
                       current_zone: TileZone | None = None) -> TileZone:
    """Apply refinement, falling back to the direction's half-zone command."""
    try:
        base = _DIRECTION_ZONES[direction]
    except KeyError:
        raise ValueError(f"unknown tile direction: {direction!r}") from None
    refined = (refine(current_zone, direction)
               if current_zone is not None else None)
    return refined if refined is not None else base


def tile_towards(current: Rect, work_area: Rect, direction: TileDirection,
                 known_zone: TileZone | None = None,
                 tolerance: int = 2) -> tuple[TileZone, Rect]:
    """Choose and compute the next target from current bounds and a direction.

    A tracked zone is trusted only while the current bounds still match it.  If
    no tracked state is supplied, exact current geometry is recognized, making
    the same decision logic useful to stateless callers.
    """
    if known_zone is not None:
        expected = compute(work_area, known_zone)
        current_zone = known_zone if approximately(
            current, expected, tolerance) else None
    else:
        current_zone = recognize_zone(current, work_area, tolerance)
    zone = zone_for_direction(direction, current_zone)
    return zone, compute(work_area, zone)


_INT_MAX = 2**31 - 1


def _clamp(value: int, lower: int, upper: int) -> int:
    if lower > upper:
        raise ValueError("minimum size cannot exceed maximum size")
    return min(max(value, lower), upper)


def apply_size_constraints(target: Rect, work_area: Rect,
                           min_width: int, min_height: int,
                           max_width: int = _INT_MAX,
                           max_height: int = _INT_MAX) -> Rect:
    """Clamp size and anchor the result within the work area's far edges."""
    width = _clamp(target.width, min(min_width, work_area.width), max_width)
    height = _clamp(target.height, min(min_height, work_area.height), max_height)

    x = target.x
    y = target.y
    if x + width > work_area.right:
        x = work_area.right - width
    if y + height > work_area.bottom:
        y = work_area.bottom - height
    x = max(x, work_area.x)
    y = max(y, work_area.y)
    return Rect(x, y, width, height)


@dataclasses.dataclass(frozen=True, slots=True)
class _RestoreEntry:
    original_bounds: Rect
    current_zone: TileZone


class TileRestoreTracker:
    """Remember only the first pre-tile bounds for each window."""

    def __init__(self) -> None:
        self._entries: dict[Hashable, _RestoreEntry] = {}
        self._lock = threading.Lock()

    def on_tiled(self, window: Hashable, bounds_before_tile: Rect,
                 zone: TileZone) -> None:
        with self._lock:
            old = self._entries.get(window)
            original = (bounds_before_tile if old is None
                        else old.original_bounds)
            self._entries[window] = _RestoreEntry(original, zone)

    def get_current_zone(self, window: Hashable) -> TileZone | None:
        with self._lock:
            entry = self._entries.get(window)
            return entry.current_zone if entry is not None else None

    def try_restore(self, window: Hashable) -> Rect | None:
        with self._lock:
            entry = self._entries.pop(window, None)
            return entry.original_bounds if entry is not None else None

    def invalidate(self, window: Hashable) -> None:
        with self._lock:
            self._entries.pop(window, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


@dataclasses.dataclass(frozen=True, slots=True)
class MonitorWorkArea:
    handle: int
    bounds: Rect
    work_area: Rect
    is_primary: bool
    device_name: str = ""


def _rect_distance_squared(left: Rect, right: Rect) -> int:
    dx = max(left.left - right.right, right.left - left.right, 0)
    dy = max(left.top - right.bottom, right.top - left.bottom, 0)
    return dx * dx + dy * dy


def select_work_area(current: Rect,
                     monitors: Sequence[MonitorWorkArea]) -> Rect | None:
    """Choose the greatest-overlap monitor, or the nearest when off-screen."""
    if not monitors:
        return None
    index, monitor = min(
        enumerate(monitors),
        key=lambda item: (
            -current.intersection_area(item[1].bounds),
            _rect_distance_squared(current, item[1].bounds),
            item[0],
        ))
    del index
    return monitor.work_area


_native: dict[str, Any] | None = None


def _load_native() -> dict[str, Any]:
    global _native
    if _native is not None:
        return _native

    import ctypes
    import ctypes.wintypes as wt

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", RECT),
                    ("rcWork", RECT), ("dwFlags", wt.DWORD),
                    ("szDevice", ctypes.c_wchar * 32)]

    monitor_proc = ctypes.WINFUNCTYPE(
        wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(RECT), wt.LPARAM)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

    user32.EnumDisplayMonitors.restype = wt.BOOL
    user32.EnumDisplayMonitors.argtypes = [
        wt.HDC, ctypes.c_void_p, monitor_proc, wt.LPARAM]
    user32.GetMonitorInfoW.restype = wt.BOOL
    user32.GetMonitorInfoW.argtypes = [
        wt.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]
    user32.GetWindowRect.restype = wt.BOOL
    user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
    user32.IsIconic.restype = wt.BOOL
    user32.IsIconic.argtypes = [wt.HWND]
    user32.IsZoomed.restype = wt.BOOL
    user32.IsZoomed.argtypes = [wt.HWND]
    user32.ShowWindow.restype = wt.BOOL
    user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
    user32.SetWindowPos.restype = wt.BOOL
    user32.SetWindowPos.argtypes = [
        wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wt.UINT]
    user32.CreateWindowExW.restype = wt.HWND
    user32.CreateWindowExW.argtypes = [
        wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
    user32.DestroyWindow.restype = wt.BOOL
    user32.DestroyWindow.argtypes = [wt.HWND]
    dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
    dwmapi.DwmGetWindowAttribute.argtypes = [
        wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]

    _native = {
        "ctypes": ctypes, "user32": user32, "dwmapi": dwmapi,
        "RECT": RECT, "MONITORINFOEXW": MONITORINFOEXW,
        "MONITOR_PROC": monitor_proc,
    }
    return _native


def enumerate_work_areas() -> tuple[MonitorWorkArea, ...]:
    """Read attached monitor bounds and usable work areas without changing them."""
    native = _load_native()
    ctypes = native["ctypes"]
    monitors: list[MonitorWorkArea] = []
    callback_errors: list[BaseException] = []

    def collect(handle: int, hdc: int, rect: Any, data: int) -> bool:
        del hdc, rect, data
        try:
            info = native["MONITORINFOEXW"]()
            info.cbSize = ctypes.sizeof(info)
            if not native["user32"].GetMonitorInfoW(
                    handle, ctypes.byref(info)):
                return True
            bounds = Rect.from_edges(
                info.rcMonitor.left, info.rcMonitor.top,
                info.rcMonitor.right, info.rcMonitor.bottom)
            work = Rect.from_edges(
                info.rcWork.left, info.rcWork.top,
                info.rcWork.right, info.rcWork.bottom)
            monitors.append(MonitorWorkArea(
                int(handle), bounds, work, bool(info.dwFlags & 1),
                info.szDevice))
            return True
        except BaseException as exc:
            callback_errors.append(exc)
            return False

    callback = native["MONITOR_PROC"](collect)
    if not native["user32"].EnumDisplayMonitors(None, None, callback, 0):
        if callback_errors:
            raise callback_errors[0]
        error = ctypes.get_last_error()
        raise OSError(error, "EnumDisplayMonitors failed")
    if callback_errors:
        raise callback_errors[0]
    return tuple(monitors)


def primary_work_area() -> Rect:
    """Return the real primary monitor's usable work area."""
    monitors = enumerate_work_areas()
    primary = next((monitor for monitor in monitors if monitor.is_primary), None)
    if primary is None:
        raise RuntimeError("Windows reported no primary monitor")
    return primary.work_area


def _frame_margins(hwnd: int) -> tuple[int, int, int, int] | None:
    native = _load_native()
    ctypes, RECT = native["ctypes"], native["RECT"]
    window_rect = RECT()
    if not native["user32"].GetWindowRect(hwnd, ctypes.byref(window_rect)):
        return None

    frame_rect = RECT()
    result = native["dwmapi"].DwmGetWindowAttribute(
        hwnd, 9, ctypes.byref(frame_rect), ctypes.sizeof(frame_rect))
    if result != 0:
        return 0, 0, 0, 0
    return (frame_rect.left - window_rect.left,
            frame_rect.top - window_rect.top,
            window_rect.right - frame_rect.right,
            window_rect.bottom - frame_rect.bottom)


def place(hwnd: int, rect: Rect) -> bool:
    """Place a window's visible DWM frame without activation or z-order change."""
    native = _load_native()
    user32 = native["user32"]
    if user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, 9)
    margins = _frame_margins(hwnd)
    if margins is None:
        return False
    left, top, right, bottom = margins
    return bool(user32.SetWindowPos(
        hwnd, None,
        rect.x - left, rect.y - top,
        rect.width + left + right, rect.height + top + bottom,
        0x0004 | 0x0010))


class TilingGeometry:
    """C#-shaped facade over the pure geometry functions."""

    compute = staticmethod(compute)
    refine = staticmethod(refine)
    apply_size_constraints = staticmethod(apply_size_constraints)


def _probe_placement() -> bool:
    native = _load_native()
    hwnd = native["user32"].CreateWindowExW(
        0, "STATIC", "OpenSpan window-tiling probe", 0x00CF0000,
        -32000, -32000, 160, 120, None, None, None, None)
    if not hwnd:
        return False
    try:
        return place(int(hwnd), Rect(-30000, -30000, 200, 150))
    finally:
        native["user32"].DestroyWindow(hwnd)


def _print_zones(label: str, work_area: Rect) -> None:
    print(f"{label} work-area={work_area}")
    for zone in TileZone:
        print(f"{label} {zone.name}={compute(work_area, zone)}")


def main() -> int:
    synthetic = Rect(0, 0, 1920, 1080)
    _print_zones("SYNTHETIC", synthetic)
    _print_zones("PRIMARY", primary_work_area())

    first_zone, first_rect = tile_towards(
        compute(synthetic, TileZone.LEFT_HALF), synthetic, TileDirection.UP,
        TileZone.LEFT_HALF)
    second_zone, second_rect = tile_towards(
        first_rect, synthetic, TileDirection.UP, first_zone)
    print("REFINE start=LEFT_HALF "
          f"press1=UP->{first_zone.name}:{first_rect} "
          f"press2=UP->{second_zone.name}:{second_rect}")
    print(f"PLACEMENT created-offscreen-window={_probe_placement()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
