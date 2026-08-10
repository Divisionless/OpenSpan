"""Independent per-monitor Spaces with a pure model and safe Windows fill.

Importing this module only declares data and types.  No desktop enumeration,
hook installation, or visibility change occurs until an explicit operation.
The command declarations are consumed by the application later; this module
does not bind keys or install a keyboard hook.
"""

from __future__ import annotations

import atexit
from collections.abc import Callable, Iterable, Mapping, Sequence
import ctypes
import dataclasses
import threading
import uuid
from typing import Any, Protocol

from monitor_identity import attached_identities
from settings_service import FeatureDeclaration, SettingsService
from window_tiling import enumerate_work_areas
from window_tracker import PixelRect, WindowService, WindowTracker


FEATURE_ID = "separate-spaces"
SPACES_PER_MONITOR_SETTING = "spacesPerMonitor"
NEXT_SPACE_COMMAND = "esotericos.spaces.next"
PREVIOUS_SPACE_COMMAND = "esotericos.spaces.previous"
MIN_SPACES = 1
MAX_SPACES = 16
DEFAULT_SPACES = 2
SW_HIDE = 0
SW_SHOWNA = 8


@dataclasses.dataclass(frozen=True, slots=True)
class CommandDeclaration:
    id: str
    title: str


COMMANDS = (
    CommandDeclaration(NEXT_SPACE_COMMAND, "Next space on this display"),
    CommandDeclaration(PREVIOUS_SPACE_COMMAND, "Previous space on this display"),
)

# Empty shortcut tuples declare the commands without binding either of them.
FEATURE_DECLARATION = FeatureDeclaration(
    FEATURE_ID,
    "Displays Have Separate Spaces",
    "Features",
    False,
    {NEXT_SPACE_COMMAND: (), PREVIOUS_SPACE_COMMAND: ()},
    {SPACES_PER_MONITOR_SETTING: DEFAULT_SPACES},
)


def clamp_spaces(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return DEFAULT_SPACES
    return max(MIN_SPACES, min(MAX_SPACES, value))


@dataclasses.dataclass(frozen=True, slots=True, order=True)
class MonitorId:
    """Stable panel key plus the left-to-right ordinal among identical panels."""

    stable_key: str
    ordinal: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.stable_key, str) or not self.stable_key.strip():
            raise ValueError("monitor stable key must be a non-empty string")
        if not isinstance(self.ordinal, int) or isinstance(self.ordinal, bool):
            raise TypeError("monitor ordinal must be an integer")
        if self.ordinal < 0:
            raise ValueError("monitor ordinal must be non-negative")
        object.__setattr__(self, "stable_key", self.stable_key.strip().lower())

    @property
    def label(self) -> str:
        return self.stable_key if self.ordinal == 0 else f"{self.stable_key}#{self.ordinal}"

    def __str__(self) -> str:
        return self.label


MonitorLike = MonitorId | tuple[str, int] | str


def monitor_id(value: MonitorLike) -> MonitorId:
    if isinstance(value, MonitorId):
        return value
    if isinstance(value, str):
        return MonitorId(value)
    if (isinstance(value, tuple) and len(value) == 2
            and isinstance(value[0], str)):
        return MonitorId(value[0], value[1])
    raise TypeError("monitor must be MonitorId, stable-key string, or (key, ordinal)")


@dataclasses.dataclass(frozen=True, slots=True)
class Workspace:
    id: str
    name: str
    ordinal: int


@dataclasses.dataclass(frozen=True, slots=True)
class WindowPlacement:
    workspace_id: str
    floating: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class MonitorWorkspaces:
    monitor: MonitorId
    workspaces: tuple[Workspace, ...]
    active_workspace_id: str


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    monitors: tuple[MonitorWorkspaces, ...]
    placements: Mapping[str, WindowPlacement]


@dataclasses.dataclass(frozen=True, slots=True)
class VisibilityPlan:
    windows_to_show: frozenset[str] = frozenset()
    windows_to_hide: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.windows_to_show.isdisjoint(self.windows_to_hide):
            raise ValueError("show and hide sets must be disjoint")


class WorkspaceModel:
    """Windows-free state and policy for independent per-display spaces."""

    def __init__(self) -> None:
        self._by_monitor: dict[MonitorId, list[Workspace]] = {}
        self._active_by_monitor: dict[MonitorId, str] = {}
        self._placements: dict[str, WindowPlacement] = {}
        self._workspace_monitor: dict[str, MonitorId] = {}

    @property
    def monitors(self) -> tuple[MonitorId, ...]:
        return tuple(self._by_monitor)

    def workspaces_on(self, monitor: MonitorLike) -> tuple[Workspace, ...]:
        return tuple(self._by_monitor.get(monitor_id(monitor), ()))

    def active_workspace_on(self, monitor: MonitorLike) -> str | None:
        return self._active_by_monitor.get(monitor_id(monitor))

    def monitor_of(self, workspace_id: str) -> MonitorId | None:
        return self._workspace_monitor.get(workspace_id)

    def placement_of(self, window_key: str) -> WindowPlacement | None:
        return self._placements.get(window_key)

    def add_monitor(self, monitor: MonitorLike,
                    workspace_count: int = 4) -> bool:
        identity = monitor_id(monitor)
        if identity in self._by_monitor:
            return False
        count = clamp_spaces(workspace_count)
        spaces = [
            Workspace(f"{identity.label}:{index}", f"Space {index + 1}", index)
            for index in range(count)
        ]
        self._by_monitor[identity] = spaces
        self._active_by_monitor[identity] = spaces[0].id
        for space in spaces:
            self._workspace_monitor[space.id] = identity
        return True

    def create_workspace(self, monitor: MonitorLike) -> Workspace | None:
        identity = monitor_id(monitor)
        spaces = self._by_monitor.get(identity)
        if spaces is None or len(spaces) >= MAX_SPACES:
            return None
        ordinal = len(spaces)
        space = Workspace(
            f"{identity.label}:{uuid.uuid4().hex}",
            f"Space {ordinal + 1}", ordinal)
        spaces.append(space)
        self._workspace_monitor[space.id] = identity
        return space

    def destroy_workspace(self, monitor: MonitorLike,
                          workspace_id: str) -> bool:
        identity = monitor_id(monitor)
        spaces = self._by_monitor.get(identity)
        if spaces is None or len(spaces) <= 1:
            return False
        target = next((item for item in spaces if item.id == workspace_id), None)
        if target is None:
            return False
        spaces.remove(target)
        self._workspace_monitor.pop(workspace_id, None)
        self._reindex(spaces)
        if self._active_by_monitor.get(identity) == workspace_id:
            self._active_by_monitor[identity] = spaces[0].id
        fallback = self._active_by_monitor[identity]
        for key, placement in tuple(self._placements.items()):
            if placement.workspace_id == workspace_id:
                self._placements[key] = dataclasses.replace(
                    placement, workspace_id=fallback)
        return True

    def move_workspace_to_monitor(self, workspace_id: str,
                                  target_monitor: MonitorLike) -> bool:
        target_identity = monitor_id(target_monitor)
        current = self._workspace_monitor.get(workspace_id)
        source = self._by_monitor.get(current) if current is not None else None
        target = self._by_monitor.get(target_identity)
        if source is None or target is None:
            return False
        space = next((item for item in source if item.id == workspace_id), None)
        if space is None:
            return False
        if current == target_identity:
            return True
        if len(source) <= 1:
            self.create_workspace(current)
        source.remove(space)
        if self._active_by_monitor.get(current) == workspace_id:
            self._active_by_monitor[current] = source[0].id
        moved = dataclasses.replace(
            space, ordinal=len(target), name=f"Space {len(target) + 1}")
        target.append(moved)
        self._workspace_monitor[workspace_id] = target_identity
        self._reindex(source)
        return True

    @staticmethod
    def _reindex(spaces: list[Workspace]) -> None:
        for index, space in enumerate(tuple(spaces)):
            spaces[index] = dataclasses.replace(
                space, ordinal=index, name=f"Space {index + 1}")

    def remove_monitor(self, monitor: MonitorLike) -> tuple[str, ...]:
        identity = monitor_id(monitor)
        spaces = self._by_monitor.pop(identity, None)
        if spaces is None:
            return ()
        self._active_by_monitor.pop(identity, None)
        workspace_ids = {space.id for space in spaces}
        return tuple(key for key, placement in self._placements.items()
                     if placement.workspace_id in workspace_ids)

    def switch_to(self, monitor: MonitorLike, workspace_id: str) -> bool:
        identity = monitor_id(monitor)
        spaces = self._by_monitor.get(identity)
        if spaces is None or all(item.id != workspace_id for item in spaces):
            return False
        self._active_by_monitor[identity] = workspace_id
        return True

    def switch_to_ordinal(self, monitor: MonitorLike, ordinal: int) -> bool:
        space = next((item for item in self.workspaces_on(monitor)
                      if item.ordinal == ordinal), None)
        return space is not None and self.switch_to(monitor, space.id)

    def step(self, monitor: MonitorLike, direction: int) -> VisibilityPlan | None:
        identity = monitor_id(monitor)
        spaces = self._by_monitor.get(identity)
        if not spaces:
            return None
        active = self._active_by_monitor[identity]
        current = next(index for index, item in enumerate(spaces)
                       if item.id == active)
        target = (current + direction) % len(spaces)
        self._active_by_monitor[identity] = spaces[target].id
        return self.visibility_plan(identity)

    def next_space(self, monitor: MonitorLike) -> VisibilityPlan | None:
        return self.step(monitor, 1)

    def previous_space(self, monitor: MonitorLike) -> VisibilityPlan | None:
        return self.step(monitor, -1)

    def assign(self, window_key: str, workspace_id: str,
               floating: bool = False) -> None:
        if workspace_id not in self._workspace_monitor:
            raise ValueError(f"unknown workspace {workspace_id!r}")
        self._placements[window_key] = WindowPlacement(workspace_id, floating)

    def assign_to_active(self, window_key: str, monitor: MonitorLike,
                         floating: bool = False) -> bool:
        active = self.active_workspace_on(monitor)
        if active is None:
            return False
        self.assign(window_key, active, floating)
        return True

    def rehome(self, window_key: str, monitor: MonitorLike) -> bool:
        identity = monitor_id(monitor)
        active = self._active_by_monitor.get(identity)
        if active is None:
            return False
        placement = self._placements.get(window_key)
        if (placement is not None
                and self._workspace_monitor.get(placement.workspace_id) == identity):
            return False
        self._placements[window_key] = WindowPlacement(
            active, placement.floating if placement is not None else False)
        return True

    def forget(self, window_key: str) -> None:
        self._placements.pop(window_key, None)

    def windows_on(self, workspace_id: str) -> tuple[str, ...]:
        return tuple(key for key, placement in self._placements.items()
                     if placement.workspace_id == workspace_id)

    def is_visible(self, window_key: str) -> bool:
        placement = self._placements.get(window_key)
        if placement is None:
            return True
        monitor = self._workspace_monitor.get(placement.workspace_id)
        if monitor is None or monitor not in self._by_monitor:
            return True
        return (placement.floating
                or self._active_by_monitor.get(monitor) == placement.workspace_id)

    def visibility_plan(self, monitor: MonitorLike | None = None) -> VisibilityPlan:
        identity = monitor_id(monitor) if monitor is not None else None
        show: set[str] = set()
        hide: set[str] = set()
        for key, placement in self._placements.items():
            owner = self._workspace_monitor.get(placement.workspace_id)
            if identity is not None and owner != identity:
                continue
            (show if self.is_visible(key) else hide).add(key)
        return VisibilityPlan(frozenset(show), frozenset(hide))

    def rescue_to(self, window_keys: Iterable[str],
                  target_monitor: MonitorLike) -> tuple[str, ...]:
        target = self.active_workspace_on(target_monitor)
        if target is None:
            return ()
        moved: list[str] = []
        for key in window_keys:
            existing = self._placements.get(key)
            self._placements[key] = WindowPlacement(
                target, existing.floating if existing is not None else False)
            moved.append(key)
        return tuple(moved)

    def remap_monitor(self, old_monitor: MonitorLike,
                      new_monitor: MonitorLike) -> None:
        old = monitor_id(old_monitor)
        new = monitor_id(new_monitor)
        if old == new:
            return
        spaces = self._by_monitor.pop(old, None)
        if spaces is None:
            return
        active = self._active_by_monitor.pop(old, None)
        self._by_monitor[new] = spaces
        if active is not None:
            self._active_by_monitor[new] = active
        for space in spaces:
            self._workspace_monitor[space.id] = new

    def snapshot(self) -> WorkspaceSnapshot:
        monitors = tuple(
            MonitorWorkspaces(identity, tuple(spaces),
                              self._active_by_monitor[identity])
            for identity, spaces in self._by_monitor.items()
        )
        return WorkspaceSnapshot(monitors, dict(self._placements))

    @classmethod
    def restore(cls, snapshot: WorkspaceSnapshot) -> WorkspaceModel:
        model = cls()
        for saved in snapshot.monitors:
            identity = monitor_id(saved.monitor)
            spaces = list(saved.workspaces)
            if not spaces:
                continue
            model._by_monitor[identity] = spaces
            valid_ids = {item.id for item in spaces}
            model._active_by_monitor[identity] = (
                saved.active_workspace_id
                if saved.active_workspace_id in valid_ids else spaces[0].id)
            for space in spaces:
                model._workspace_monitor[space.id] = identity
        for key, placement in snapshot.placements.items():
            if placement.workspace_id in model._workspace_monitor:
                model._placements[key] = placement
        return model


class VisibilityApplier(Protocol):
    def is_window(self, handle: int) -> bool: ...
    def is_window_visible(self, handle: int) -> bool: ...
    def show_window(self, handle: int, command: int) -> bool: ...


class WindowsVisibilityApplier:
    """The only native visibility fill; all calls are explicit and injectable."""

    def __init__(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.IsWindow.argtypes = [ctypes.c_void_p]
        user32.IsWindow.restype = ctypes.c_int
        user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        user32.IsWindowVisible.restype = ctypes.c_int
        user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.ShowWindow.restype = ctypes.c_int
        self._user32 = user32

    def is_window(self, handle: int) -> bool:
        return bool(self._user32.IsWindow(handle))

    def is_window_visible(self, handle: int) -> bool:
        return bool(self._user32.IsWindowVisible(handle))

    def show_window(self, handle: int, command: int) -> bool:
        return bool(self._user32.ShowWindow(handle, command))

    @staticmethod
    def identity_of(handle: int) -> tuple[int, str]:
        service = WindowService()
        return service.get_process_id(handle), service.get_class_name(handle)


@dataclasses.dataclass(frozen=True, slots=True)
class HiddenWindow:
    handle: int
    identity: Any = None


class WindowVisibilityController:
    """Own every successful hide and undo it without activating the window."""

    def __init__(self, applier: VisibilityApplier) -> None:
        self._applier = applier
        self._hidden: dict[int, HiddenWindow] = {}
        self._gate = threading.RLock()

    @property
    def hidden(self) -> tuple[HiddenWindow, ...]:
        with self._gate:
            return tuple(self._hidden.values())

    @property
    def hidden_handles(self) -> frozenset[int]:
        with self._gate:
            return frozenset(self._hidden)

    def is_hidden_by_us(self, handle: int) -> bool:
        with self._gate:
            return handle in self._hidden

    def _identity_of(self, handle: int) -> Any:
        resolver = getattr(self._applier, "identity_of", None)
        return resolver(handle) if callable(resolver) else None

    def _same_window(self, saved: HiddenWindow) -> bool:
        if not self._applier.is_window(saved.handle):
            return False
        current = self._identity_of(saved.handle)
        return saved.identity is None or current is None or current == saved.identity

    def hide(self, handle: int, identity: Any = None) -> bool:
        if not self._applier.is_window(handle):
            return False
        if not self._applier.is_window_visible(handle):
            return False
        saved = HiddenWindow(
            handle, self._identity_of(handle) if identity is None else identity)
        with self._gate:
            if handle in self._hidden:
                return False
            # Record before crossing the native boundary, so an exception cannot
            # create an unowned hidden window.
            self._hidden[handle] = saved
        try:
            changed = self._applier.show_window(handle, SW_HIDE)
        except BaseException:
            # It may have hidden before raising. Keep ownership so restore_all can retry.
            raise
        if not changed:
            with self._gate:
                self._hidden.pop(handle, None)
            return False
        return True

    def reveal(self, handle: int) -> bool:
        with self._gate:
            saved = self._hidden.get(handle)
        if saved is None:
            return False
        if self._same_window(saved):
            self._applier.show_window(handle, SW_SHOWNA)
        with self._gate:
            self._hidden.pop(handle, None)
        return True

    def restore_all(self) -> int:
        with self._gate:
            saved = tuple(self._hidden.values())
        revealed = 0
        first_error: BaseException | None = None
        for item in saved:
            try:
                if self._same_window(item):
                    self._applier.show_window(item.handle, SW_SHOWNA)
                    revealed += 1
                with self._gate:
                    self._hidden.pop(item.handle, None)
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error
        return revealed

    def forget(self, handle: int) -> None:
        """Forget only a window known to have vanished; never use this to reveal."""
        with self._gate:
            self._hidden.pop(handle, None)

    def prune(self) -> None:
        with self._gate:
            saved = tuple(self._hidden.values())
        for item in saved:
            if not self._same_window(item):
                self.forget(item.handle)

    def dispose(self) -> int:
        return self.restore_all()


@dataclasses.dataclass(frozen=True, slots=True)
class DesktopWindow:
    handle: int
    monitor: MonitorId
    identity: Any = None
    title: str = ""

    @property
    def key(self) -> str:
        return f"{self.handle:X}"


class SpacesModule:
    """Explicit lifecycle around the pure model and visibility ownership."""

    def __init__(
        self,
        *,
        visibility_factory: Callable[[], WindowVisibilityController] | None = None,
        settings_service: SettingsService | None = None,
    ) -> None:
        self.model = WorkspaceModel()
        self._visibility_factory = visibility_factory or (
            lambda: WindowVisibilityController(WindowsVisibilityApplier()))
        self._settings = settings_service
        self._visibility: WindowVisibilityController | None = None
        self._windows: dict[str, DesktopWindow] = {}
        self._spaces_per_monitor = DEFAULT_SPACES
        self._enabled = False
        self._releases: list[Callable[[], None]] = []
        self._shutdown_registered = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def visibility(self) -> WindowVisibilityController | None:
        return self._visibility

    def add_release(self, release: Callable[[], None]) -> None:
        """Record app-owned wiring for disable; this module never creates hooks."""
        if not callable(release):
            raise TypeError("release must be callable")
        self._releases.append(release)

    def enable(
        self,
        monitors: Iterable[MonitorLike],
        windows: Iterable[DesktopWindow] = (),
        *,
        spaces_per_monitor: int | None = None,
    ) -> bool:
        if self._enabled:
            return False
        configured = spaces_per_monitor
        if configured is None and self._settings is not None:
            configured = self._settings.get_setting(
                FEATURE_ID, SPACES_PER_MONITOR_SETTING)
        self._spaces_per_monitor = clamp_spaces(
            DEFAULT_SPACES if configured is None else configured)

        model = WorkspaceModel()
        for item in monitors:
            model.add_monitor(item, self._spaces_per_monitor)
        existing: dict[str, DesktopWindow] = {}
        for window in windows:
            if model.assign_to_active(window.key, window.monitor):
                existing[window.key] = window

        # The visibility boundary is constructed only after the harmless model
        # is complete. Existing windows all joined active spaces, so enable hides none.
        visibility = self._visibility_factory()
        self.model = model
        self._windows = existing
        self._visibility = visibility
        self._enabled = True
        if not self._shutdown_registered:
            atexit.register(self._shutdown)
            self._shutdown_registered = True
        return True

    def _shutdown(self) -> None:
        if self._enabled:
            self.disable()

    def _require_enabled(self) -> WindowVisibilityController:
        if not self._enabled or self._visibility is None:
            raise RuntimeError("separate spaces is disabled")
        return self._visibility

    def _apply(self, plan: VisibilityPlan | None) -> VisibilityPlan | None:
        if plan is None:
            return None
        visibility = self._require_enabled()
        for key in sorted(plan.windows_to_show):
            window = self._windows.get(key)
            if window is not None:
                visibility.reveal(window.handle)
        for key in sorted(plan.windows_to_hide):
            window = self._windows.get(key)
            if window is not None:
                visibility.hide(window.handle, window.identity)
        visibility.prune()
        return plan

    def next_space(self, monitor: MonitorLike) -> VisibilityPlan | None:
        self._require_enabled()
        return self._apply(self.model.next_space(monitor))

    def previous_space(self, monitor: MonitorLike) -> VisibilityPlan | None:
        self._require_enabled()
        return self._apply(self.model.previous_space(monitor))

    def switch_to_ordinal(self, monitor: MonitorLike,
                          ordinal: int) -> VisibilityPlan | None:
        self._require_enabled()
        if not self.model.switch_to_ordinal(monitor, ordinal):
            return None
        return self._apply(self.model.visibility_plan(monitor))

    def window_appeared(self, window: DesktopWindow) -> bool:
        self._require_enabled()
        if self.model.placement_of(window.key) is not None:
            return False
        if not self.model.assign_to_active(window.key, window.monitor):
            return False
        self._windows[window.key] = window
        return True

    def window_moved(self, window: DesktopWindow) -> bool:
        visibility = self._require_enabled()
        if visibility.is_hidden_by_us(window.handle):
            return False
        changed = self.model.rehome(window.key, window.monitor)
        self._windows[window.key] = window
        return changed

    def window_vanished(self, handle: int) -> None:
        visibility = self._require_enabled()
        key = f"{handle:X}"
        self.model.forget(key)
        self._windows.pop(key, None)
        visibility.forget(handle)

    def topology_changed(self, attached: Iterable[MonitorLike]) -> None:
        visibility = self._require_enabled()
        # Reveal first. No model rebuild or release is allowed to strand a window.
        visibility.restore_all()
        present = tuple(dict.fromkeys(monitor_id(item) for item in attached))
        for known in tuple(self.model.monitors):
            if known not in present:
                orphaned = self.model.remove_monitor(known)
                if present:
                    self.model.rescue_to(orphaned, present[0])
        for identity in present:
            if not self.model.workspaces_on(identity):
                self.model.add_monitor(identity, self._spaces_per_monitor)
        for identity in present:
            self._apply(self.model.visibility_plan(identity))

    def disable(self) -> bool:
        if not self._enabled:
            return False
        visibility = self._require_enabled()
        # This ordering is deliberate and tested: restore before releasing anything.
        visibility.restore_all()
        first_error: BaseException | None = None
        for release in tuple(self._releases):
            try:
                release()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        self._releases.clear()
        if self._shutdown_registered:
            atexit.unregister(self._shutdown)
            self._shutdown_registered = False
        self._visibility = None
        self._windows.clear()
        self._enabled = False
        if first_error is not None:
            raise first_error
        return True


@dataclasses.dataclass(frozen=True, slots=True)
class LiveMonitor:
    id: MonitorId
    device_name: str
    bounds: PixelRect
    is_primary: bool = False


def _live_monitors() -> tuple[LiveMonitor, ...]:
    identities = attached_identities()
    work_areas = {item.device_name.lower(): item
                  for item in enumerate_work_areas()}
    grouped: dict[str, list[Any]] = {}
    for identity in identities:
        grouped.setdefault(identity.stable_key or "no-stable-key", []).append(identity)
    ordinals: dict[str, int] = {}
    for key, same_key in grouped.items():
        for ordinal, identity in enumerate(sorted(
                same_key, key=lambda item: (item.virtual_x, item.virtual_y))):
            ordinals[identity.device_name.lower()] = ordinal

    result: list[LiveMonitor] = []
    for identity in identities:
        work = work_areas.get(identity.device_name.lower())
        if work is None:
            continue
        key = identity.stable_key or "no-stable-key"
        result.append(LiveMonitor(
            MonitorId(key, ordinals[identity.device_name.lower()]),
            identity.device_name,
            PixelRect(work.bounds.x, work.bounds.y,
                      work.bounds.width, work.bounds.height),
            work.is_primary,
        ))
    return tuple(result)


def _choose_monitor(bounds: PixelRect,
                    monitors: Sequence[LiveMonitor]) -> LiveMonitor | None:
    if not monitors:
        return None

    def rank(item: tuple[int, LiveMonitor]) -> tuple[int, int, int]:
        index, monitor = item
        area = monitor.bounds
        overlap_width = max(0, min(bounds.right, area.right)
                            - max(bounds.left, area.left))
        overlap_height = max(0, min(bounds.bottom, area.bottom)
                             - max(bounds.top, area.top))
        dx = max(bounds.left - area.right, area.left - bounds.right, 0)
        dy = max(bounds.top - area.bottom, area.top - bounds.bottom, 0)
        return -(overlap_width * overlap_height), dx * dx + dy * dy, index

    return min(enumerate(monitors), key=rank)[1]


def _live_windows(monitors: Sequence[LiveMonitor]) -> tuple[DesktopWindow, ...]:
    service = WindowService()
    tracker = WindowTracker(service)
    tracker.scan()
    result: list[DesktopWindow] = []
    for tracked in tracker.windows:
        measured = service.get_bounds(tracked.handle)
        if measured is None:
            continue
        display = _choose_monitor(measured[0], monitors)
        if display is None:
            continue
        result.append(DesktopWindow(
            tracked.handle,
            display.id,
            (tracked.process_id, tracked.identity.window_class),
            tracked.identity.title,
        ))
    return tuple(result)


def _pointer_monitor(monitors: Sequence[LiveMonitor]) -> LiveMonitor | None:
    if not monitors:
        return None
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    point = POINT()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    if user32.GetCursorPos(ctypes.byref(point)):
        for monitor in monitors:
            area = monitor.bounds
            if area.left <= point.x < area.right and area.top <= point.y < area.bottom:
                return monitor
    return next((item for item in monitors if item.is_primary), monitors[0])


def _window_description(window: DesktopWindow) -> str:
    title = window.title.replace("\r", " ").replace("\n", " ")
    return f"0x{window.handle:X} {title!r}"


def main() -> int:
    print("READ-ONLY PROBE: model simulation only; no real window will be hidden or shown.")
    monitors = _live_monitors()
    windows = _live_windows(monitors)
    model = WorkspaceModel()
    for monitor in monitors:
        model.add_monitor(monitor.id, DEFAULT_SPACES)
    for window in windows:
        model.assign_to_active(window.key, window.monitor)

    by_key = {window.key: window for window in windows}
    for monitor in monitors:
        spaces = model.workspaces_on(monitor.id)
        active = model.active_workspace_on(monitor.id)
        print(f"MONITOR device={monitor.device_name!r} "
              f"stable={monitor.id.stable_key} ordinal={monitor.id.ordinal}")
        print("  SPACES " + ", ".join(
            f"{space.ordinal + 1}:{space.name}[{space.id}]"
            + ("*" if space.id == active else "") for space in spaces))
        print(f"  ACTIVE {active}")

    pointer = _pointer_monitor(monitors)
    if pointer is None:
        print("POINTER monitor=none")
        plan = VisibilityPlan()
    else:
        print(f"POINTER monitor={pointer.device_name!r} "
              f"stable={pointer.id.stable_key} ordinal={pointer.id.ordinal}")
        plan = model.next_space(pointer.id) or VisibilityPlan()
    shown = [_window_description(by_key[key])
             for key in sorted(plan.windows_to_show) if key in by_key]
    hidden = [_window_description(by_key[key])
              for key in sorted(plan.windows_to_hide) if key in by_key]
    print(f"NEXT WOULD SHOW ({len(shown)}): " + (", ".join(shown) or "none"))
    print(f"NEXT WOULD HIDE ({len(hidden)}): " + (", ".join(hidden) or "none"))
    print("NO WINDOWS WERE HIDDEN OR SHOWN.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
