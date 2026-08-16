"""Pure fake-driven and structural checks for screen_zoom.py."""

import ast
import ctypes
import inspect
import logging
import math
import pathlib
import tempfile
import threading

import screen_zoom as zoom
from config_store import ConfigStore
from keyboard_interception import (
    ChordModifiers,
    KeyboardRoutingVerdictKind,
    RawKeyboardEvent,
)
from monitor_identity import MonitorIdentity
from settings_service import FeatureRegistry, SettingsService


failures = []


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        failures.append(name)


def armed():
    gesture = zoom.ZoomGesture()
    gesture.on_key("Alt", True)
    return gesture


# ---- every ZoomGesture.cs verdict and reset path ----------------------------

gesture = armed()
check("the configured modifier plus either wheel direction zooms",
      gesture.on_wheel(1, False) is zoom.ZoomVerdict.SWALLOW_AND_ZOOM
      and gesture.on_wheel(-1, False) is zoom.ZoomVerdict.SWALLOW_AND_ZOOM)

gesture = zoom.ZoomGesture()
check("a bare wheel is never touched",
      gesture.on_wheel(1, False) is zoom.ZoomVerdict.PASS_THROUGH
      and gesture.on_wheel(-3, False) is zoom.ZoomVerdict.PASS_THROUGH)

gesture = armed()
gesture.on_key("Shift", True)
extra_passes = gesture.on_wheel(1, False) is zoom.ZoomVerdict.PASS_THROUGH
gesture.on_key("Shift", False)
check("an extra modifier disarms only while it is held",
      extra_passes
      and gesture.on_wheel(1, False) is zoom.ZoomVerdict.SWALLOW_AND_ZOOM)

gesture = armed()
gesture.on_key("Alt", False)
check("releasing the configured modifier disarms the gesture",
      gesture.on_wheel(1, False) is zoom.ZoomVerdict.PASS_THROUGH)

gesture = zoom.ZoomGesture()
gesture.on_key("Ctrl", True)
check("a different modifier alone does not arm the gesture",
      gesture.on_wheel(1, False) is zoom.ZoomVerdict.PASS_THROUGH)

gesture = zoom.ZoomGesture()
gesture.on_key("Ctrl", True)
check("Ctrl scroll passes through by default for document zoom",
      gesture.modifier is ChordModifiers.ALT
      and gesture.on_wheel(1, False) is zoom.ZoomVerdict.PASS_THROUGH
      and gesture.on_wheel(-1, False) is zoom.ZoomVerdict.PASS_THROUGH)

gesture = zoom.ZoomGesture(ChordModifiers.CTRL)
gesture.on_key("Ctrl", True)
ctrl_works = gesture.on_wheel(1, False) is zoom.ZoomVerdict.SWALLOW_AND_ZOOM
gesture.on_key("Ctrl", False)
gesture.on_key("Alt", True)
check("the modifier is configurable",
      ctrl_works
      and gesture.on_wheel(1, False) is zoom.ZoomVerdict.PASS_THROUGH)

check("injected wheel events are never consumed",
      armed().on_wheel(1, True) is zoom.ZoomVerdict.PASS_THROUGH)
check("a zero wheel delta is not a gesture",
      armed().on_wheel(0, False) is zoom.ZoomVerdict.PASS_THROUGH)

gesture = armed()
gesture.enabled = False
check("disabling the gesture passes the wheel through",
      gesture.on_wheel(1, False) is zoom.ZoomVerdict.PASS_THROUGH)

gesture = armed()
gesture.reset()
check("reset forgets held modifiers",
      not gesture.is_armed
      and gesture.on_wheel(1, False) is zoom.ZoomVerdict.PASS_THROUGH)

gesture = armed()
gesture.on_key("A", True)
gesture.on_key("Left", True)
check("non-modifier keys do not affect arming",
      gesture.on_wheel(1, False) is zoom.ZoomVerdict.SWALLOW_AND_ZOOM)

gesture = armed()
t = 10_000
for index in range(6):
    gesture.on_pointer_move(True, t + index * 20)
t += 120
check("the wheel passes while another process drives the pointer",
      gesture.pointer_driven_elsewhere(t)
      and gesture.on_wheel(1, False, t) is zoom.ZoomVerdict.PASS_THROUGH)

gesture.on_pointer_move(False, t)
check("a real pointer move immediately takes the wheel back",
      not gesture.pointer_driven_elsewhere(t)
      and gesture.on_wheel(1, False, t)
      is zoom.ZoomVerdict.SWALLOW_AND_ZOOM)

gesture = armed()
gesture.on_pointer_move(True, 10_000)
check("one stray injected move does not surrender the wheel",
      not gesture.pointer_driven_elsewhere(10_000)
      and gesture.on_wheel(1, False, 10_000)
      is zoom.ZoomVerdict.SWALLOW_AND_ZOOM)

gesture = armed()
for index in range(6):
    gesture.on_pointer_move(True, 10_000 + index * 20)
check("pointer ownership lapses after the injected stream stops",
      not gesture.pointer_driven_elsewhere(15_000)
      and gesture.on_wheel(1, False, 15_000)
      is zoom.ZoomVerdict.SWALLOW_AND_ZOOM)

parse_cases = {
    "ctrl": ChordModifiers.CTRL,
    "Alt": ChordModifiers.ALT,
    "SHIFT": ChordModifiers.SHIFT,
    "cmd": ChordModifiers.WIN,
    "nonsense": ChordModifiers.ALT,
    None: ChordModifiers.ALT,
}
check("modifier settings parse forgivingly",
      all(zoom.ZoomGesture.parse_modifier(raw) is expected
          for raw, expected in parse_cases.items()))

gesture = armed()
gesture.sync_held(lambda _key: False)
check("physical resync repairs a missed modifier release",
      not gesture.is_armed
      and gesture.on_wheel(1, False) is zoom.ZoomVerdict.PASS_THROUGH)

gesture = armed()
gesture.sync_held(lambda key: key == "Alt")
check("a physically held modifier survives resync",
      gesture.is_armed
      and gesture.on_wheel(1, False)
      is zoom.ZoomVerdict.SWALLOW_AND_ZOOM)

gesture = armed()
gesture.on_key("Shift", True)
was_disarmed = gesture.on_wheel(1, False) is zoom.ZoomVerdict.PASS_THROUGH
gesture.sync_held(lambda key: key == "Alt")
check("resync drops stale extra modifiers and restores arming",
      was_disarmed
      and gesture.on_wheel(1, False)
      is zoom.ZoomVerdict.SWALLOW_AND_ZOOM)


# ---- observer, decoded mouse paths, suspension, and clamping ----------------

observer_gesture = zoom.ZoomGesture()
observer = zoom.ZoomModifierObserver(observer_gesture)
down = RawKeyboardEvent(0xA4, 0, True, False, 0, "Alt")
up = RawKeyboardEvent(0xA4, 0, False, False, 0, "Alt")
down_verdict = observer.process_key_event(down, ChordModifiers.ALT)
armed_after_down = observer_gesture.is_armed
up_verdict = observer.process_key_event(up, ChordModifiers.NONE)
check("the priority-zero modifier observer never swallows",
      observer.priority == 0 and armed_after_down and not observer_gesture.is_armed
      and down_verdict.kind is KeyboardRoutingVerdictKind.PASS_THROUGH
      and up_verdict.kind is KeyboardRoutingVerdictKind.PASS_THROUGH)

submitted = []
next_calls = []


def submit_now(callback, *args):
    submitted.append(args)
    callback(*args)


zoomed = []
gesture = zoom.ZoomGesture()
hook = zoom.ZoomMouseHook(
    gesture, lambda n, x, y: zoomed.append((n, x, y)), lambda: False,
    submit=submit_now)
consumed = hook.dispatch_mouse_event(
    hook.WM_MOUSEWHEEL, raw_delta=240, x=20, y=30,
    is_physically_down=lambda key: key == "Alt",
    call_next=lambda: next_calls.append(True) or 77)
check("an armed physical wheel is consumed and queues fractional zoom",
      consumed == 1 and zoomed == [(2.0, 20, 30)] and not next_calls)

passed = hook.dispatch_mouse_event(
    hook.WM_MOUSEWHEEL, raw_delta=-120,
    is_physically_down=lambda _key: False,
    call_next=lambda: 77)
check("a wheel without the modifier calls next untouched", passed == 77)

injected = hook.dispatch_mouse_event(
    hook.WM_MOUSEWHEEL, raw_delta=120, is_injected=True,
    is_physically_down=lambda key: key == "Alt",
    call_next=lambda: 88)
check("an injected wheel calls next even while the modifier is down",
      injected == 88 and len(zoomed) == 1)

suspended_gesture = armed()
suspended_zoom = []
suspended_hook = zoom.ZoomMouseHook(
    suspended_gesture, lambda *args: suspended_zoom.append(args), lambda: True,
    submit=submit_now)
suspended = suspended_hook.dispatch_mouse_event(
    suspended_hook.WM_MOUSEWHEEL, raw_delta=120,
    is_physically_down=lambda key: key == "Alt",
    call_next=lambda: 91)
check("suspension resets the gesture and passes the wheel through",
      suspended == 91 and not suspended_gesture.is_armed
      and not suspended_zoom)

mid_gesture = zoom.ZoomGesture()
mid_observer = zoom.ZoomModifierObserver(mid_gesture)
mid_hook = zoom.ZoomMouseHook(
    mid_gesture, lambda *args: None, lambda: False, submit=submit_now)
mid_observer.process_key_event(down, ChordModifiers.ALT)
first = mid_hook.dispatch_mouse_event(
    mid_hook.WM_MOUSEWHEEL, raw_delta=120,
    is_physically_down=lambda key: key == "Alt", call_next=lambda: 92)
mid_observer.process_key_event(up, ChordModifiers.NONE)
second = mid_hook.dispatch_mouse_event(
    mid_hook.WM_MOUSEWHEEL, raw_delta=120,
    is_physically_down=lambda _key: False, call_next=lambda: 92)
check("releasing the modifier mid-gesture stops consumption immediately",
      first == 1 and second == 92)

check("zoom stepping clamps at both C# limits",
      zoom.step_level(1.0, -20) == zoom.MIN_LEVEL
      and zoom.step_level(1.0, 100) == zoom.MAX_LEVEL
      and zoom.clamp_level(float("nan")) == zoom.MIN_LEVEL)

screen = zoom.ScreenRect(-2560, -1440, 2560, 1440)
anchor = (-1000, -500)
old_x, old_y = -2200.25, -1200.75
new_x, new_y = zoom.rescale_about(
    screen, anchor, old_x, old_y, 2.0, 5.0)
check("RescaleAbout keeps the anchor pixel fixed across a level change",
      math.isclose((anchor[0] - old_x) * 2.0,
                   (anchor[0] - new_x) * 5.0, abs_tol=1e-9)
      and math.isclose((anchor[1] - old_y) * 2.0,
                       (anchor[1] - new_y) * 5.0, abs_tol=1e-9))

composed_x, composed_y = old_x, old_y
levels = [2.0 * math.pow(5.0 / 2.0, index / 200.0)
          for index in range(201)]
for before, after in zip(levels, levels[1:]):
    composed_x, composed_y = zoom.rescale_about(
        screen, anchor, composed_x, composed_y, before, after)
direct_x, direct_y = zoom.rescale_about(
    screen, anchor, old_x, old_y, 2.0, 5.0)
check("fractional RescaleAbout composition does not accumulate drift",
      math.isclose(composed_x, direct_x, abs_tol=1e-9)
      and math.isclose(composed_y, direct_y, abs_tol=1e-9))

negative_monitor = zoom.ScreenRect(-2560, -1440, 2560, 1440)
view_x, view_y = -2200.25, -1100.75
level = 2.5
transform = zoom.window_transform(
    negative_monitor, view_x, view_y, level)
wrong_false_origin_x = -view_x * level
wrong_relative_x = -(view_x - negative_monitor.x) * level
wrong_false_origin_y = -view_y * level
wrong_relative_y = -(view_y - negative_monitor.y) * level
check("negative-origin transform includes the monitor desktop constant",
      math.isclose(
          transform.v02,
          negative_monitor.x - view_x * level, abs_tol=1e-9)
      and math.isclose(
          transform.v12,
          negative_monitor.y - view_y * level, abs_tol=1e-9)
      and transform.v00 == transform.v11 == level
      and transform.v22 == 1.0)
check("neither documented false-origin transform is produced",
      not math.isclose(transform.v02, wrong_false_origin_x)
      and not math.isclose(transform.v02, wrong_relative_x)
      and not math.isclose(transform.v12, wrong_false_origin_y)
      and not math.isclose(transform.v12, wrong_relative_y))
resolved_x = (negative_monitor.x - transform.v02) / level
resolved_y = (negative_monitor.y - transform.v12) / level
check("a left-of-primary viewport resolves to itself, never the primary",
      math.isclose(resolved_x, view_x, abs_tol=1e-9)
      and math.isclose(resolved_y, view_y, abs_tol=1e-9)
      and resolved_x < 0)

edge_screen = zoom.ScreenRect(-3200, -1200, 1600, 1200)
edge_level = 2.0
edge_x, edge_y = -3000.0, -1100.0
view_width = round(edge_screen.width / edge_level)
view_height = round(edge_screen.height / edge_level)
right_overshoot = 37
bottom_overshoot = 23
pushed_x, pushed_y = zoom.edge_push(
    edge_screen,
    (round(edge_x) + view_width - 1 + right_overshoot,
     round(edge_y) + view_height - 1 + bottom_overshoot),
    edge_x, edge_y, edge_level)
check("EdgePush moves a negative-origin viewport by exactly the overshoot",
      pushed_x == edge_x + right_overshoot
      and pushed_y == edge_y + bottom_overshoot)
inside = zoom.edge_push(
    edge_screen, (-2800, -800), pushed_x, pushed_y, edge_level)
check("EdgePush leaves viewport state untouched while the pointer is inside",
      inside == (pushed_x, pushed_y))

eased = zoom.ease_level(2.0, 8.0, zoom.RAMP_TIME_CONSTANT_MS)
expected_log = (math.log(2.0)
                + (math.log(8.0) - math.log(2.0)) * (1.0 - math.exp(-1.0)))
settling = 2.0
for _ in range(100):
    settling = zoom.ease_level(settling, 8.0, 16.0)
check("EaseLevel converges in log space and settles exactly on target",
      math.isclose(math.log(eased), expected_log, abs_tol=1e-12)
      and settling == 8.0)

negative_screen = zoom.ScreenRect(-1920, -1080, 1920, 1080)
negative_view = zoom.ScreenRect(-1680, -945, 960, 540)
check("negative-origin monitor uses primary-relative fullscreen offsets",
      zoom.fullscreen_offsets(negative_screen, negative_view, 2.0)
      == (-720, -405))

monitor = MonitorIdentity(
    native_width=1920, native_height=1080,
    virtual_x=-1920, virtual_y=0, device_name="DISPLAY2")
check("monitor_identity selects the monitor beneath the pointer",
      zoom.screen_for_point((monitor,), -100, 500)
      == zoom.ScreenRect(-1920, 0, 1920, 1080)
      and zoom.screen_for_point((monitor,), 1, 500) is None)


# ---- real hook lifetime driven entirely by fake Windows bindings ------------

events = []


class FakeKeyboardService:
    def __init__(self):
        self.registered = []
        self.unregistered = []

    def register_consumer(self, consumer):
        events.append("register")
        self.registered.append(consumer)

    def unregister_consumer(self, consumer_id):
        events.append("unregister")
        self.unregistered.append(consumer_id)


class FakeUser32:
    def __init__(self):
        self.quit = threading.Event()

    def PeekMessageW(self, *args):
        return 0

    def SetWindowsHookExW(self, *args):
        events.append("install")
        return 123

    def GetMessageW(self, *args):
        self.quit.wait(2.0)
        return 0

    def TranslateMessage(self, *args):
        return True

    def DispatchMessageW(self, *args):
        return 0

    def PostThreadMessageW(self, *args):
        events.append("quit")
        self.quit.set()
        return True

    def UnhookWindowsHookEx(self, *args):
        events.append("unhook")
        return True

    def GetAsyncKeyState(self, vk):
        return 0

    def CallNextHookEx(self, *args):
        return 0


class FakeKernel32:
    @staticmethod
    def GetCurrentThreadId():
        return 42

    @staticmethod
    def GetModuleHandleW(name):
        return 1


class FakeMouseBindings:
    WH_MOUSE_LL = 14
    MSLLHOOKSTRUCT = zoom._MouseBindings.MSLLHOOKSTRUCT

    def __init__(self):
        self.user32 = FakeUser32()
        self.kernel32 = FakeKernel32()
        self.hook_proc_type = lambda callback: callback


keyboard = FakeKeyboardService()
native_hook = zoom.ZoomMouseHook(
    zoom.ZoomGesture(), lambda *args: None, lambda: False, keyboard,
    bindings_factory=FakeMouseBindings)
check("constructing a mouse hook registers and installs nothing",
      not events and not keyboard.registered and not native_hook.installed)
native_hook.start()
started_state = (native_hook.installed and events[:2] == ["register", "install"]
                 and keyboard.registered[0].priority == 0)
native_hook.stop()
check("start owns installation and stop fully releases the scoped hook",
      started_state and "quit" in events and "unhook" in events
      and keyboard.unregistered == [zoom.CONSUMER_ID]
      and not native_hook.installed)


# ---- feature declaration, settings, unavailable start, and safe stop --------

with tempfile.TemporaryDirectory() as directory:
    store = ConfigStore(directory)
    settings = SettingsService(
        store, FeatureRegistry([zoom.FEATURE_DECLARATION]))
    check("the feature declaration is disabled by default",
          not zoom.FEATURE_DECLARATION.default_enabled
          and not settings.is_enabled(zoom.FEATURE_ID))
    module = zoom.ScreenZoomModule(
        magnifier=object(), settings_service=settings,
        monitor_provider=lambda: ())
    module.set_modifier("control")
    reloaded = SettingsService(
        ConfigStore(directory), FeatureRegistry([zoom.FEATURE_DECLARATION]))
    check("the modifier setting round-trips through landed persistence",
          settings.get_setting(zoom.FEATURE_ID, zoom.MODIFIER_SETTING) == "Ctrl"
          and reloaded.get_setting(
              zoom.FEATURE_ID, zoom.MODIFIER_SETTING) == "Ctrl")


class FakeMagnifier:
    def __init__(self, event_log=None, *, available=True, reset_error=None):
        self.events = event_log if event_log is not None else []
        self.available = available
        self.reset_error = reset_error
        self.probes = 0
        self.applied = []

    def probe(self):
        self.probes += 1
        self.events.append("probe")
        return self.available

    def apply(self, level, screen, anchor):
        self.applied.append((level, screen, anchor))
        return True

    def apply_viewport(self, level, screen, view):
        self.applied.append((level, screen, view))
        return True

    def reset(self):
        self.events.append("reset")
        if self.reset_error is not None:
            raise self.reset_error
        return True


class FakeFeatureHook:
    def __init__(self, *args, event_log=None, **kwargs):
        self.events = event_log if event_log is not None else []
        self.installed = False

    def start(self):
        self.events.append("hook-start")
        self.installed = True

    def stop(self):
        self.events.append("hook-stop")
        self.installed = False


factory_calls = []


def unavailable_factory(*args, **kwargs):
    factory_calls.append((args, kwargs))
    return FakeFeatureHook()


unavailable_magnifier = FakeMagnifier(available=False)
unavailable_module = zoom.ScreenZoomModule(
    unavailable_magnifier, hook_factory=unavailable_factory,
    monitor_provider=lambda: ())
check("module construction probes and installs nothing",
      unavailable_magnifier.probes == 0 and not factory_calls)
check("unavailable magnification installs no half-armed hook",
      not unavailable_module.start() and unavailable_magnifier.probes == 1
      and not factory_calls and unavailable_module.available is False)


class RampClock:
    def __init__(self):
        self.value = 1.0

    def __call__(self):
        self.value += 0.016
        return self.value

    def advance(self, seconds):
        self.value += seconds


negative_monitor_identity = MonitorIdentity(
    native_width=1920, native_height=1080,
    virtual_x=-1920, virtual_y=-200, device_name="DISPLAY2")
upper_monitor = MonitorIdentity(
    native_width=1920, native_height=1080,
    virtual_x=0, virtual_y=-1280, device_name="DISPLAY3")
ramp_clock = RampClock()
ramp_magnifier = FakeMagnifier()
ramp_module = zoom.ScreenZoomModule(
    ramp_magnifier,
    monitor_provider=lambda: (negative_monitor_identity, upper_monitor),
    clock=ramp_clock)
first_anchor = (-1500, 300)
ramp_module.zoom_at(1.0, first_anchor, 2.0)
ramp_module.zoom_at(1.0, (800, -800), 2.0)
check("the 600 ms gesture freezes its anchor and never crosses displays",
      ramp_module._gesture_anchor == first_anchor
      and ramp_module.level == 4.0
      and ramp_magnifier.applied[-1][2]
      == first_anchor
      and ramp_magnifier.applied[-1][1]
      == zoom.ScreenRect(-1920, -200, 1920, 1080))
ramp_clock.advance(0.7)
new_anchor = (800, -800)
ramp_module.zoom_at(-1.0, new_anchor, 2.0)
check("a new gesture re-establishes its anchor",
      ramp_module._gesture_anchor == new_anchor
      and ramp_module.level == 2.0
      and ramp_magnifier.applied[-1][2] == new_anchor
      and ramp_magnifier.applied[-1][1]
      == zoom.ScreenRect(0, -1280, 1920, 1080))

lifetime_events = []


def lifetime_factory(*args, **kwargs):
    return FakeFeatureHook(event_log=lifetime_events)


raising_magnifier = FakeMagnifier(
    lifetime_events, reset_error=RuntimeError("restore failed"))
module = zoom.ScreenZoomModule(
    raising_magnifier, hook_factory=lifetime_factory,
    monitor_provider=lambda: ())
started = module.start()
stopped = module.stop()
check("stop attempts 1x before releasing the hook even when restore raises",
      started and not stopped
      and lifetime_events == ["probe", "hook-start", "reset", "hook-stop"]
      and "restore failed" in module.last_error)

construction_calls = []
magnifier = zoom.ScreenMagnifier(
    bindings_factory=lambda: construction_calls.append("native"))
check("ScreenMagnifier construction starts no thread or native API",
      not construction_calls and magnifier._thread is None)


class CursorCalls:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []

    def __call__(self, show):
        self.calls.append(show)
        return next(self.outcomes)


cursor_calls = CursorCalls((True, True, False, True))
cursor_logger = logging.getLogger("cursor-test")
cursor_logger.disabled = True
cursor = zoom._CursorVisibility(
    cursor_calls, cursor_logger)
startup_restored = cursor.restore_at_startup()
hidden = cursor.set_hidden(True)
first_restore = cursor.set_hidden(False)
still_owned_after_failure = cursor.hidden
second_restore = cursor.set_hidden(False)
check("cursor is restored at startup and a failed restore is retried",
      startup_restored and hidden and not first_restore
      and still_owned_after_failure and second_restore and not cursor.hidden
      and cursor_calls.calls == [True, False, True, True])


# ---- structural proof of the explicit start boundary ------------------------

source = pathlib.Path(zoom.__file__).read_text(encoding="utf-8")
tree = ast.parse(source)
parents = {}
for node in ast.walk(tree):
    for child in ast.iter_child_nodes(node):
        parents[child] = node


def enclosing(node, kind):
    while node in parents:
        node = parents[node]
        if isinstance(node, kind):
            return node
    return None


native_installs = [
    node for node in ast.walk(tree)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and node.func.attr == "SetWindowsHookExW"
]
install_owner = (enclosing(native_installs[0], ast.FunctionDef)
                 if len(native_installs) == 1 else None)
install_class = (enclosing(native_installs[0], ast.ClassDef)
                 if len(native_installs) == 1 else None)
thread_targets = [
    node for node in ast.walk(tree)
    if isinstance(node, ast.keyword) and node.arg == "target"
    and isinstance(node.value, ast.Attribute)
    and node.value.attr == "_thread_main"
    and (owner := enclosing(node, ast.ClassDef)) is not None
    and owner.name == "ZoomMouseHook"
]
thread_starts = [
    node for node in ast.walk(tree)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and node.func.attr == "start"
    and isinstance(node.func.value, ast.Attribute)
    and node.func.value.attr == "_thread"
    and (owner := enclosing(node, ast.ClassDef)) is not None
    and owner.name == "ZoomMouseHook"
]
start_owner = (enclosing(thread_starts[0], ast.FunctionDef)
               if len(thread_starts) == 1 else None)
check("the native mouse hook is reachable only from ZoomMouseHook.start",
      len(native_installs) == 1
      and install_owner is not None and install_owner.name == "_thread_main"
      and install_class is not None and install_class.name == "ZoomMouseHook"
      and len(thread_targets) == 1 and len(thread_starts) == 1
      and start_owner is not None and start_owner.name == "start")

module_level_effects = [
    node for node in tree.body
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
]
check("the module has no import-time call expressions", not module_level_effects)

render_method = next(
    node for node in ast.walk(tree)
    if isinstance(node, ast.FunctionDef) and node.name == "_render_level")
render_mag_calls = [
    node.func.attr for node in ast.walk(render_method)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and node.func.attr.startswith("MagSet")
]
source_pushes = [
    node for node in ast.walk(tree)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and node.func.attr == "MagSetWindowSource"
]
source_push_owner = (enclosing(source_pushes[0], ast.FunctionDef)
                     if len(source_pushes) == 1 else None)
check("each render branch makes one transform call and never pushes source",
      sorted(render_mag_calls)
      == ["MagSetFullscreenTransform", "MagSetWindowTransform"]
      and len(source_pushes) == 1
      and source_push_owner is not None
      and source_push_owner.name == "_on_apply")
check("the per-display host has the required cursor style and self-filter",
      "MS_SHOWMAGNIFIEDCURSOR" in source
      and "MagSetWindowFilterList" in source
      and "WS_EX_TRANSPARENT" in source
      and "WM_SETREDRAW" not in source)
scope_default = inspect.signature(
    zoom.ScreenMagnifier).parameters["scope"].default
check("per-display scope is default and desktop scope remains explicit",
      scope_default == "display"
      and hasattr(zoom.ScreenMagnifier, "apply_fullscreen"))
check("the magnifier owns an STA message-pump thread",
      "COINIT_APARTMENTTHREADED" in source
      and "GetMessageW" in source
      and "DispatchMessageW" in source)

thread_main = next(
    node for node in ast.walk(tree)
    if isinstance(node, ast.FunctionDef)
    and node.name == "_thread_main"
    and enclosing(node, ast.ClassDef).name == "ScreenMagnifier")
cleanup_calls = [
    node for node in ast.walk(thread_main)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and node.func.attr == "_cleanup_on_thread"
]
hide_method = next(
    node for node in ast.walk(tree)
    if isinstance(node, ast.FunctionDef) and node.name == "_hide_on_thread")
restore_calls = [
    node for node in ast.walk(hide_method)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and node.func.attr == "set_hidden"
]
check("cursor restoration is on normal hide and the thread-finally exit",
      len(cleanup_calls) == 1 and len(restore_calls) == 1)


# ---- the per-display host wins the z-order every frame ----------------------
#
# 2026-08-16: with a display zoomed, a context menu was painted at native size
# on top of the magnified image. Windows inserts every new topmost window --
# menus, tooltips, flyouts -- ABOVE the host, which was created once at
# startup. The overlay must re-assert HWND_TOPMOST each frame; fullscreen
# mode, having no host on screen, must not.

class _Recorder:
    def __init__(self, ret=1):
        self.calls = []
        self.ret = ret

    def __call__(self, *args):
        self.calls.append(args)
        return self.ret


class _FakeMagUser32:
    def __init__(self):
        self.SetWindowPos = _Recorder()
        self.ShowWindow = _Recorder()
        self.SetTimer = _Recorder()
        self.KillTimer = _Recorder()
        self.PostMessageW = _Recorder()

    @staticmethod
    def GetCursorPos(ref):
        ref._obj.x, ref._obj.y = -900, 400
        return 1


class _FakeMagDll:
    def __init__(self):
        self.MagSetWindowTransform = _Recorder()
        self.MagSetFullscreenTransform = _Recorder()
        self.MagSetWindowSource = _Recorder()


class _FakeMagBindings:
    def __init__(self):
        self.user32 = _FakeMagUser32()
        self.dll = _FakeMagDll()


def _zoomed_display_magnifier():
    mag = zoom.ScreenMagnifier(logger=cursor_logger)
    mag._bindings = _FakeMagBindings()
    mag._host, mag._control = 0x1234, 0x5678
    left = zoom.ScreenRect(-1920, 0, 1920, 1080)      # a NEGATIVE-origin display
    mag._requested_level = mag._level = mag._applied_level = 2.0
    mag._requested_monitor = mag._placed_monitor = left
    mag._requested_anchor = (-960, 540)
    mag._view_x, mag._view_y = -1440.0, 270.0
    mag._refreshing = True
    return mag


HWND_TOPMOST_AS_POINTER = 0xFFFFFFFFFFFFFFFF
NOMOVE_NOSIZE_NOACTIVATE = 0x0002 | 0x0001 | 0x0010

mag = _zoomed_display_magnifier()
mag._on_tick()
pos_calls = mag._bindings.user32.SetWindowPos.calls
topmost_calls = [
    c for c in pos_calls
    if c[0] == 0x1234 and c[1] in (-1, HWND_TOPMOST_AS_POINTER)
    and c[2:6] == (0, 0, 0, 0)
    and c[6] & NOMOVE_NOSIZE_NOACTIVATE == NOMOVE_NOSIZE_NOACTIVATE
]
check("a settled per-display frame re-asserts the host at HWND_TOPMOST, "
      "moving and sizing nothing and activating nothing",
      len(topmost_calls) == 1
      and len(mag._bindings.dll.MagSetWindowTransform.calls) == 1)
check("HWND_TOPMOST marshals as the pointer Windows expects",
      ctypes.c_void_p(zoom.ScreenMagnifier.HWND_TOPMOST).value
      == HWND_TOPMOST_AS_POINTER
      and zoom.wt.HWND.from_param(zoom.ScreenMagnifier.HWND_TOPMOST)
      is not None)

mag = _zoomed_display_magnifier()
mag._requested_level = 3.0                             # mid-ramp frame
mag._on_tick()
check("a ramping per-display frame re-asserts the host too",
      len(mag._bindings.user32.SetWindowPos.calls) == 1)

mag = _zoomed_display_magnifier()
mag._full_screen = True
mag._on_tick()
check("a fullscreen frame never touches window z-order (there is no host on screen)",
      mag._bindings.user32.SetWindowPos.calls == [])

on_apply = next(
    node for node in ast.walk(tree)
    if isinstance(node, ast.FunctionDef) and node.name == "_on_apply")
show_then_top = [
    node.func.attr for node in ast.walk(on_apply)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    and node.func.attr in ("ShowWindow", "_keep_on_top")
]
check("showing the host on a new display is followed by the topmost assert",
      show_then_top[-2:] == ["ShowWindow", "_keep_on_top"])


if failures:
    print(f"RESULT: {len(failures)} FAILED")
    raise SystemExit(1)
print("RESULT: ALL SCREEN ZOOM TESTS PASSED")
