"""Explicit hotkey host wiring keyboard chords to safe window actions.

Importing this module and constructing either public class has no hook side
effects.  :meth:`HotkeyHost.start` is the sole hook-start boundary.
"""

from __future__ import annotations

import dataclasses
import importlib
import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from config_store import ConfigStore
from keyboard_interception import (
    ChordModifiers,
    KeyChord,
    KeySequence,
    KeyboardInterceptionService,
    KeyboardRouter,
    KeyboardRoutingVerdict,
    RawKeyboardEvent,
)
from settings_service import (
    FeatureDeclaration,
    FeatureRegistry,
    SettingsService,
)
from window_tiling import (
    Rect,
    TileDirection,
    TileRestoreTracker,
    TileZone,
    apply_size_constraints,
    approximately,
    compute,
    enumerate_work_areas,
    select_work_area,
    zone_for_direction,
)
from window_tracker import WindowRejection, WindowService


FEATURE_ID = "halves-quarters-keyboard-tiling"
CONSUMER_ID = "OpenSpan.HotkeyHost"

# Doug's mapping, which is the one that ships: Ctrl+Win+Alt plus the number
# whose numpad position IS the zone (7 8 9 / 4 5 6 / 1 2 3 laid over the
# screen). The C# reference used Win+Alt; the triple modifier is deliberate,
# because Win+Alt+digit collides with far more third-party software.
ZONE_COMMANDS: tuple[tuple[str, TileZone, str], ...] = (
    ("esotericos.tile.left-half", TileZone.LEFT_HALF,
     "Ctrl+Win+Alt+Numpad 4"),
    ("esotericos.tile.right-half", TileZone.RIGHT_HALF,
     "Ctrl+Win+Alt+Numpad 6"),
    ("esotericos.tile.top-half", TileZone.TOP_HALF,
     "Ctrl+Win+Alt+Numpad 8"),
    ("esotericos.tile.bottom-half", TileZone.BOTTOM_HALF,
     "Ctrl+Win+Alt+Numpad 2"),
    ("esotericos.tile.top-left", TileZone.TOP_LEFT,
     "Ctrl+Win+Alt+Numpad 7"),
    ("esotericos.tile.top-right", TileZone.TOP_RIGHT,
     "Ctrl+Win+Alt+Numpad 9"),
    ("esotericos.tile.bottom-left", TileZone.BOTTOM_LEFT,
     "Ctrl+Win+Alt+Numpad 1"),
    ("esotericos.tile.bottom-right", TileZone.BOTTOM_RIGHT,
     "Ctrl+Win+Alt+Numpad 3"),
)

# The same digits on the number ROW, so the mapping works on a keyboard with
# no numpad without asking anyone to learn a second layout.
TOP_ROW_DIGITS: dict[str, str] = {
    "esotericos.tile.left-half": "Ctrl+Win+Alt+4",
    "esotericos.tile.right-half": "Ctrl+Win+Alt+6",
    "esotericos.tile.top-half": "Ctrl+Win+Alt+8",
    "esotericos.tile.bottom-half": "Ctrl+Win+Alt+2",
    "esotericos.tile.top-left": "Ctrl+Win+Alt+7",
    "esotericos.tile.top-right": "Ctrl+Win+Alt+9",
    "esotericos.tile.bottom-left": "Ctrl+Win+Alt+1",
    "esotericos.tile.bottom-right": "Ctrl+Win+Alt+3",
}
RESTORE_COMMAND = "esotericos.tile.restore"
REFINE_COMMANDS: tuple[tuple[str, TileDirection, str], ...] = (
    ("esotericos.tile.refine-left", TileDirection.LEFT, "Win+Left"),
    ("esotericos.tile.refine-right", TileDirection.RIGHT, "Win+Right"),
    ("esotericos.tile.refine-up", TileDirection.UP, "Win+Up"),
    ("esotericos.tile.refine-down", TileDirection.DOWN, "Win+Down"),
)

# Arrow equivalents for the halves, for reaching a zone without hunting for a
# digit. Quarters stay digit-only: a four-modifier arrow chord is worse than
# the number whose position already means the corner.
LAPTOP_SHORTCUTS: dict[str, str] = {
    "esotericos.tile.left-half": "Ctrl+Win+Alt+Left",
    "esotericos.tile.right-half": "Ctrl+Win+Alt+Right",
    "esotericos.tile.top-half": "Ctrl+Win+Alt+Up",
    "esotericos.tile.bottom-half": "Ctrl+Win+Alt+Down",
    RESTORE_COMMAND: "Ctrl+Win+Alt+Backspace",
}

# Space switching, straight from the old program: Alt+<n> jumps to space n
# on the display under the pointer. Doug's muscle memory runs on Alt+1/Alt+2,
# and these chords are the ONLY way back to a hidden space from the keyboard,
# so they ship bound whenever Spaces can be enabled at all.
SPACE_SWITCH_COMMANDS: tuple[tuple[str, int, str], ...] = tuple(
    (f"esotericos.spaces.switch-{n}", n - 1, f"Alt+{n}") for n in range(1, 10))
NEXT_SPACE_COMMAND = "esotericos.spaces.next"
PREVIOUS_SPACE_COMMAND = "esotericos.spaces.previous"

DEFAULT_SHORTCUTS = {
    command: tuple(chord for chord in (
        reference, TOP_ROW_DIGITS.get(command),
        LAPTOP_SHORTCUTS.get(command)) if chord)
    for command, _argument, reference in (*ZONE_COMMANDS, *REFINE_COMMANDS)
}
for _command, _ordinal, _chord in SPACE_SWITCH_COMMANDS:
    DEFAULT_SHORTCUTS[_command] = (_chord,)
DEFAULT_SHORTCUTS[RESTORE_COMMAND] = (
    "Ctrl+Win+Alt+Numpad 5", "Ctrl+Win+Alt+5",
    LAPTOP_SHORTCUTS[RESTORE_COMMAND])

FEATURE_DECLARATION = FeatureDeclaration(
    FEATURE_ID,
    "Halves and quarters keyboard tiling",
    "Shortcuts",
    False,
    DEFAULT_SHORTCUTS,
    {"interceptWinArrows": True},
    ("WH_KEYBOARD_LL through the central KeyboardInterceptionService",),
)


@dataclasses.dataclass(frozen=True, slots=True)
class ActionResult:
    """A compact, inspectable outcome for every window verb."""

    command: str
    performed: bool
    reason: str
    handle: int | None = None
    before: Rect | None = None
    after: Rect | None = None
    available: bool = True
    details: Any = None


@dataclasses.dataclass(frozen=True, slots=True)
class HostResult:
    operation: str
    performed: bool
    reason: str


@dataclasses.dataclass(frozen=True, slots=True)
class _Focused:
    handle: int
    bounds: Rect
    work_area: Rect


def _default_foreground_window() -> int:
    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wt.HWND
    user32.GetForegroundWindow.argtypes = []
    return int(user32.GetForegroundWindow() or 0)


def _default_size_constraints(_handle: int) -> tuple[int, int, int, int]:
    # The landed WindowService has no WM_GETMINMAXINFO surface.  Keeping the
    # collaborator explicit lets that later fill supply real constraints while
    # all targets still pass through the landed clamping policy today.
    maximum = 2**31 - 1
    return 0, 0, maximum, maximum


def _as_rect(value: Any) -> Rect:
    return Rect(value.x, value.y, value.width, value.height)


class WindowActions:
    """Individually callable window verbs with all effects injectable."""

    def __init__(
            self,
            window_service: Any | None = None,
            *,
            foreground: Callable[[], int] | None = None,
            monitors: Callable[[], Sequence[Any]] | None = None,
            constraints: Callable[[int], tuple[int, int, int, int]] | None = None,
            placer: Callable[[int, Rect], bool] | None = None,
            tracker: TileRestoreTracker | None = None,
            settings_service: SettingsService | None = None,
            store: ConfigStore | None = None,
            module_loader: Callable[[str], Any] | None = None,
            own_process_id: int | None = None,
    ) -> None:
        self.window_service = (window_service if window_service is not None
                               else WindowService())
        self._foreground = (foreground if foreground is not None
                            else _default_foreground_window)
        self._monitors = monitors if monitors is not None else enumerate_work_areas
        self._constraints = (constraints if constraints is not None
                             else _default_size_constraints)
        self._placer = placer if placer is not None else self.window_service.place
        self.tracker = tracker if tracker is not None else TileRestoreTracker()
        self.settings_service = settings_service
        self.store = (store if store is not None else
                      (settings_service.store if settings_service is not None
                       else ConfigStore()))
        self._module_loader = (module_loader if module_loader is not None
                               else importlib.import_module)
        self._own_process_id = (os.getpid() if own_process_id is None
                                else own_process_id)

    def _result(self, command: str, reason: str, **values: Any) -> ActionResult:
        return ActionResult(command, False, reason, **values)

    def _is_ours(self, handle: int) -> bool:
        try:
            process_id = self.window_service.get_process_id(handle)
            return not process_id or process_id == self._own_process_id
        except (OSError, RuntimeError):
            return True

    def _focused(self, command: str) -> tuple[_Focused | None, ActionResult | None]:
        try:
            handle = int(self._foreground() or 0)
        except (OSError, RuntimeError) as exc:
            return None, self._result(command, f"foreground lookup failed: {exc}")
        if not handle:
            return None, self._result(command, "nothing focused")
        if self._is_ours(handle):
            return None, self._result(
                command, "focused window belongs to this process", handle=handle)
        try:
            rejection = self.window_service.classify(handle)
        except (OSError, RuntimeError) as exc:
            return None, self._result(
                command, f"focused window lookup failed: {exc}", handle=handle)
        if rejection != WindowRejection.NONE:
            name = getattr(rejection, "name", str(rejection)).lower()
            return None, self._result(
                command, f"focused window rejected: {name}", handle=handle)
        measured = self.window_service.get_bounds(handle)
        if measured is None:
            return None, self._result(
                command, "focused window bounds unavailable", handle=handle)
        bounds = _as_rect(measured[0] if isinstance(measured, tuple) else measured)
        try:
            work_area = select_work_area(bounds, tuple(self._monitors()))
        except (OSError, RuntimeError) as exc:
            return None, self._result(
                command, f"monitor lookup failed: {exc}", handle=handle,
                before=bounds)
        if work_area is None:
            return None, self._result(
                command, "no monitor work area", handle=handle, before=bounds)
        return _Focused(handle, bounds, work_area), None

    def _constrained(self, focused: _Focused, target: Rect) -> Rect:
        minimum_width, minimum_height, maximum_width, maximum_height = (
            self._constraints(focused.handle))
        return apply_size_constraints(
            target, focused.work_area,
            minimum_width, minimum_height, maximum_width, maximum_height)

    def _place(self, command: str, focused: _Focused, target: Rect,
               *, zone: TileZone | None = None) -> ActionResult:
        try:
            target = self._constrained(focused, target)
            placed = bool(self._placer(focused.handle, target))
        except (OSError, RuntimeError, ValueError) as exc:
            return self._result(
                command, f"placement failed: {exc}", handle=focused.handle,
                before=focused.bounds, after=target)
        if not placed:
            return self._result(
                command, "placement refused", handle=focused.handle,
                before=focused.bounds, after=target)
        if zone is not None:
            self.tracker.on_tiled(focused.handle, focused.bounds, zone)
        return ActionResult(
            command, True, "placed", focused.handle, focused.bounds, target)

    def tile_focused(self, zone: TileZone) -> ActionResult:
        command = "tile_focused"
        if not isinstance(zone, TileZone):
            return self._result(command, f"unknown zone: {zone!r}")
        focused, stopped = self._focused(command)
        if stopped is not None:
            return stopped
        assert focused is not None
        known = self.tracker.get_current_zone(focused.handle)
        if (known is not None and not approximately(
                focused.bounds, compute(focused.work_area, known))):
            self.tracker.invalidate(focused.handle)
        return self._place(
            command, focused, compute(focused.work_area, zone), zone=zone)

    def refine_focused(self, direction: TileDirection) -> ActionResult:
        command = "refine_focused"
        if not isinstance(direction, TileDirection):
            return self._result(command, f"unknown direction: {direction!r}")
        focused, stopped = self._focused(command)
        if stopped is not None:
            return stopped
        assert focused is not None
        known = self.tracker.get_current_zone(focused.handle)
        if (known is not None and not approximately(
                focused.bounds, compute(focused.work_area, known))):
            self.tracker.invalidate(focused.handle)
            known = None
        zone = zone_for_direction(direction, known)
        return self._place(
            command, focused, compute(focused.work_area, zone), zone=zone)

    def restore_focused(self) -> ActionResult:
        command = "restore_focused"
        focused, stopped = self._focused(command)
        if stopped is not None:
            return stopped
        assert focused is not None
        original = self.tracker.try_restore(focused.handle)
        if original is None:
            return self._result(
                command, "no stored bounds", handle=focused.handle,
                before=focused.bounds)
        try:
            placed = bool(self._placer(focused.handle, original))
        except (OSError, RuntimeError) as exc:
            return self._result(
                command, f"restore failed: {exc}", handle=focused.handle,
                before=focused.bounds, after=original)
        if not placed:
            return self._result(
                command, "restore refused", handle=focused.handle,
                before=focused.bounds, after=original)
        return ActionResult(
            command, True, "restored", focused.handle,
            focused.bounds, original)

    def center_focused(self) -> ActionResult:
        command = "center_focused"
        focused, stopped = self._focused(command)
        if stopped is not None:
            return stopped
        assert focused is not None
        sized = self._constrained(
            focused, Rect(focused.work_area.x, focused.work_area.y,
                          focused.bounds.width, focused.bounds.height))
        target = Rect(
            focused.work_area.x + (focused.work_area.width - sized.width) // 2,
            focused.work_area.y + (focused.work_area.height - sized.height) // 2,
            sized.width, sized.height)
        return self._place(command, focused, target)

    def _optional_module(self, name: str, command: str) -> tuple[Any | None,
                                                                  ActionResult | None]:
        try:
            return self._module_loader(name), None
        except (ImportError, ModuleNotFoundError) as exc:
            return None, ActionResult(
                command, False, f"{name} unavailable: {exc}", available=False)

    def _safe_live_windows(self, desktop: Any) -> Any:
        windows = tuple(window for window in desktop
                        if not self._is_ours(window.handle))
        if hasattr(desktop, "monitors"):
            return type(desktop)(windows, desktop.monitors)
        return windows

    def apply_rules_now(self) -> ActionResult:
        command = "apply_rules_now"
        rules, stopped = self._optional_module("window_rules", command)
        if stopped is not None:
            return stopped
        assert rules is not None
        try:
            rule_set = rules.load_rules(self.store)
            if rule_set.problems:
                return self._result(command, "rules are invalid",
                                    details=rule_set.problems)
            from window_tracker import WindowTracker

            tracker = WindowTracker(self.window_service)
            tracker.scan()
            applied = 0
            refused = 0
            for window in tracker.windows:
                if self._is_ours(window.handle):
                    continue
                action = rules.resolve(
                    rule_set.rules, rules.WindowFacts.from_tracked(window))
                if action is None:
                    continue
                if rules.apply_action(window.handle, action,
                                      self._rule_mover(rules)):
                    applied += 1
                else:
                    refused += 1
            return ActionResult(
                command, bool(applied),
                "rules applied" if applied else "no matching rules",
                details={"applied": applied, "refused": refused})
        except (OSError, RuntimeError, ValueError) as exc:
            return self._result(command, f"rule application failed: {exc}")

    def _rule_mover(self, rules: Any) -> Callable[[int, Any], bool]:
        def move(handle: int, action: Any) -> bool:
            if self._is_ours(handle):
                return False
            measured = self.window_service.get_bounds(handle)
            if measured is None:
                return False
            bounds = _as_rect(measured[0] if isinstance(measured, tuple) else measured)
            monitors = tuple(self._monitors())
            work = select_work_area(bounds, monitors)
            if work is None:
                return False
            if action.kind is rules.RuleAction.ZONE:
                target = rules.zone_rect(action, work)
            elif action.kind is rules.RuleAction.RECT:
                proxy = rules.WindowRule(
                    x=action.x, y=action.y, width=action.width,
                    height=action.height)
                target = rules.try_resolve_rect(proxy, work)
            elif action.kind is rules.RuleAction.MONITOR:
                target_work = self._work_area_for_key(action.monitor_key, monitors)
                target = (rules.move_between_work_areas(bounds, work, target_work)
                          if target_work is not None else None)
            elif action.kind is rules.RuleAction.MAXIMIZE:
                return self._maximize(handle)
            else:
                return True
            return target is not None and bool(self._placer(handle, _as_rect(target)))

        return move

    @staticmethod
    def _work_area_for_key(key: str | None,
                           monitors: Sequence[Any]) -> Rect | None:
        if not key:
            return None
        lowered = key.lower()
        if lowered == "primary":
            primary = next((item for item in monitors if item.is_primary), None)
            return primary.work_area if primary is not None else None
        try:
            from monitor_identity import attached_identities

            identities = {item.device_name.lower(): item
                          for item in attached_identities()}
            for monitor in monitors:
                identity = identities.get(monitor.device_name.lower())
                if identity is not None and identity.stable_key == lowered:
                    return monitor.work_area
        except (OSError, RuntimeError):
            return None
        return None

    @staticmethod
    def _maximize(handle: int) -> bool:
        import ctypes
        import ctypes.wintypes as wt

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.ShowWindow.restype = wt.BOOL
        user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
        user32.ShowWindow(handle, 3)
        return True

    def save_preset(self, name: str) -> ActionResult:
        command = "save_preset"
        presets, stopped = self._optional_module("layout_presets", command)
        if stopped is not None:
            return stopped
        assert presets is not None
        try:
            desktop = self._safe_live_windows(presets.scan_live_desktop())
            preset = presets.capture_preset(name, desktop)
            presets.save_preset(self.store, preset)
            return ActionResult(
                command, True, "preset saved",
                details={"name": preset.name, "windows": len(preset.windows)})
        except (OSError, RuntimeError, ValueError) as exc:
            return self._result(command, f"preset save failed: {exc}")

    def restore_preset(self, name: str) -> ActionResult:
        command = "restore_preset"
        presets, stopped = self._optional_module("layout_presets", command)
        if stopped is not None:
            return stopped
        assert presets is not None
        cleaned = name.strip()
        if not cleaned:
            return self._result(command, "preset name is blank")
        try:
            preset = next((item for item in presets.load_presets(self.store)
                           if item.name.lower() == cleaned.lower()), None)
            if preset is None:
                return self._result(command, "preset not found",
                                    details={"name": cleaned})
            desktop = self._safe_live_windows(presets.scan_live_desktop())

            def safe_place(handle: int, target: Any) -> bool:
                return (not self._is_ours(handle)
                        and bool(self._placer(handle, _as_rect(target))))

            applied, unmatched = presets.restore(preset, desktop, safe_place)
            succeeded = sum(item.succeeded for item in applied)
            details = {"name": preset.name, "moved": succeeded,
                       "refused": len(applied) - succeeded,
                       "unmatched": len(unmatched)}
            return ActionResult(
                command, bool(succeeded),
                "preset restored" if succeeded else "no windows moved",
                details=details)
        except (OSError, RuntimeError, ValueError) as exc:
            return self._result(command, f"preset restore failed: {exc}")


class _BindingConsumer:
    consumer_id = CONSUMER_ID
    priority = 50

    def __init__(self, actions: WindowActions,
                 bindings: Mapping[KeyChord, Callable[[], ActionResult]]) -> None:
        self._actions = actions
        self._bindings = dict(bindings)
        self.last_result: ActionResult | None = None

    def process_key_event(
            self, event: RawKeyboardEvent,
            current_modifiers: ChordModifiers) -> KeyboardRoutingVerdict:
        if not event.is_down:
            return KeyboardRoutingVerdict.pass_through()
        action = self._bindings.get(KeyChord(current_modifiers,
                                             event.canonical_key))
        if action is None:
            return KeyboardRoutingVerdict.pass_through()

        def invoke() -> None:
            self.last_result = action()

        return KeyboardRoutingVerdict.swallow_with_action(invoke, self.consumer_id)


class HotkeyHost:
    """Own one central-service consumer and an explicit hook lifetime."""

    def __init__(self, actions: WindowActions,
                 router: KeyboardRouter | None = None,
                 service: Any | None = None,
                 settings_service: SettingsService | None = None) -> None:
        self.actions = actions
        if service is None:
            self.router = router if router is not None else KeyboardRouter()
            self.service = KeyboardInterceptionService(self.router)
        else:
            self.service = service
            self.router = (router if router is not None else
                           getattr(service, "router", KeyboardRouter()))
        self.settings_service = (settings_service or actions.settings_service
                                 or self._default_settings(actions.store))
        self._ensure_declaration(self.settings_service.registry)
        self._running = False
        self._consumer: _BindingConsumer | None = None

    @staticmethod
    def _default_settings(store: ConfigStore) -> SettingsService:
        return SettingsService(store, FeatureRegistry([FEATURE_DECLARATION]))

    @staticmethod
    def _ensure_declaration(registry: FeatureRegistry) -> None:
        try:
            registry.get(FEATURE_ID)
        except KeyError:
            registry.register(FEATURE_DECLARATION)

    @property
    def is_running(self) -> bool:
        return self._running

    def bindings(self) -> dict[str, str]:
        table: dict[str, str] = {}
        effective = self.settings_service.effective_shortcuts()
        for command, sequences in effective.items():
            if command not in DEFAULT_SHORTCUTS:
                continue
            for text in sequences:
                try:
                    sequence = KeySequence.parse(text)
                except ValueError:
                    continue
                if sequence.is_single:
                    table[str(sequence)] = command
        return table

    def collisions(self) -> tuple[Any, ...]:
        return self.settings_service.shortcut_collisions()

    def _action_for(self, command: str) -> Callable[[], ActionResult]:
        for command_id, zone, _chord in ZONE_COMMANDS:
            if command == command_id:
                return lambda zone=zone: self.actions.tile_focused(zone)
        for command_id, direction, _chord in REFINE_COMMANDS:
            if command == command_id:
                return lambda direction=direction: self.actions.refine_focused(direction)
        if command == RESTORE_COMMAND:
            return self.actions.restore_focused
        for command_id, ordinal, _chord in SPACE_SWITCH_COMMANDS:
            if command == command_id:
                return lambda ordinal=ordinal: self.switch_space(ordinal)
        raise KeyError(command)

    # The app attaches the live Spaces module here. Until it does, the chord
    # is bound but inert -- it reports rather than pretending, and it never
    # raises inside the hook.
    switch_space_hook: Callable[[int], Any] | None = None

    def switch_space(self, ordinal: int) -> ActionResult:
        hook = self.switch_space_hook
        if hook is None:
            return ActionResult(f"esotericos.spaces.switch-{ordinal + 1}",
                                False, "separate Spaces is not enabled", {})
        try:
            hook(ordinal)
        except Exception as exc:  # noqa: BLE001
            return ActionResult(f"esotericos.spaces.switch-{ordinal + 1}",
                                False, f"space switch failed: {exc}", {})
        return ActionResult(f"esotericos.spaces.switch-{ordinal + 1}",
                            True, "switched space", {"ordinal": ordinal})

    def start(self) -> HostResult:
        if self._running:
            return HostResult("start", False, "already running")
        if bool(getattr(self.service, "is_hook_installed", False)):
            return HostResult("start", False, "a hook owner already exists")
        parsed: dict[KeyChord, Callable[[], ActionResult]] = {}
        for chord, command in self.bindings().items():
            parsed[KeySequence.parse(chord).first] = self._action_for(command)
        consumer = _BindingConsumer(self.actions, parsed)
        self.service.register_consumer(consumer)
        try:
            self.service.start()
        except (OSError, PermissionError, RuntimeError, TimeoutError) as exc:
            self.service.unregister_consumer(consumer.consumer_id)
            return HostResult("start", False, str(exc))
        self._consumer = consumer
        self._running = True
        return HostResult("start", True, "started")

    def stop(self) -> HostResult:
        if not self._running:
            return HostResult("stop", False, "not running")
        problem: str | None = None
        try:
            self.service.stop()
        except (OSError, RuntimeError, TimeoutError) as exc:
            problem = str(exc)
        finally:
            if self._consumer is not None:
                self.service.unregister_consumer(self._consumer.consumer_id)
            self._consumer = None
            self._running = False
        if problem is not None:
            return HostResult("stop", False, problem)
        return HostResult("stop", True, "stopped")


def _probe_window_worker(connection: Any) -> None:
    import ctypes
    import ctypes.wintypes as wt
    import time

    native = ctypes.WinDLL("user32", use_last_error=True)
    native.CreateWindowExW.restype = wt.HWND
    native.CreateWindowExW.argtypes = [
        wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
    native.DestroyWindow.restype = wt.BOOL
    native.DestroyWindow.argtypes = [wt.HWND]
    native.PeekMessageW.restype = wt.BOOL
    native.PeekMessageW.argtypes = [
        ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
    native.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
    native.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]

    hwnd = int(native.CreateWindowExW(
        0, "STATIC", "OpenSpan hotkey-host probe", 0x10CF0000,
        -30000, -30000, 320, 220, None, None, None, None) or 0)
    connection.send(hwnd)
    try:
        message = wt.MSG()
        while not connection.poll():
            while native.PeekMessageW(
                    ctypes.byref(message), None, 0, 0, 0x0001):
                native.TranslateMessage(ctypes.byref(message))
                native.DispatchMessageW(ctypes.byref(message))
            time.sleep(0.01)
        connection.recv()
    except EOFError:
        pass
    finally:
        if hwnd:
            native.DestroyWindow(hwnd)
        connection.close()


def _probe() -> int:
    import multiprocessing
    import tempfile

    parent, child = multiprocessing.Pipe()
    window_process = multiprocessing.Process(
        target=_probe_window_worker, args=(child,), daemon=True)
    window_process.start()
    child.close()
    hwnd = int(parent.recv()) if parent.poll(5.0) else 0
    if not hwnd:
        print("PROBE window=create-failed")
        parent.close()
        window_process.terminate()
        window_process.join(2.0)
        return 1
    try:
        service = WindowService()
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(directory)
            settings = SettingsService(
                store, FeatureRegistry([FEATURE_DECLARATION]))
            actions = WindowActions(
                service, foreground=lambda: hwnd, store=store,
                settings_service=settings)
            host = HotkeyHost(actions, settings_service=settings)
            print("BINDINGS")
            for chord, command in host.bindings().items():
                print(f"  {chord} -> {command}")
            print("COLLISIONS")
            collisions = host.collisions()
            if collisions:
                for collision in collisions:
                    print(f"  {collision.kind}: {collision.message}")
            else:
                print("  (none)")
            before = _as_rect(service.get_bounds(hwnd)[0])
            tiled = actions.tile_focused(TileZone.LEFT_HALF)
            after_tile = _as_rect(service.get_bounds(hwnd)[0])
            refined = actions.refine_focused(TileDirection.UP)
            after_refine = _as_rect(service.get_bounds(hwnd)[0])
            print(f"WINDOW before={before}")
            print(f"TILE performed={tiled.performed} after={after_tile}")
            print(f"REFINE performed={refined.performed} after={after_refine}")
            started = host.start()
            print(f"HOST start={started.performed} is_running={host.is_running}")
            stopped = host.stop()
            print(f"HOST stop={stopped.performed} is_running={host.is_running}")
        return 0
    finally:
        try:
            parent.send("destroy")
        except (BrokenPipeError, EOFError, OSError):
            pass
        parent.close()
        window_process.join(2.0)
        if window_process.is_alive():
            window_process.terminate()
            window_process.join(2.0)


if __name__ == "__main__":
    raise SystemExit(_probe())
