"""Durable window identity plus an explicit, event-driven Windows tracker.

The pure core at the top of this module imports no Windows bindings.  The
Windows fill opens ctypes and the Win32 DLLs only when one of its functions is
called.  Importing the module and constructing :class:`WindowTracker` therefore
install nothing; ``start()`` is the sole hook-installation boundary.

Window handles and titles are transient.  Persistence and grouping use the
facts that survive process restarts, while the tracker follows the state that
Windows reports and never fabricates a window or a transition.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import ntpath
import time
from collections.abc import Callable, Iterable, Sequence
from typing import Any


# ---- pure core -----------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class WindowIdentity:
    """Durable window facts, with the volatile title retained as a weak signal."""

    executable_path: str
    window_class: str
    title: str = ""
    app_user_model_id: str | None = None
    sibling_index: int = 0

    @staticmethod
    def normalize_path(path: str | None) -> str:
        if path is None or not path.strip():
            return ""
        return path.strip().replace("/", "\\").lower()

    @property
    def executable_name(self) -> str:
        name = ntpath.splitext(ntpath.basename(self.executable_path))[0]
        return name if name else self.executable_path

    @property
    def stable_key(self) -> str:
        app = (self.app_user_model_id
               if self.app_user_model_id is not None
               else self.executable_path)
        seed = f"{app}|{self.window_class}|{self.sibling_index}"
        return hashlib.sha256(seed.encode("utf-8")).digest()[:8].hex()


class MatchStrength(enum.IntEnum):
    NONE = 0
    APPLICATION = 1
    CLASS = 2
    POSITION = 3
    TITLE = 4
    EXACT = 5


def _ordinal_ignore_case(left: str, right: str) -> bool:
    # ``lower`` is the closest stdlib analogue to .NET's non-expanding
    # OrdinalIgnoreCase comparison (``casefold`` would equate "ß" and "ss").
    return left.lower() == right.lower()


def score(remembered: WindowIdentity,
          candidate: WindowIdentity) -> MatchStrength:
    """Score a live candidate; application agreement is always mandatory."""
    if remembered.app_user_model_id:
        same_app = bool(candidate.app_user_model_id) and _ordinal_ignore_case(
            remembered.app_user_model_id, candidate.app_user_model_id)
    else:
        same_app = _ordinal_ignore_case(
            remembered.executable_path, candidate.executable_path)
    if not same_app:
        return MatchStrength.NONE

    if remembered.window_class != candidate.window_class:
        return MatchStrength.APPLICATION

    same_title = bool(remembered.title) and remembered.title == candidate.title
    same_position = remembered.sibling_index == candidate.sibling_index
    if same_title and same_position:
        return MatchStrength.EXACT
    if same_title:
        return MatchStrength.TITLE
    if same_position:
        return MatchStrength.POSITION
    return MatchStrength.CLASS


def assign(remembered: Sequence[WindowIdentity],
           candidates: Sequence[WindowIdentity],
           minimum: MatchStrength = MatchStrength.CLASS) -> dict[int, int]:
    """Greedily assign strongest matches, using input order to break ties."""
    scored: list[tuple[int, int, MatchStrength]] = []
    for remembered_index, old in enumerate(remembered):
        for candidate_index, live in enumerate(candidates):
            strength = score(old, live)
            if strength >= minimum:
                scored.append((remembered_index, candidate_index, strength))
    scored.sort(key=lambda item: (-item[2], item[0], item[1]))

    result: dict[int, int] = {}
    used_candidates: set[int] = set()
    for remembered_index, candidate_index, _ in scored:
        if (remembered_index in result
                or candidate_index in used_candidates):
            continue
        result[remembered_index] = candidate_index
        used_candidates.add(candidate_index)
    return result


class WindowMatching:
    """C#-shaped facade for callers that prefer the original API surface."""

    score = staticmethod(score)
    assign = staticmethod(assign)


@dataclasses.dataclass(frozen=True, slots=True, eq=False)
class AppIdentity:
    key: str
    display_name: str

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, AppIdentity)
                and _ordinal_ignore_case(self.key, other.key))

    def __hash__(self) -> int:
        return hash(self.key.lower())


@dataclasses.dataclass(frozen=True, slots=True)
class ApplicationGroup:
    app: AppIdentity
    windows: tuple[Any, ...]


class ApplicationGrouping:
    """Application-centric grouping independent of process IDs."""

    MULTI_APP_HOSTS = frozenset({
        "chrome", "msedge", "firefox", "brave", "vivaldi", "opera",
        "chromium", "electron", "javaw", "java", "python", "pythonw",
        "wscript", "cscript",
    })

    @staticmethod
    def is_packaged(app_user_model_id: str) -> bool:
        return "!" in app_user_model_id

    @staticmethod
    def _capitalize(value: str) -> str:
        return value[:1].upper() + value[1:] if value else value

    @classmethod
    def _friendly_name_from_aumid(cls, aumid: str, fallback: str) -> str:
        bang = aumid.find("!")
        family = aumid[:bang] if bang > 0 else aumid
        underscore = family.rfind("_")
        if underscore > 0:
            family = family[:underscore]
        dot = family.rfind(".")
        if 0 <= dot < len(family) - 1:
            family = family[dot + 1:]
        return family if family else cls._capitalize(fallback)

    @classmethod
    def resolve(cls, window: WindowIdentity) -> AppIdentity:
        executable_name = window.executable_name
        host_serves_many = executable_name.casefold() in cls.MULTI_APP_HOSTS
        aumid = window.app_user_model_id
        if aumid and (host_serves_many or cls.is_packaged(aumid)):
            return AppIdentity(
                aumid, cls._friendly_name_from_aumid(aumid, executable_name))
        return AppIdentity(window.executable_path,
                           cls._capitalize(executable_name))

    @classmethod
    def group(cls, windows: Iterable[Any],
              identify: Callable[[Any], WindowIdentity]) -> list[ApplicationGroup]:
        order: list[AppIdentity] = []
        buckets: dict[AppIdentity, list[Any]] = {}
        for window in windows:
            app = cls.resolve(identify(window))
            if app not in buckets:
                order.append(app)
                buckets[app] = []
            buckets[app].append(window)
        return [ApplicationGroup(app, tuple(buckets[app])) for app in order]


# ---- Windows fill --------------------------------------------------------------


class WindowRejection(enum.IntEnum):
    NONE = 0
    NOT_A_WINDOW = 1
    INVISIBLE = 2
    CLOAKED = 3
    TOOL_WINDOW = 4
    CHILD_WINDOW = 5
    NOT_RESIZABLE = 6
    SHELL_SURFACE = 7
    OWN_SURFACE = 8
    ACCESS_DENIED = 9


@dataclasses.dataclass(frozen=True, slots=True)
class PixelRect:
    x: int
    y: int
    width: int
    height: int

    @classmethod
    def from_edges(cls, left: int, top: int,
                   right: int, bottom: int) -> PixelRect:
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


@dataclasses.dataclass(frozen=True, slots=True)
class FrameMargins:
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class EnumeratedWindow:
    handle: int
    class_name: str
    bounds: PixelRect | None
    margins: FrameMargins
    cloaked: bool | None


@dataclasses.dataclass(frozen=True, slots=True)
class EnumerationResult:
    windows: tuple[EnumeratedWindow, ...]
    excluded: dict[WindowRejection, int]


@dataclasses.dataclass(frozen=True, slots=True)
class TrackedWindow:
    handle: int
    identity: WindowIdentity
    process_id: int


@dataclasses.dataclass(frozen=True, slots=True)
class WindowAppeared:
    window: TrackedWindow


@dataclasses.dataclass(frozen=True, slots=True)
class WindowVanished:
    handle: int


@dataclasses.dataclass(frozen=True, slots=True)
class WindowRenamed:
    window: TrackedWindow


@dataclasses.dataclass(frozen=True, slots=True)
class WindowMoved:
    window: TrackedWindow


@dataclasses.dataclass(frozen=True, slots=True)
class ForegroundChanged:
    window: TrackedWindow | None


_GWL_STYLE = -16
_GWL_EXSTYLE = -20
_WS_CHILD = 0x40000000
_WS_EX_TOOLWINDOW = 0x00000080
_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_DWMWA_CLOAKED = 14
_SW_RESTORE = 9
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_GA_ROOT = 2

EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_SYSTEM_MOVESIZEEND = 0x000B
EVENT_OBJECT_CREATE = 0x8000
EVENT_OBJECT_DESTROY = 0x8001
EVENT_OBJECT_SHOW = 0x8002
EVENT_OBJECT_HIDE = 0x8003
EVENT_OBJECT_NAMECHANGE = 0x800C
_WINEVENT_OUTOFCONTEXT = 0x0000
_WINEVENT_SKIPOWNPROCESS = 0x0002
_OBJID_WINDOW = 0
_CHILDID_SELF = 0
_PM_REMOVE = 0x0001

_SHELL_CLASSES = frozenset({
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "NotifyIconOverflowWindow",
})

_NATIVE: dict[str, Any] | None = None


def _load_native() -> dict[str, Any]:
    """Build private Win32 prototypes lazily; no shared windll state is changed."""
    global _NATIVE
    if _NATIVE is not None:
        return _NATIVE

    import ctypes
    import ctypes.wintypes as wt

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MSG(ctypes.Structure):
        _fields_ = [("hwnd", wt.HWND), ("message", wt.UINT),
                    ("wParam", wt.WPARAM), ("lParam", wt.LPARAM),
                    ("time", wt.DWORD), ("pt", wt.POINT),
                    ("lPrivate", wt.DWORD)]

    enum_windows_proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    win_event_proc = ctypes.WINFUNCTYPE(
        None, ctypes.c_void_p, wt.DWORD, wt.HWND, ctypes.c_long,
        ctypes.c_long, wt.DWORD, wt.DWORD)

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.EnumWindows.restype = wt.BOOL
    user32.EnumWindows.argtypes = [enum_windows_proc, wt.LPARAM]
    user32.IsWindowVisible.restype = wt.BOOL
    user32.IsWindowVisible.argtypes = [wt.HWND]
    user32.IsWindow.restype = wt.BOOL
    user32.IsWindow.argtypes = [wt.HWND]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetWindowRect.restype = wt.BOOL
    user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
    user32.IsIconic.restype = wt.BOOL
    user32.IsIconic.argtypes = [wt.HWND]
    user32.IsZoomed.restype = wt.BOOL
    user32.IsZoomed.argtypes = [wt.HWND]
    user32.ShowWindow.restype = wt.BOOL
    user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
    user32.SetWindowPos.restype = wt.BOOL
    user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    wt.UINT]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.restype = wt.DWORD
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND,
                                                ctypes.POINTER(wt.DWORD)]
    user32.GetAncestor.restype = wt.HWND
    user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
    user32.SetWinEventHook.restype = ctypes.c_void_p
    user32.SetWinEventHook.argtypes = [wt.DWORD, wt.DWORD, wt.HMODULE,
                                       win_event_proc, wt.DWORD, wt.DWORD,
                                       wt.DWORD]
    user32.UnhookWinEvent.restype = wt.BOOL
    user32.UnhookWinEvent.argtypes = [ctypes.c_void_p]
    user32.PeekMessageW.restype = wt.BOOL
    user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), wt.HWND,
                                    wt.UINT, wt.UINT, wt.UINT]
    user32.TranslateMessage.restype = wt.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    user32.DispatchMessageW.restype = ctypes.c_ssize_t
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]

    dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
    dwmapi.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD,
                                             ctypes.c_void_p, wt.DWORD]

    kernel32.OpenProcess.restype = wt.HANDLE
    kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD,
                                                    wt.LPWSTR,
                                                    ctypes.POINTER(wt.DWORD)]
    kernel32.CloseHandle.restype = wt.BOOL
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    get_aumid = getattr(kernel32, "GetApplicationUserModelId", None)
    if get_aumid is not None:
        get_aumid.restype = ctypes.c_long
        get_aumid.argtypes = [wt.HANDLE, ctypes.POINTER(wt.UINT), wt.LPWSTR]

    _NATIVE = {
        "ctypes": ctypes, "wt": wt, "user32": user32, "dwmapi": dwmapi,
        "kernel32": kernel32, "RECT": RECT, "MSG": MSG,
        "ENUM_WINDOWS_PROC": enum_windows_proc,
        "WIN_EVENT_PROC": win_event_proc,
        "GET_AUMID": get_aumid,
    }
    return _NATIVE


class WindowService:
    """Read-only enumeration/classification and DWM-compensated placement."""

    def is_cloaked(self, handle: int) -> bool | None:
        native = _load_native()
        ctypes, wt = native["ctypes"], native["wt"]
        value = wt.DWORD()
        result = native["dwmapi"].DwmGetWindowAttribute(
            handle, _DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
        return bool(value.value) if result == 0 else None

    def classify(self, handle: int) -> WindowRejection:
        if not handle:
            return WindowRejection.NOT_A_WINDOW
        native = _load_native()
        user32 = native["user32"]
        if not user32.IsWindowVisible(handle):
            return WindowRejection.INVISIBLE

        if self.is_cloaked(handle) is True:
            return WindowRejection.CLOAKED

        style = int(user32.GetWindowLongPtrW(handle, _GWL_STYLE))
        extended_style = int(user32.GetWindowLongPtrW(handle, _GWL_EXSTYLE))
        if style & _WS_CHILD:
            return WindowRejection.CHILD_WINDOW
        if extended_style & _WS_EX_TOOLWINDOW:
            return WindowRejection.TOOL_WINDOW
        if self.get_class_name(handle) in _SHELL_CLASSES:
            return WindowRejection.SHELL_SURFACE
        return WindowRejection.NONE

    @staticmethod
    def get_class_name(handle: int) -> str:
        native = _load_native()
        ctypes = native["ctypes"]
        buffer = ctypes.create_unicode_buffer(256)
        length = native["user32"].GetClassNameW(handle, buffer, len(buffer))
        return buffer[:length] if length > 0 else ""

    @staticmethod
    def get_process_id(handle: int) -> int:
        native = _load_native()
        pid = native["wt"].DWORD()
        native["user32"].GetWindowThreadProcessId(
            handle, native["ctypes"].byref(pid))
        return int(pid.value)

    def get_bounds(self, handle: int) -> tuple[PixelRect, FrameMargins] | None:
        native = _load_native()
        ctypes, RECT = native["ctypes"], native["RECT"]
        window_rect = RECT()
        if not native["user32"].GetWindowRect(handle,
                                                ctypes.byref(window_rect)):
            return None
        window = PixelRect.from_edges(
            window_rect.left, window_rect.top,
            window_rect.right, window_rect.bottom)

        frame_rect = RECT()
        result = native["dwmapi"].DwmGetWindowAttribute(
            handle, _DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(frame_rect), ctypes.sizeof(frame_rect))
        if result != 0:
            return window, FrameMargins()

        frame = PixelRect.from_edges(
            frame_rect.left, frame_rect.top, frame_rect.right, frame_rect.bottom)
        margins = FrameMargins(
            frame.left - window.left,
            frame.top - window.top,
            window.right - frame.right,
            window.bottom - frame.bottom,
        )
        return frame, margins

    def place(self, handle: int, target: PixelRect) -> bool:
        native = _load_native()
        user32 = native["user32"]
        if user32.IsIconic(handle) or user32.IsZoomed(handle):
            user32.ShowWindow(handle, _SW_RESTORE)

        measured = self.get_bounds(handle)
        if measured is None:
            return False
        _, margins = measured
        return bool(user32.SetWindowPos(
            handle, None,
            target.x - margins.left,
            target.y - margins.top,
            target.width + margins.left + margins.right,
            target.height + margins.top + margins.bottom,
            _SWP_NOZORDER | _SWP_NOACTIVATE))

    def enumerate(self) -> EnumerationResult:
        native = _load_native()
        handles: list[int] = []
        callback_error: list[BaseException] = []

        def collect(handle: int, data: int) -> bool:
            try:
                handles.append(int(handle))
                return True
            except BaseException as exc:
                callback_error.append(exc)
                return False

        callback = native["ENUM_WINDOWS_PROC"](collect)
        succeeded = native["user32"].EnumWindows(callback, 0)
        if callback_error:
            raise RuntimeError("EnumWindows callback failed") from callback_error[0]
        if not succeeded:
            error = native["ctypes"].get_last_error()
            if error:
                raise OSError(error, "EnumWindows failed")

        excluded = {reason: 0 for reason in WindowRejection
                    if reason is not WindowRejection.NONE}
        windows: list[EnumeratedWindow] = []
        for handle in handles:
            rejection = self.classify(handle)
            if rejection is not WindowRejection.NONE:
                excluded[rejection] += 1
                continue
            measured = self.get_bounds(handle)
            bounds, margins = measured if measured else (None, FrameMargins())
            windows.append(EnumeratedWindow(
                handle=handle,
                class_name=self.get_class_name(handle),
                bounds=bounds,
                margins=margins,
                cloaked=self.is_cloaked(handle),
            ))
        return EnumerationResult(tuple(windows), excluded)


class WindowTracker:
    """Live model driven only by WinEvent notifications and an initial scan."""

    _HOOK_RANGES = (
        (EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND),
        (EVENT_SYSTEM_MOVESIZEEND, EVENT_SYSTEM_MOVESIZEEND),
        (EVENT_OBJECT_CREATE, EVENT_OBJECT_HIDE),
        (EVENT_OBJECT_NAMECHANGE, EVENT_OBJECT_NAMECHANGE),
    )

    def __init__(self, service: WindowService | None = None,
                 on_event: Callable[[object], None] | None = None):
        self._service = service if service is not None else WindowService()
        self._on_event = on_event
        self._tracked: dict[int, TrackedWindow] = {}
        self._process_cache: dict[int, tuple[str, str | None]] = {}
        self._hooks: list[int] = []
        self._callback: Any = None
        self._started = False
        self._event_count = 0
        self.start_errors: list[str] = []
        self.callback_errors: list[str] = []

    @property
    def windows(self) -> tuple[TrackedWindow, ...]:
        return tuple(self._tracked.values())

    @property
    def event_count(self) -> int:
        return self._event_count

    def get(self, handle: int) -> TrackedWindow | None:
        return self._tracked.get(handle)

    def start(self) -> int:
        """Scan reality, then install the four out-of-context WinEvent hooks."""
        if self._started:
            return len(self._hooks)
        native = _load_native()
        self.scan()
        self._callback = native["WIN_EVENT_PROC"](self._callback_boundary)
        for first_event, last_event in self._HOOK_RANGES:
            hook = native["user32"].SetWinEventHook(
                first_event, last_event, None, self._callback, 0, 0,
                _WINEVENT_OUTOFCONTEXT | _WINEVENT_SKIPOWNPROCESS)
            if hook:
                self._hooks.append(int(hook))
            else:
                error = native["ctypes"].get_last_error()
                self.start_errors.append(
                    f"SetWinEventHook {first_event:#06x}-{last_event:#06x} "
                    f"failed ({error})")
        self._started = True
        return len(self._hooks)

    def stop(self) -> None:
        if not self._hooks and not self._started:
            return
        native = _load_native()
        for hook in self._hooks:
            native["user32"].UnhookWinEvent(hook)
        self._hooks.clear()
        self._callback = None
        self._started = False

    def scan(self) -> None:
        native = _load_native()
        callback_error: list[BaseException] = []

        def visit(handle: int, data: int) -> bool:
            try:
                self._try_track(int(handle))
                return True
            except BaseException as exc:
                callback_error.append(exc)
                return False

        callback = native["ENUM_WINDOWS_PROC"](visit)
        succeeded = native["user32"].EnumWindows(callback, 0)
        if callback_error:
            raise RuntimeError("window scan callback failed") from callback_error[0]
        if not succeeded:
            error = native["ctypes"].get_last_error()
            if error:
                raise OSError(error, "EnumWindows failed during scan")

    def _callback_boundary(self, hook: int, event: int, handle: int,
                           object_id: int, child_id: int,
                           thread_id: int, event_time: int) -> None:
        """Never allow a Python exception to cross the native callback boundary."""
        try:
            self._event_count += 1
            self._handle_win_event(event, int(handle or 0),
                                   object_id, child_id)
        except BaseException as exc:
            try:
                self.callback_errors.append(
                    f"event {event:#06x}: {type(exc).__name__}: {exc}")
            except BaseException:
                pass

    def _handle_win_event(self, event: int, handle: int,
                          object_id: int, child_id: int) -> None:
        if object_id != _OBJID_WINDOW or child_id != _CHILDID_SELF or not handle:
            return
        if event in (EVENT_OBJECT_CREATE, EVENT_OBJECT_SHOW):
            self._try_track(handle)
            return
        if event == EVENT_OBJECT_DESTROY:
            if self._tracked.pop(handle, None) is not None:
                self._emit(WindowVanished(handle))
            return
        if event == EVENT_OBJECT_HIDE:
            if not _load_native()["user32"].IsWindow(handle):
                if self._tracked.pop(handle, None) is not None:
                    self._emit(WindowVanished(handle))
            return
        if event == EVENT_OBJECT_NAMECHANGE:
            existing = self._tracked.get(handle)
            if existing is not None:
                renamed = dataclasses.replace(
                    existing,
                    identity=dataclasses.replace(
                        existing.identity, title=self._get_title(handle)))
                self._tracked[handle] = renamed
                self._emit(WindowRenamed(renamed))
            return
        if event == EVENT_SYSTEM_MOVESIZEEND:
            moved = self._tracked.get(handle)
            if moved is not None:
                self._emit(WindowMoved(moved))
            return
        if event == EVENT_SYSTEM_FOREGROUND:
            root = _load_native()["user32"].GetAncestor(handle, _GA_ROOT)
            root_handle = int(root or 0)
            window = self._tracked.get(root_handle)
            if window is None:
                window = self._try_track(root_handle)
            self._emit(ForegroundChanged(window))

    def _try_track(self, handle: int) -> TrackedWindow | None:
        if not handle:
            return None
        existing = self._tracked.get(handle)
        if existing is not None:
            return existing
        if self._service.classify(handle) is not WindowRejection.NONE:
            return None
        process_id = self._service.get_process_id(handle)
        if not process_id:
            return None
        if process_id not in self._process_cache:
            self._process_cache[process_id] = self._resolve_process(process_id)
        executable_path, aumid = self._process_cache[process_id]
        if not executable_path:
            return None

        normalized = WindowIdentity.normalize_path(executable_path)
        window_class = self._service.get_class_name(handle)
        sibling_index = sum(
            tracked.identity.executable_path == normalized
            and tracked.identity.window_class == window_class
            for tracked in self._tracked.values())
        tracked = TrackedWindow(
            handle,
            WindowIdentity(
                executable_path=normalized,
                window_class=window_class,
                title=self._get_title(handle),
                app_user_model_id=aumid,
                sibling_index=sibling_index,
            ),
            process_id,
        )
        self._tracked[handle] = tracked
        self._emit(WindowAppeared(tracked))
        return tracked

    @staticmethod
    def _get_title(handle: int) -> str:
        native = _load_native()
        buffer = native["ctypes"].create_unicode_buffer(512)
        length = native["user32"].GetWindowTextW(handle, buffer, len(buffer))
        return buffer[:length] if length > 0 else ""

    @staticmethod
    def _resolve_process(process_id: int) -> tuple[str, str | None]:
        native = _load_native()
        ctypes, wt, kernel32 = (native["ctypes"], native["wt"],
                                native["kernel32"])
        process = kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
        if not process:
            return "", None
        try:
            path_buffer = ctypes.create_unicode_buffer(1024)
            path_size = wt.DWORD(len(path_buffer))
            if kernel32.QueryFullProcessImageNameW(
                    process, 0, path_buffer, ctypes.byref(path_size)):
                path = path_buffer[:path_size.value]
            else:
                path = ""

            aumid = None
            get_aumid = native["GET_AUMID"]
            if get_aumid is not None:
                aumid_buffer = ctypes.create_unicode_buffer(512)
                aumid_size = wt.UINT(len(aumid_buffer))
                if get_aumid(process, ctypes.byref(aumid_size), aumid_buffer) == 0:
                    aumid = (aumid_buffer[:max(0, aumid_size.value - 1)]
                             if aumid_size.value > 1 else None)
            return path, aumid
        finally:
            kernel32.CloseHandle(process)

    def _emit(self, event: object) -> None:
        if self._on_event is not None:
            self._on_event(event)

    def __enter__(self) -> WindowTracker:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.stop()


def _pump_messages(seconds: float) -> None:
    native = _load_native()
    ctypes, user32 = native["ctypes"], native["user32"]
    message = native["MSG"]()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, _PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        time.sleep(0.01)


def main() -> int:
    service = WindowService()
    desktop = service.enumerate()
    for window in desktop.windows:
        if window.bounds is None:
            bounds = "unavailable"
        else:
            rect = window.bounds
            bounds = f"({rect.left},{rect.top},{rect.right},{rect.bottom})"
        cloaked = "unknown" if window.cloaked is None else str(int(window.cloaked))
        print(f"WINDOW class={window.class_name!r} bounds={bounds} cloaked={cloaked}")
    for reason in WindowRejection:
        if reason is not WindowRejection.NONE:
            print(f"EXCLUDED {reason.name.lower()}={desktop.excluded[reason]}")

    tracker = WindowTracker(service)
    try:
        tracker.start()
        _pump_messages(3.0)
    finally:
        tracker.stop()
    print(f"WINEVENTS arrived={tracker.event_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
