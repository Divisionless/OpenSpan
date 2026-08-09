"""Named window-layout capture, planning, persistence, and read-only probe.

The pure model and planner are the first half of this file.  Native Windows
calls are reached only by :func:`scan_live_desktop`, while importing the module
and planning restores remain portable and deterministic.
"""

from __future__ import annotations

import dataclasses
import math
import os

# Pixels of slack when deciding a window is already where a preset wants it.
# Matches window_tiling's driver tolerance: below this, nothing is visible.
RESTORE_TOLERANCE = 2
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any

from config_store import ConfigStore
from monitor_identity import attached_identities
from window_tiling import enumerate_work_areas
from window_tracker import (
    MatchStrength,
    PixelRect,
    WindowIdentity,
    WindowService,
    WindowTracker,
    assign as assign_windows,
)


FEATURE_ID = "window-layout-presets"
PRESETS_SETTING = "presets"
WINDOW_NOT_FOUND = "window-not-found"
MONITOR_NOT_FOUND = "monitor-not-found"
MONITOR_INSTANCE_NOT_FOUND = "monitor-instance-not-found"


@dataclasses.dataclass(frozen=True, slots=True)
class RelativeRect:
    """Window edges relative to a monitor work area."""

    x: float
    y: float
    width: float
    height: float


@dataclasses.dataclass(frozen=True, slots=True)
class MonitorState:
    """Durable key, usable rectangle, and position for twin ordering."""

    stable_key: str
    work_area: PixelRect
    virtual_x: int | None = None
    virtual_y: int | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class LiveWindow:
    handle: int
    identity: WindowIdentity
    bounds: PixelRect
    monitor: MonitorState


@dataclasses.dataclass(frozen=True, slots=True)
class LiveDesktop(Sequence[LiveWindow]):
    """A live window sequence that also records empty attached monitors."""

    windows: tuple[LiveWindow, ...]
    monitors: tuple[MonitorState, ...]

    def __iter__(self) -> Iterator[LiveWindow]:
        return iter(self.windows)

    def __len__(self) -> int:
        return len(self.windows)

    def __getitem__(self, index: int | slice) -> LiveWindow | tuple[LiveWindow, ...]:
        return self.windows[index]


@dataclasses.dataclass(frozen=True, slots=True)
class PresetWindow:
    identity: WindowIdentity
    monitor_key: str
    relative_rect: RelativeRect
    monitor_ordinal: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class LayoutPreset:
    name: str
    windows: tuple[PresetWindow, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class RestoreMove:
    handle: int
    identity: WindowIdentity
    target: PixelRect


@dataclasses.dataclass(frozen=True, slots=True)
class UnmatchedWindow:
    window: PresetWindow
    reason: str


@dataclasses.dataclass(frozen=True, slots=True)
class AppliedMove:
    move: RestoreMove
    succeeded: bool


def _round_half_away_from_zero(value: float) -> int:
    if value >= 0:
        return math.floor(value + 0.5)
    return math.ceil(value - 0.5)


def relative_rect(bounds: PixelRect, work_area: PixelRect) -> RelativeRect:
    """Convert physical bounds to monitor-relative fractions."""
    if work_area.width <= 0 or work_area.height <= 0:
        raise ValueError("monitor work area must have positive dimensions")
    return RelativeRect(
        (bounds.x - work_area.x) / work_area.width,
        (bounds.y - work_area.y) / work_area.height,
        bounds.width / work_area.width,
        bounds.height / work_area.height,
    )


def within_tolerance(a: PixelRect, b: PixelRect,
                     tolerance: int = RESTORE_TOLERANCE) -> bool:
    """Two rectangles the user would call identical.

    Capturing to relative edges and resolving back rounds, so a window that
    never moved can differ from its own target by a pixel or two; a maximized
    window differs by its invisible resize border. Exact comparison therefore
    planned moves for an untouched desk and restore would visibly nudge every
    window it was asked to leave alone.
    """
    return (abs(a.x - b.x) <= tolerance and abs(a.y - b.y) <= tolerance
            and abs(a.width - b.width) <= tolerance
            and abs(a.height - b.height) <= tolerance)


def resolve_relative(rect: RelativeRect, work_area: PixelRect) -> PixelRect:
    """Resolve cumulative relative edges on a possibly different work area."""
    left = work_area.x + _round_half_away_from_zero(rect.x * work_area.width)
    top = work_area.y + _round_half_away_from_zero(rect.y * work_area.height)
    right = work_area.x + _round_half_away_from_zero(
        (rect.x + rect.width) * work_area.width)
    bottom = work_area.y + _round_half_away_from_zero(
        (rect.y + rect.height) * work_area.height)
    return PixelRect.from_edges(left, top, right, bottom)


def capture_preset(name: str, live_windows: Iterable[LiveWindow]) -> LayoutPreset:
    """Capture eligible live windows without changing desktop state."""
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValueError("preset name must not be blank")
    live = tuple(live_windows)
    monitors = _available_monitors(
        live_windows if isinstance(live_windows, LiveDesktop) else live)
    captured = tuple(
        PresetWindow(
            window.identity,
            window.monitor.stable_key,
            relative_rect(window.bounds, window.monitor.work_area),
            next(ordinal for (key, ordinal), monitor in monitors.items()
                 if key == window.monitor.stable_key.lower()
                 and monitor.work_area == window.monitor.work_area),
        )
        for window in live
        if window.monitor.stable_key
    )
    return LayoutPreset(cleaned_name, captured)


def _available_monitors(
        live_windows: Iterable[LiveWindow],
) -> dict[tuple[str, int], MonitorState]:
    if isinstance(live_windows, LiveDesktop):
        candidates = live_windows.monitors
    else:
        candidates = tuple(window.monitor for window in live_windows)

    unique: dict[tuple[str, PixelRect], MonitorState] = {}
    for monitor in candidates:
        if monitor.stable_key:
            unique.setdefault(
                (monitor.stable_key.lower(), monitor.work_area), monitor)

    grouped: dict[str, list[MonitorState]] = {}
    for (key, _), monitor in unique.items():
        grouped.setdefault(key, []).append(monitor)

    return {
        (key, ordinal): monitor
        for key, same_key in grouped.items()
        for ordinal, monitor in enumerate(sorted(
            same_key,
            key=lambda item: (
                item.work_area.x if item.virtual_x is None else item.virtual_x,
                item.work_area.y if item.virtual_y is None else item.virtual_y,
            ),
        ))
    }


def plan_restore(
        preset: LayoutPreset,
        live_windows: Iterable[LiveWindow],
) -> tuple[list[RestoreMove], list[UnmatchedWindow]]:
    """Purely plan moves and report every entry that cannot be restored.

    A missing monitor is never substituted.  Windows are assigned one-to-one
    using ``window_tracker``'s strongest-first durable identity matcher, with
    class agreement as the minimum acceptable strength.  Already-correct
    windows produce no move, making capture followed by planning a no-op.
    """
    live = tuple(live_windows)
    monitors = _available_monitors(
        live_windows if isinstance(live_windows, LiveDesktop) else live)
    eligible_indices: list[int] = []
    unmatched: list[UnmatchedWindow] = []
    for index, remembered in enumerate(preset.windows):
        monitor_ref = (remembered.monitor_key.lower(), remembered.monitor_ordinal)
        if monitor_ref not in monitors:
            reason = (MONITOR_INSTANCE_NOT_FOUND
                      if any(key == monitor_ref[0] for key, _ in monitors)
                      else MONITOR_NOT_FOUND)
            unmatched.append(UnmatchedWindow(remembered, reason))
        else:
            eligible_indices.append(index)

    assignments = assign_windows(
        [preset.windows[index].identity for index in eligible_indices],
        [window.identity for window in live],
        MatchStrength.CLASS,
    )
    moves: list[RestoreMove] = []
    for eligible_index, preset_index in enumerate(eligible_indices):
        remembered = preset.windows[preset_index]
        live_index = assignments.get(eligible_index)
        if live_index is None:
            unmatched.append(UnmatchedWindow(remembered, WINDOW_NOT_FOUND))
            continue
        candidate = live[live_index]
        monitor = monitors[(remembered.monitor_key.lower(),
                            remembered.monitor_ordinal)]
        target = resolve_relative(remembered.relative_rect, monitor.work_area)
        if not within_tolerance(candidate.bounds, target):
            moves.append(RestoreMove(candidate.handle, candidate.identity, target))
    return moves, unmatched


def apply_restore(
        moves: Iterable[RestoreMove],
        mover: Callable[[int, PixelRect], bool] | None = None,
) -> list[AppliedMove]:
    """Apply a previously computed plan through an injectable placement helper."""
    placement = mover if mover is not None else WindowService().place
    return [AppliedMove(move, bool(placement(move.handle, move.target)))
            for move in moves]


def restore(
        preset: LayoutPreset,
        live_windows: Iterable[LiveWindow],
        mover: Callable[[int, PixelRect], bool] | None = None,
) -> tuple[list[AppliedMove], list[UnmatchedWindow]]:
    """Plan first, then apply only the intended moves."""
    moves, unmatched = plan_restore(preset, live_windows)
    return apply_restore(moves, mover), unmatched


def _identity_to_data(identity: WindowIdentity) -> dict[str, Any]:
    return {
        "executablePath": identity.executable_path,
        "windowClass": identity.window_class,
        "title": identity.title,
        "appUserModelId": identity.app_user_model_id,
        "siblingIndex": identity.sibling_index,
    }


def _preset_to_data(preset: LayoutPreset) -> dict[str, Any]:
    return {
        "name": preset.name,
        "windows": [
            {
                "identity": _identity_to_data(window.identity),
                "monitorKey": window.monitor_key,
                "monitorOrdinal": window.monitor_ordinal,
                "relativeRect": dataclasses.asdict(window.relative_rect),
            }
            for window in preset.windows
        ],
    }


def _preset_from_data(value: Any) -> LayoutPreset | None:
    if not isinstance(value, dict) or not isinstance(value.get("name"), str):
        return None
    name = value["name"].strip()
    raw_windows = value.get("windows")
    if not name or not isinstance(raw_windows, list):
        return None
    windows: list[PresetWindow] = []
    try:
        for raw in raw_windows:
            identity = raw["identity"]
            relative = raw["relativeRect"]
            monitor_key = raw["monitorKey"]
            monitor_ordinal = int(raw.get("monitorOrdinal", 0))
            if (not isinstance(identity, dict)
                    or not isinstance(relative, dict)
                    or not isinstance(monitor_key, str)
                    or not monitor_key
                    or monitor_ordinal < 0):
                return None
            aumid = identity.get("appUserModelId")
            if aumid is not None and not isinstance(aumid, str):
                return None
            window_identity = WindowIdentity(
                executable_path=str(identity["executablePath"]),
                window_class=str(identity["windowClass"]),
                title=str(identity.get("title", "")),
                app_user_model_id=aumid,
                sibling_index=int(identity.get("siblingIndex", 0)),
            )
            window_rect = RelativeRect(
                float(relative["x"]), float(relative["y"]),
                float(relative["width"]), float(relative["height"]),
            )
            if not all(math.isfinite(number) for number in dataclasses.astuple(window_rect)):
                return None
            windows.append(PresetWindow(
                window_identity, monitor_key, window_rect, monitor_ordinal))
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return LayoutPreset(name, tuple(windows))


def load_presets(store: ConfigStore) -> list[LayoutPreset]:
    """Load only valid presets from this feature's settings section."""
    raw = store.get_feature_setting(FEATURE_ID, PRESETS_SETTING, [])
    if not isinstance(raw, list):
        return []
    return [preset for item in raw if (preset := _preset_from_data(item)) is not None]


def save_presets(store: ConfigStore, presets: Iterable[LayoutPreset]) -> None:
    store.set_feature_setting(
        FEATURE_ID, PRESETS_SETTING,
        [_preset_to_data(preset) for preset in presets],
    )


def save_preset(store: ConfigStore, preset: LayoutPreset) -> None:
    """Insert or replace a named preset, preserving the other saved presets."""
    presets = load_presets(store)
    for index, existing in enumerate(presets):
        if existing.name.lower() == preset.name.lower():
            presets[index] = preset
            break
    else:
        presets.append(preset)
    save_presets(store, presets)


# ---- translated C# zone geometry and assignment -------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class LayoutSlot:
    x: float
    y: float
    width: float
    height: float
    match_process: str | None = None
    match_title_contains: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ZonePreset:
    name: str
    slots: tuple[LayoutSlot, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class LayoutWindow:
    process_name: str
    title: str


_FRACTION_SCALE = 1_000_000


def _quantize(value: float) -> int:
    if math.isnan(value):
        return 0
    clamped = min(max(value, 0.0), 1.0)
    return math.floor(clamped * _FRACTION_SCALE + 0.5)


def _scale(quantized: int, extent: int) -> int:
    return (quantized * max(0, extent) + _FRACTION_SCALE // 2) // _FRACTION_SCALE


def resolve_slot(slot: LayoutSlot, work_area: PixelRect) -> PixelRect:
    left = _quantize(slot.x)
    top = _quantize(slot.y)
    right = max(left, _quantize(slot.x + slot.width))
    bottom = max(top, _quantize(slot.y + slot.height))
    return PixelRect.from_edges(
        work_area.x + _scale(left, work_area.width),
        work_area.y + _scale(top, work_area.height),
        work_area.x + _scale(right, work_area.width),
        work_area.y + _scale(bottom, work_area.height),
    )


def resolve_layout(preset: ZonePreset, work_area: PixelRect) -> list[PixelRect]:
    return [resolve_slot(slot, work_area) for slot in preset.slots]


class LayoutGeometry:
    resolve = staticmethod(resolve_layout)
    resolve_slot = staticmethod(resolve_slot)


class BuiltInLayouts:
    HALVES = "Halves"
    THIRDS = "Thirds"
    MAIN_SIDE = "Main + Side"
    QUARTERS = "Quarters"
    FOCUS = "Focus"
    _THIRD = 1.0 / 3.0
    ALL = (
        ZonePreset(HALVES, (
            LayoutSlot(0.0, 0.0, 0.5, 1.0),
            LayoutSlot(0.5, 0.0, 0.5, 1.0))),
        ZonePreset(THIRDS, (
            LayoutSlot(0.0, 0.0, _THIRD, 1.0),
            LayoutSlot(_THIRD, 0.0, _THIRD, 1.0),
            LayoutSlot(2.0 * _THIRD, 0.0, _THIRD, 1.0))),
        ZonePreset(MAIN_SIDE, (
            LayoutSlot(0.0, 0.0, 0.66, 1.0),
            LayoutSlot(0.66, 0.0, 0.34, 1.0))),
        ZonePreset(QUARTERS, (
            LayoutSlot(0.0, 0.0, 0.5, 0.5),
            LayoutSlot(0.5, 0.0, 0.5, 0.5),
            LayoutSlot(0.0, 0.5, 0.5, 0.5),
            LayoutSlot(0.5, 0.5, 0.5, 0.5))),
        ZonePreset(FOCUS, (LayoutSlot(0.1, 0.05, 0.8, 0.9),)),
    )
    FULL_PARTITIONS = tuple(preset for preset in ALL if preset.name != "Focus")

    @classmethod
    def find(cls, name: str) -> ZonePreset | None:
        return next((preset for preset in cls.ALL
                     if preset.name.lower() == name.lower()), None)


def has_rule(slot: LayoutSlot) -> bool:
    return bool((slot.match_process and slot.match_process.strip())
                or (slot.match_title_contains and slot.match_title_contains.strip()))


def _strip_executable_suffix(name: str) -> str:
    return name[:-4] if name.lower().endswith(".exe") else name


def slot_matches(slot: LayoutSlot, window: LayoutWindow) -> bool:
    if not has_rule(slot):
        return False
    if slot.match_process and slot.match_process.strip():
        expected = _strip_executable_suffix(slot.match_process.strip())
        actual = _strip_executable_suffix(window.process_name or "")
        if expected.lower() != actual.lower():
            return False
    return (not slot.match_title_contains
            or not slot.match_title_contains.strip()
            or slot.match_title_contains.strip().lower() in (window.title or "").lower())


def assign_slots(slots: Sequence[LayoutSlot],
                 windows_in_z_order: Sequence[LayoutWindow]) -> list[int]:
    assignment = [-1] * len(slots)
    claimed = [False] * len(windows_in_z_order)
    for slot_index, slot in enumerate(slots):
        if not has_rule(slot):
            continue
        for window_index, window in enumerate(windows_in_z_order):
            if not claimed[window_index] and slot_matches(slot, window):
                assignment[slot_index] = window_index
                claimed[window_index] = True
                break
    next_window = 0
    for slot_index in range(len(slots)):
        if assignment[slot_index] >= 0:
            continue
        while next_window < len(claimed) and claimed[next_window]:
            next_window += 1
        if next_window >= len(claimed):
            break
        assignment[slot_index] = next_window
        claimed[next_window] = True
    return assignment


class LayoutAssignment:
    has_rule = staticmethod(has_rule)
    matches = staticmethod(slot_matches)
    assign = staticmethod(assign_slots)


# ---- live, read-only capture ---------------------------------------------------


def _choose_monitor(bounds: PixelRect,
                    monitors: Sequence[MonitorState]) -> MonitorState | None:
    if not monitors:
        return None

    def rank(item: tuple[int, MonitorState]) -> tuple[int, int, int]:
        index, monitor = item
        area = monitor.work_area
        overlap_width = max(0, min(bounds.right, area.right) - max(bounds.left, area.left))
        overlap_height = max(0, min(bounds.bottom, area.bottom) - max(bounds.top, area.top))
        dx = max(bounds.left - area.right, area.left - bounds.right, 0)
        dy = max(bounds.top - area.bottom, area.top - bounds.bottom, 0)
        return -(overlap_width * overlap_height), dx * dx + dy * dy, index

    return min(enumerate(monitors), key=rank)[1]


def scan_live_desktop() -> LiveDesktop:
    """Read eligible top-level windows and their stable monitor work areas."""
    work_areas = enumerate_work_areas()
    identities = {identity.device_name.lower(): identity
                  for identity in attached_identities()}
    monitors: list[MonitorState] = []
    for work in work_areas:
        identity = identities.get(work.device_name.lower())
        stable_key = identity.stable_key if identity is not None else None
        if stable_key:
            monitors.append(MonitorState(
                stable_key,
                PixelRect(work.work_area.x, work.work_area.y,
                          work.work_area.width, work.work_area.height),
                identity.virtual_x,
                identity.virtual_y,
            ))

    service = WindowService()
    tracker = WindowTracker(service)
    tracker.scan()
    native = __import__("window_tracker")._load_native()
    own_process_id = os.getpid()
    live: list[LiveWindow] = []
    for tracked in tracker.windows:
        if (tracked.process_id == own_process_id
                or not tracked.identity.title.strip()
                or native["user32"].IsIconic(tracked.handle)):
            continue
        style = int(native["user32"].GetWindowLongPtrW(tracked.handle, -16))
        if not style & 0x00040000:  # WS_THICKFRAME: resizable top-level window
            continue
        measured = service.get_bounds(tracked.handle)
        if measured is None:
            continue
        bounds, _ = measured
        monitor = _choose_monitor(bounds, monitors)
        if monitor is not None:
            live.append(LiveWindow(tracked.handle, tracked.identity, bounds, monitor))
    return LiveDesktop(tuple(live), tuple(monitors))


def main() -> int:
    print("READ-ONLY PROBE: captures and plans only; no real window will be moved.")
    desktop = scan_live_desktop()
    preset = capture_preset("Live desk", desktop)
    print(f"PRESET name={preset.name!r} windows={len(preset.windows)}")
    for window in preset.windows:
        identity = window.identity
        rect = window.relative_rect
        print(
            "WINDOW "
            f"identity={identity.stable_key} "
            f"executable={identity.executable_path!r} "
            f"class={identity.window_class!r} title={identity.title!r} "
            f"sibling={identity.sibling_index} "
            f"monitor={window.monitor_key} "
            f"ordinal={window.monitor_ordinal} "
            f"relative=({rect.x:.6f},{rect.y:.6f},"
            f"{rect.width:.6f},{rect.height:.6f})"
        )
    moves, unmatched = plan_restore(preset, desktop)
    print(f"RESTORE PLAN moves={len(moves)} unmatched={len(unmatched)}")
    for move in moves:
        print(f"MOVE identity={move.identity.stable_key} target="
              f"({move.target.x},{move.target.y},{move.target.width},{move.target.height})")
    for item in unmatched:
        print(f"UNMATCHED identity={item.window.identity.stable_key} reason={item.reason}")
    print("PLAN no-op" if not moves and not unmatched else "PLAN requires-action")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
