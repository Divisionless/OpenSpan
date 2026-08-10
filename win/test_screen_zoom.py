"""Pure fake-driven and structural checks for screen_zoom.py."""

import ast
import ctypes
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


if failures:
    print(f"RESULT: {len(failures)} FAILED")
    raise SystemExit(1)
print("RESULT: ALL SCREEN ZOOM TESTS PASSED")
