"""Hold a modifier and scroll to magnify the Windows desktop.

The gesture and zoom arithmetic are pure.  Native magnification and the
``WH_MOUSE_LL`` hook are explicit lifetime objects: importing this module and
constructing any class install nothing.  ``ScreenZoomModule.start`` is the sole
feature boundary that can start the mouse hook.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import dataclasses
import enum
import logging
import math
import os
import queue
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


class _MagnificationBindings:
    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("The Windows Magnification API is available only on Windows.")
        self.dll = ctypes.WinDLL("Magnification.dll", use_last_error=True)
        self.dll.MagInitialize.restype = wt.BOOL
        self.dll.MagInitialize.argtypes = []
        self.dll.MagUninitialize.restype = wt.BOOL
        self.dll.MagUninitialize.argtypes = []
        self.dll.MagSetFullscreenTransform.restype = wt.BOOL
        self.dll.MagSetFullscreenTransform.argtypes = [
            ctypes.c_float, ctypes.c_int, ctypes.c_int]
        self.dll.MagGetFullscreenTransform.restype = wt.BOOL
        self.dll.MagGetFullscreenTransform.argtypes = [
            ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int)]
        self.dll.MagShowSystemCursor.restype = wt.BOOL
        self.dll.MagShowSystemCursor.argtypes = [wt.BOOL]


class ScreenMagnifier:
    """Thread-affine wrapper around the full-screen Magnification API."""

    def __init__(
            self, *, bindings_factory: Callable[[], Any] | None = None,
            logger: logging.Logger | None = None) -> None:
        self._bindings_factory = bindings_factory or _MagnificationBindings
        self._logger = logger or logging.getLogger(__name__)
        self._tasks: queue.Queue[tuple[str, tuple[Any, ...], threading.Event,
                                      dict[str, Any]] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._initialized = False
        self._unavailable = False
        self._closed = False
        self._startup_error: BaseException | None = None
        self._level = MIN_LEVEL
        self._gate = threading.RLock()

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
                    name="OpenSpan.ScreenMagnifier", daemon=True)
                self._thread.start()
        if not self._ready.wait(5.0):
            self._unavailable = True
            return False
        return self._initialized and self._startup_error is None

    def _thread_main(self) -> None:
        bindings = None
        try:
            bindings = self._bindings_factory()
            if not bindings.dll.MagInitialize():
                raise OSError(
                    ctypes.get_last_error(), "MagInitialize failed")
            self._initialized = True
            bindings.dll.MagShowSystemCursor(True)
        except BaseException as exc:
            self._startup_error = exc
            self._unavailable = True
        finally:
            self._ready.set()

        if not self._initialized or bindings is None:
            return
        try:
            while True:
                item = self._tasks.get()
                if item is None:
                    return
                operation, arguments, done, result = item
                try:
                    if operation == "probe":
                        level = ctypes.c_float()
                        x_offset = ctypes.c_int()
                        y_offset = ctypes.c_int()
                        result["value"] = bool(
                            bindings.dll.MagGetFullscreenTransform(
                                ctypes.byref(level), ctypes.byref(x_offset),
                                ctypes.byref(y_offset)))
                    elif operation == "apply":
                        result["value"] = bool(
                            bindings.dll.MagSetFullscreenTransform(*arguments))
                    elif operation == "reset":
                        result["value"] = bool(
                            bindings.dll.MagSetFullscreenTransform(
                                MIN_LEVEL, 0, 0))
                        bindings.dll.MagShowSystemCursor(True)
                except BaseException as exc:
                    result["error"] = exc
                finally:
                    done.set()
        finally:
            try:
                bindings.dll.MagSetFullscreenTransform(MIN_LEVEL, 0, 0)
                bindings.dll.MagShowSystemCursor(True)
            finally:
                bindings.dll.MagUninitialize()
                self._initialized = False

    def _invoke(self, operation: str, *arguments: Any) -> bool:
        if not self._ensure_thread():
            return False
        done = threading.Event()
        result: dict[str, Any] = {}
        self._tasks.put((operation, arguments, done, result))
        if not done.wait(5.0):
            raise TimeoutError("Magnification API call did not finish within 5 seconds.")
        if "error" in result:
            raise result["error"]
        return bool(result.get("value"))

    def probe(self) -> bool:
        try:
            return self._invoke("probe")
        except BaseException as exc:
            self._logger.warning("Screen magnification probe failed: %s", exc)
            self._unavailable = True
            return False

    def apply(
            self, level: float, screen: ScreenRect,
            anchor: tuple[int, int]) -> bool:
        level = clamp_level(level)
        offset_x, offset_y = viewport_offset(screen, anchor, level)
        try:
            applied = self._invoke("apply", level, offset_x, offset_y)
        except BaseException as exc:
            self._logger.warning("Screen magnification failed: %s", exc)
            self._unavailable = True
            return False
        if applied:
            with self._gate:
                self._level = level
        else:
            self._unavailable = True
        return applied

    def reset(self) -> bool:
        try:
            restored = self._invoke("reset")
        except BaseException as exc:
            self._logger.error("Could not restore screen magnification: %s", exc)
            return False
        if restored:
            with self._gate:
                self._level = MIN_LEVEL
        return restored

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.reset()
        finally:
            with self._gate:
                thread = self._thread
                self._closed = True
            if thread is not None and thread.is_alive():
                self._tasks.put(None)
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

    GESTURE_GAP_MS = 400

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
            self._level = MIN_LEVEL
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
                and pointer_monitor is not None
                and self._gesture_monitor == pointer_monitor
            )
            self._last_step_ms = now_ms
            if continuing:
                anchor = self._gesture_anchor
            else:
                self._gesture_anchor = anchor
                self._gesture_monitor = pointer_monitor
            next_level = step_level(self._level, notches, step_factor)
            if next_level <= MIN_LEVEL + 0.001:
                self._level = MIN_LEVEL
                try:
                    return bool(self.magnifier.reset())
                except BaseException as exc:
                    self.last_error = str(exc)
                    return False
            source = pointer_monitor or next(
                (ScreenRect(m.virtual_x, m.virtual_y,
                            m.native_width, m.native_height)
                 for m in monitors
                 if m.virtual_x == 0 and m.virtual_y == 0),
                ScreenRect(0, 0, 1920, 1080))
            try:
                applied = bool(self.magnifier.apply(next_level, source, anchor))
            except BaseException as exc:
                self.last_error = str(exc)
                return False
            if applied:
                self._level = next_level
            return applied

    def describe_active_hooks(self) -> tuple[str, ...]:
        if not self.is_running:
            return ()
        return (
            "WH_MOUSE_LL mouse-wheel hook; modifier observed through the "
            f"central keyboard service ({modifier_name(self.modifier)}).",
        )


def _probe() -> None:
    print("Pure gesture demo (no hooks):")
    gesture = ZoomGesture()
    factor = MIN_LEVEL
    gesture.on_key("Alt", True)
    print(f"  modifier down: armed={gesture.is_armed} factor={factor:.3f}")
    for index in range(1, 4):
        verdict = gesture.on_wheel(1, False)
        if verdict is ZoomVerdict.SWALLOW_AND_ZOOM:
            factor = step_level(factor, 1)
        print(f"  wheel {index}: verdict={verdict.name} factor={factor:.3f}")
    gesture.on_key("Alt", False)
    print(f"  modifier up: armed={gesture.is_armed} factor={factor:.3f}")

    print("Live Magnification API check:")
    magnifier = ScreenMagnifier()
    available = magnifier.probe()
    print(f"  available={available}")
    if not available:
        magnifier.close()
        return
    print("  The screen will briefly zoom to 1.5x for about one second.")
    monitors = attached_identities()
    source = next(
        (ScreenRect(item.virtual_x, item.virtual_y,
                    item.native_width, item.native_height)
         for item in monitors
         if item.virtual_x == 0 and item.virtual_y == 0),
        ScreenRect(0, 0, 1920, 1080))
    anchor = (source.x + source.width // 2,
              source.y + source.height // 2)
    print(f"  before={magnifier.level:.3f}")
    try:
        if magnifier.apply(1.5, source, anchor):
            print(f"  applied={magnifier.level:.3f}")
            time.sleep(1.0)
        else:
            print("  apply refused")
    finally:
        magnifier.reset()
        print(f"  after={magnifier.level:.3f}")
        magnifier.close()


if __name__ == "__main__":
    _probe()


# Wiring notes (intentionally not wired into openspan.py in this slice):
# 1. Register FEATURE_DECLARATION in the shared FeatureRegistry.
# 2. Construct ScreenZoomModule with the shared SettingsService and the existing
#    KeyboardInterceptionService; do not create or start another keyboard hook.
# 3. Call start() only when the disabled-by-default feature is enabled, propagate
#    shortcut suspension through set_suspended(), and always call stop() at exit.
