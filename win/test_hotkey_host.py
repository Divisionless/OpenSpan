"""Pure fake-driven and structural checks for hotkey_host.py."""

import ast
import pathlib
import tempfile
from types import SimpleNamespace

import hotkey_host as host
from config_store import ConfigStore
from keyboard_interception import (
    ChordModifiers,
    KeySequence,
    KeyboardRouter,
    KeyboardRoutingVerdictKind,
    RawKeyboardEvent,
)
from settings_service import FeatureRegistry, SettingsService
from window_tiling import MonitorWorkArea, Rect, TileDirection, TileZone
from window_tracker import WindowRejection


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


WORK = Rect(0, 0, 1000, 800)
MONITORS = (MonitorWorkArea(1, WORK, WORK, True, "DISPLAY1"),)


class FakeWindows:
    def __init__(self):
        self.handle = 10
        self.pid = 7
        self.rejection = WindowRejection.NONE
        self.bounds = Rect(100, 100, 400, 300)
        self.place_result = True
        self.placements = []

    def get_process_id(self, handle):
        return self.pid

    def classify(self, handle):
        return self.rejection

    def get_bounds(self, handle):
        return None if self.bounds is None else (self.bounds, object())

    def place(self, handle, target):
        self.placements.append((handle, target))
        if self.place_result:
            self.bounds = target
        return self.place_result


def make_actions(windows=None, foreground=None, monitors=None,
                 constraints=None, module_loader=None):
    windows = windows or FakeWindows()
    temporary = tempfile.TemporaryDirectory()
    store = ConfigStore(temporary.name)
    settings = SettingsService(
        store, FeatureRegistry([host.FEATURE_DECLARATION]))
    actions = host.WindowActions(
        windows,
        foreground=(foreground if foreground is not None
                    else lambda: windows.handle),
        monitors=(monitors if monitors is not None else lambda: MONITORS),
        constraints=(constraints if constraints is not None
                     else lambda _handle: (0, 0, 2**31 - 1, 2**31 - 1)),
        store=store,
        settings_service=settings,
        module_loader=module_loader,
        own_process_id=99,
    )
    actions._test_temporary = temporary
    return actions, windows, settings


# ---- focused-window safety and tiling paths ----------------------------------

actions, windows, _settings = make_actions(foreground=lambda: 0)
result = actions.tile_focused(TileZone.LEFT_HALF)
check("tile reports nothing focused", not result.performed
      and result.reason == "nothing focused")

actions, windows, _settings = make_actions()
windows.pid = 99
result = actions.tile_focused(TileZone.LEFT_HALF)
check("tile refuses a window owned by this process",
      not result.performed and "belongs to this process" in result.reason
      and not windows.placements)

actions, windows, _settings = make_actions()
windows.rejection = WindowRejection.NOT_RESIZABLE
result = actions.tile_focused(TileZone.LEFT_HALF)
check("tile reports an unmanageable focused window",
      not result.performed and "not_resizable" in result.reason)

actions, windows, _settings = make_actions(monitors=lambda: ())
result = actions.tile_focused(TileZone.LEFT_HALF)
check("tile reports a missing work area",
      not result.performed and result.reason == "no monitor work area")

actions, windows, _settings = make_actions()
windows.bounds = None
result = actions.tile_focused(TileZone.LEFT_HALF)
check("tile reports unavailable bounds",
      not result.performed and "bounds unavailable" in result.reason)

actions, windows, _settings = make_actions(
    constraints=lambda _handle: (600, 200, 700, 700))
before = windows.bounds
result = actions.tile_focused(TileZone.LEFT_HALF)
check("tile computes, clamps, places, and records the original",
      result.performed and result.before == before
      and result.after == Rect(0, 0, 600, 700)
      and actions.tracker.get_current_zone(10) is TileZone.LEFT_HALF)

actions, windows, _settings = make_actions()
windows.place_result = False
result = actions.tile_focused(TileZone.RIGHT_HALF)
check("tile reports refused placement without recording restore state",
      not result.performed and result.reason == "placement refused"
      and actions.tracker.get_current_zone(10) is None)

result = actions.tile_focused("left")
check("tile reports an invalid zone", not result.performed
      and "unknown zone" in result.reason)


# ---- refinement ladder and one-shot restore ----------------------------------

actions, windows, _settings = make_actions()
original = windows.bounds
first = actions.refine_focused(TileDirection.LEFT)
second = actions.refine_focused(TileDirection.UP)
third = actions.refine_focused(TileDirection.DOWN)
check("refinement walks half to quarter and back through the landed ladder",
      first.after == Rect(0, 0, 500, 800)
      and second.after == Rect(0, 0, 500, 400)
      and third.after == Rect(0, 0, 500, 800))
restored = actions.restore_focused()
again = actions.restore_focused()
check("restore returns the first pre-tile bounds and is one-shot",
      restored.performed and restored.after == original
      and not again.performed and again.reason == "no stored bounds")

invalid = actions.refine_focused("north")
check("refine reports an invalid direction", not invalid.performed
      and "unknown direction" in invalid.reason)

actions, windows, _settings = make_actions()
actions.tile_focused(TileZone.LEFT_HALF)
windows.bounds = Rect(40, 40, 420, 360)
result = actions.refine_focused(TileDirection.UP)
check("a manual move invalidates tracked refinement state",
      result.after == Rect(0, 0, 1000, 400)
      and actions.tracker.get_current_zone(10) is TileZone.TOP_HALF)


# ---- center and all shared decision exits ------------------------------------

actions, windows, _settings = make_actions(
    constraints=lambda _handle: (0, 0, 300, 250))
result = actions.center_focused()
check("center preserves a permitted size, clamps, and centers in the work area",
      result.performed and result.after == Rect(350, 275, 300, 250))

actions, windows, _settings = make_actions(foreground=lambda: 0)
center = actions.center_focused()
restore = actions.restore_focused()
refine = actions.refine_focused(TileDirection.RIGHT)
check("every focused verb reports the nothing-focused decision",
      all(item.reason == "nothing focused" for item in
          (center, restore, refine)))

actions, windows, _settings = make_actions()
windows.pid = 99
center = actions.center_focused()
restore = actions.restore_focused()
refine = actions.refine_focused(TileDirection.RIGHT)
check("every focused verb enforces the own-process safety law",
      all("belongs to this process" in item.reason for item in
          (center, restore, refine)) and not windows.placements)


# ---- optional module degradation and available paths -------------------------

def missing(name):
    raise ModuleNotFoundError(name)


actions, _windows, _settings = make_actions(module_loader=missing)
optional = (actions.apply_rules_now(), actions.save_preset("Work"),
            actions.restore_preset("Work"))
check("rules and presets degrade with available=False when modules are absent",
      all(not item.performed and not item.available for item in optional))


class FakePresets:
    def __init__(self):
        self.saved = []
        self.presets = []
        self.restores = []

    def scan_live_desktop(self):
        return (SimpleNamespace(handle=10),)

    def capture_preset(self, name, desktop):
        return SimpleNamespace(name=name.strip(), windows=tuple(desktop))

    def save_preset(self, store, preset):
        self.saved.append(preset)
        self.presets = [preset]

    def load_presets(self, store):
        return self.presets

    def restore(self, preset, desktop, mover):
        self.restores.append((preset, desktop))
        succeeded = mover(10, Rect(20, 30, 320, 240))
        return [SimpleNamespace(succeeded=succeeded)], []


fake_presets = FakePresets()
actions, _windows, _settings = make_actions(
    module_loader=lambda name: fake_presets)
saved = actions.save_preset(" Work ")
restored = actions.restore_preset("work")
missing_preset = actions.restore_preset("Missing")
check("preset verbs report save, successful restore, and missing-name outcomes",
      saved.performed and saved.details == {"name": "Work", "windows": 1}
      and restored.performed and restored.details["moved"] == 1
      and not missing_preset.performed
      and missing_preset.reason == "preset not found")


class FakeRules:
    class WindowFacts:
        from_tracked = staticmethod(lambda window: window)

    @staticmethod
    def load_rules(store):
        return SimpleNamespace(rules=(object(),), problems=())

    @staticmethod
    def resolve(rules, facts):
        return object()

    @staticmethod
    def apply_action(handle, action, mover):
        return True


import window_tracker

real_tracker = window_tracker.WindowTracker


class FakeTracker:
    def __init__(self, service):
        self.windows = (SimpleNamespace(handle=10),)

    def scan(self):
        pass


try:
    window_tracker.WindowTracker = FakeTracker
    actions, _windows, _settings = make_actions(
        module_loader=lambda name: FakeRules)
    rules_result = actions.apply_rules_now()
finally:
    window_tracker.WindowTracker = real_tracker
check("apply rules now reports a completed injected rule pass",
      rules_result.performed
      and rules_result.details == {"applied": 1, "refused": 0})


# ---- registry, live binding table, consumer routing, and hook lifetime --------

def _canonical(chord):
    """The router's spelling, so the table can be compared honestly."""
    return str(KeySequence.parse(chord))


# Every zone answers to three spellings of the same idea: the numpad digit
# whose position is the zone, the same digit on the number row, and (for the
# halves) an arrow. All under Doug's triple modifier.
expected_bindings = {}
for _command, _argument, _chord in (*host.ZONE_COMMANDS, *host.REFINE_COMMANDS):
    expected_bindings[_canonical(_chord)] = _command
for _table in (host.TOP_ROW_DIGITS, host.LAPTOP_SHORTCUTS):
    for _command, _chord in _table.items():
        expected_bindings[_canonical(_chord)] = _command
expected_bindings[_canonical("Ctrl+Win+Alt+Numpad 5")] = host.RESTORE_COMMAND
expected_bindings[_canonical("Ctrl+Win+Alt+5")] = host.RESTORE_COMMAND


class FakeService:
    def __init__(self, router=None):
        self.router = router or KeyboardRouter()
        self.is_hook_installed = False
        self.starts = 0
        self.stops = 0
        self.registered = []
        self.unregistered = []
        self.start_error = None

    def register_consumer(self, consumer):
        self.registered.append(consumer)
        self.router.register_consumer(consumer)

    def unregister_consumer(self, consumer_id):
        self.unregistered.append(consumer_id)
        self.router.unregister_consumer(consumer_id)

    def start(self):
        self.starts += 1
        if self.start_error:
            raise self.start_error
        self.is_hook_installed = True

    def stop(self):
        self.stops += 1
        self.is_hook_installed = False


actions, windows, settings = make_actions()
service = FakeService()
hotkeys = host.HotkeyHost(actions, service=service,
                          settings_service=settings)
check("the feature declaration is disabled by default",
      not host.FEATURE_DECLARATION.default_enabled
      and not settings.is_enabled(host.FEATURE_ID))
check("the live binding table carries the reference and laptop chords",
      hotkeys.bindings() == expected_bindings)
check("every zone is reachable without a numpad",
      all(command in host.TOP_ROW_DIGITS
          for command, _argument, _chord in host.ZONE_COMMANDS))
check("every zone chord carries Doug's triple modifier",
      all(chord.startswith("Ctrl+Win+Alt+")
          for _c, _a, chord in host.ZONE_COMMANDS)
      and all(chord.startswith("Ctrl+Win+Alt+")
              for chord in (*host.TOP_ROW_DIGITS.values(),
                            *host.LAPTOP_SHORTCUTS.values())))
check("no alternate chord collides with another binding",
      len(hotkeys.bindings()) == len(expected_bindings))
check("collisions delegates to settings_service",
      hotkeys.collisions() == settings.shortcut_collisions())
check("construction installs and registers nothing",
      service.starts == 0 and not service.registered
      and not hotkeys.is_running)

started = hotkeys.start()
consumer = service.registered[-1]
verdict = consumer.process_key_event(
    RawKeyboardEvent(0x64, 0, True, False, 0, "Numpad4"),
    ChordModifiers.CTRL | ChordModifiers.WIN | ChordModifiers.ALT)
verdict.action()
check("a matching key-down is swallowed with its window action",
      started.performed
      and verdict.kind is KeyboardRoutingVerdictKind.SWALLOW_WITH_ACTION
      and consumer.last_result.performed
      and windows.bounds == Rect(0, 0, 500, 800))

stopped = hotkeys.stop()
restarted = hotkeys.start()
restopped = hotkeys.stop()
check("stop fully releases and start-stop-start works",
      stopped.performed and restarted.performed and restopped.performed
      and service.starts == 2 and service.stops == 2
      and service.unregistered == [host.CONSUMER_ID, host.CONSUMER_ID]
      and not hotkeys.is_running)

service = FakeService()
service.is_hook_installed = True
hotkeys = host.HotkeyHost(actions, service=service,
                          settings_service=settings)
refused = hotkeys.start()
check("start refuses when a hook owner already exists",
      not refused.performed and service.starts == 0
      and not service.registered and not hotkeys.is_running)

service = FakeService()
service.start_error = RuntimeError("A WH_KEYBOARD_LL owner is already active")
hotkeys = host.HotkeyHost(actions, service=service,
                          settings_service=settings)
refused = hotkeys.start()
check("a raced single-hook refusal is reported and registration is rolled back",
      not refused.performed and "owner" in refused.reason
      and service.unregistered == [host.CONSUMER_ID]
      and not hotkeys.is_running)


# ---- structural proof of the explicit hook boundary --------------------------

source = pathlib.Path(host.__file__).read_text(encoding="utf-8")
tree = ast.parse(source)
parents = {}
for node in ast.walk(tree):
    for child in ast.iter_child_nodes(node):
        parents[child] = node


def enclosing_function(node):
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return None


service_starts = [
    node for node in ast.walk(tree)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and node.func.attr == "start"
    and isinstance(node.func.value, ast.Attribute)
    and node.func.value.attr == "service"
]
check("the hook service is started only inside HotkeyHost.start",
      len(service_starts) == 1
      and enclosing_function(service_starts[0]) == "start")

module_level_effects = [
    node for node in tree.body
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
]
check("the module has no import-time call expressions",
      not module_level_effects)

print("ALL HOTKEY HOST TESTS PASSED")
