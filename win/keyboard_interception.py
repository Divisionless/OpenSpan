"""Central keyboard routing and optional Windows interception.

The running product already captures the keyboard through its portal pipeline,
and Input Director also hooks input on this machine.  Consequently this module
installs nothing on import and nothing on construction.  Hook installation is
an explicit :meth:`KeyboardInterceptionService.start` operation for a later app
slice, guarded by the process-wide single-owner policy below.

The chord model, router, and ownership policy are pure Python.  The Windows
fill imports ``ctypes`` only inside functions so the core remains importable and
unit-testable on non-Windows systems.
"""

from __future__ import annotations

import dataclasses
import enum
import os
import queue
import threading
import time
from collections.abc import Callable, Sequence
from typing import Protocol


# ---- pure core ---------------------------------------------------------------


class ChordModifiers(enum.IntFlag):
    NONE = 0
    WIN = 1
    CTRL = 2
    ALT = 4
    SHIFT = 8


_CANONICAL_KEYS = {
    "left": "Left", "leftarrow": "Left",
    "right": "Right", "rightarrow": "Right",
    "up": "Up", "uparrow": "Up",
    "down": "Down", "downarrow": "Down",
    "space": "Space", "spacebar": "Space",
    "enter": "Enter", "return": "Enter",
    "esc": "Escape", "escape": "Escape",
    "tab": "Tab", "backspace": "Backspace",
    "delete": "Delete", "del": "Delete",
    "insert": "Insert", "ins": "Insert",
    "home": "Home", "end": "End",
    "pageup": "PageUp", "pgup": "PageUp",
    "pagedown": "PageDown", "pgdn": "PageDown",
    "printscreen": "PrintScreen", "prtscn": "PrintScreen",
    "prtsc": "PrintScreen",
    "grave": "Grave", "backtick": "Grave", "`": "Grave",
    "tilde": "Grave",
    "minus": "Minus", "-": "Minus",
    "equals": "Equals", "=": "Equals", "plus": "Equals",
    "comma": "Comma", ",": "Comma",
    "period": "Period", ".": "Period", "dot": "Period",
    "slash": "Slash", "/": "Slash",
    "backslash": "Backslash", "\\": "Backslash",
    "semicolon": "Semicolon", ";": "Semicolon",
    "quote": "Quote", "'": "Quote", "apostrophe": "Quote",
    "leftbracket": "LeftBracket", "[": "LeftBracket",
    "rightbracket": "RightBracket", "]": "RightBracket",
}
_CANONICAL_KEYS.update({f"numpad{i}": f"Numpad{i}" for i in range(10)})
_CANONICAL_KEYS.update({f"num{i}": f"Numpad{i}" for i in range(10)})


def canonical_key(token: str) -> str:
    """Normalize a plan/user key token to its physical-key spelling."""
    compact = token.strip().replace(" ", "").lower()
    known = _CANONICAL_KEYS.get(compact)
    if known is not None:
        return known
    if len(compact) == 1 and compact.isascii() and compact.isalnum():
        return compact.upper()
    if compact.startswith("f") and compact[1:].isdigit():
        number = int(compact[1:])
        if 1 <= number <= 24 and len(compact) in (2, 3):
            return f"F{number}"
    raise ValueError(f"Unknown key token '{token}'.")


def display_key(key: str) -> str:
    if key.startswith("Numpad") and len(key) == 7:
        return f"Numpad {key[6]}"
    return key


@dataclasses.dataclass(frozen=True, slots=True)
class KeyChord:
    modifiers: ChordModifiers
    key: str

    def __str__(self) -> str:
        parts = [name for flag, name in (
            (ChordModifiers.WIN, "Win"),
            (ChordModifiers.CTRL, "Ctrl"),
            (ChordModifiers.ALT, "Alt"),
            (ChordModifiers.SHIFT, "Shift"),
        ) if self.modifiers & flag]
        return "+".join((*parts, display_key(self.key)))


_MODIFIER_NAMES = {
    "win": ChordModifiers.WIN,
    "windows": ChordModifiers.WIN,
    "super": ChordModifiers.WIN,
    "meta": ChordModifiers.WIN,
    "ctrl": ChordModifiers.CTRL,
    "control": ChordModifiers.CTRL,
    "alt": ChordModifiers.ALT,
    "option": ChordModifiers.ALT,
    "shift": ChordModifiers.SHIFT,
}


def _parse_chord(text: str) -> KeyChord:
    tokens = [token.strip() for token in text.split("+") if token.strip()]
    if not tokens:
        raise ValueError(f"Empty chord in '{text}'.")
    modifiers = ChordModifiers.NONE
    for token in tokens[:-1]:
        modifier = _MODIFIER_NAMES.get(token.lower())
        if modifier is None:
            raise ValueError(f"Unknown modifier '{token}' in '{text}'.")
        modifiers |= modifier
    return KeyChord(modifiers, canonical_key(tokens[-1]))


@dataclasses.dataclass(frozen=True, slots=True)
class KeySequence:
    chords: tuple[KeyChord, ...]

    SEPARATOR = ", then "

    def __init__(self, chords: Sequence[KeyChord]):
        object.__setattr__(self, "chords", tuple(chords))

    @property
    def is_single(self) -> bool:
        return len(self.chords) == 1

    @property
    def first(self) -> KeyChord:
        return self.chords[0]

    def __str__(self) -> str:
        return self.SEPARATOR.join(map(str, self.chords))

    def is_prefix_of(self, other: KeySequence) -> bool:
        return (len(self.chords) < len(other.chords)
                and self.chords == other.chords[:len(self.chords)])

    @classmethod
    def parse(cls, text: str) -> KeySequence:
        if not text or not text.strip():
            raise ValueError("Empty key sequence.")
        if cls.SEPARATOR in text:
            parts = text.split(cls.SEPARATOR)
        elif ", Then " in text:
            parts = text.split(", Then ")
        else:
            parts = [text]
        return cls(_parse_chord(part.strip()) for part in parts)

    @classmethod
    def try_parse(cls, text: str) -> tuple[bool, KeySequence | None]:
        try:
            return True, cls.parse(text)
        except ValueError:
            return False, None


PROTECTED_CHORDS = frozenset({
    KeyChord(ChordModifiers.CTRL | ChordModifiers.ALT, "Delete"),
    KeyChord(ChordModifiers.WIN, "L"),
    KeyChord(ChordModifiers.ALT, "F4"),
    KeyChord(ChordModifiers.WIN | ChordModifiers.CTRL |
             ChordModifiers.SHIFT, "B"),
    KeyChord(ChordModifiers.NONE, "F12"),
})


def is_protected(chord: KeyChord) -> bool:
    return chord in PROTECTED_CHORDS


class KeyboardRoutingVerdictKind(enum.Enum):
    PASS_THROUGH = enum.auto()
    SWALLOW = enum.auto()
    SWALLOW_WITH_ACTION = enum.auto()


@dataclasses.dataclass(frozen=True, slots=True)
class KeyboardRoutingVerdict:
    kind: KeyboardRoutingVerdictKind
    action: Callable[[], None] | None = None
    consumer_id: str | None = None

    @classmethod
    def pass_through(cls) -> KeyboardRoutingVerdict:
        return cls(KeyboardRoutingVerdictKind.PASS_THROUGH)

    @classmethod
    def swallow(cls, consumer_id: str) -> KeyboardRoutingVerdict:
        return cls(KeyboardRoutingVerdictKind.SWALLOW,
                   consumer_id=consumer_id)

    @classmethod
    def swallow_with_action(
            cls, action: Callable[[], None],
            consumer_id: str) -> KeyboardRoutingVerdict:
        return cls(KeyboardRoutingVerdictKind.SWALLOW_WITH_ACTION,
                   action, consumer_id)


@dataclasses.dataclass(frozen=True, slots=True)
class RawKeyboardEvent:
    vk_code: int
    scan_code: int
    is_down: bool
    is_injected: bool
    extra_info: int
    canonical_key: str


class KeyboardConsumer(Protocol):
    consumer_id: str
    priority: int

    def process_key_event(
            self, event: RawKeyboardEvent,
            current_modifiers: ChordModifiers) -> KeyboardRoutingVerdict: ...


class KeyboardRouter:
    """Pure decision core; it performs no Windows calls and takes no locks."""

    ESOTERICOS_EXTRA_INFO_SIGNATURE = 0x45534F54  # "ESOT"

    def __init__(self) -> None:
        self.l_shift = self.r_shift = False
        self.l_ctrl = self.r_ctrl = False
        self.l_alt = self.r_alt = False
        self.l_win = self.r_win = False
        self.is_suspended = False
        self.hooks_allowed = True
        self.has_input_capture_lease = False
        self._swallowed_down_ownership: dict[int, str] = {}
        self._consumed_win_chord_while_win_held = False
        self._win_release_mask_pending = False
        self._consumers: list[KeyboardConsumer] = []

    @property
    def shift_held(self) -> bool:
        return self.l_shift or self.r_shift

    @property
    def ctrl_held(self) -> bool:
        return self.l_ctrl or self.r_ctrl

    @property
    def alt_held(self) -> bool:
        return self.l_alt or self.r_alt

    @property
    def win_held(self) -> bool:
        return self.l_win or self.r_win

    @property
    def active_modifiers(self) -> ChordModifiers:
        modifiers = ChordModifiers.NONE
        if self.win_held:
            modifiers |= ChordModifiers.WIN
        if self.ctrl_held:
            modifiers |= ChordModifiers.CTRL
        if self.alt_held:
            modifiers |= ChordModifiers.ALT
        if self.shift_held:
            modifiers |= ChordModifiers.SHIFT
        return modifiers

    @property
    def should_mask_win_release(self) -> bool:
        if self._win_release_mask_pending:
            self._win_release_mask_pending = False
            return True
        return self._consumed_win_chord_while_win_held

    def register_consumer(self, consumer: KeyboardConsumer) -> None:
        self._consumers.append(consumer)
        self._consumers.sort(key=lambda item: item.priority)

    def unregister_consumer(self, consumer_id: str) -> None:
        self._consumers[:] = [consumer for consumer in self._consumers
                             if consumer.consumer_id != consumer_id]

    def clear_consumers(self) -> None:
        self._consumers.clear()

    def route_key_event(
            self, event: RawKeyboardEvent) -> KeyboardRoutingVerdict:
        self._update_modifier_state(event)

        if (event.is_injected or
                event.extra_info == self.ESOTERICOS_EXTRA_INFO_SIGNATURE):
            return KeyboardRoutingVerdict.pass_through()

        if (self.is_suspended or not self.hooks_allowed or
                self.has_input_capture_lease):
            return KeyboardRoutingVerdict.pass_through()

        modifiers = self.active_modifiers
        if is_protected(KeyChord(modifiers, event.canonical_key)):
            return KeyboardRoutingVerdict.pass_through()

        if not event.is_down:
            mask_was_needed = False
            if event.canonical_key == "Win":
                mask_was_needed = self._consumed_win_chord_while_win_held
                self._consumed_win_chord_while_win_held = False

            owner_id = self._swallowed_down_ownership.pop(
                event.vk_code, None)
            if owner_id is not None:
                owner = next((consumer for consumer in self._consumers
                              if consumer.consumer_id == owner_id), None)
                if owner is not None:
                    verdict = owner.process_key_event(event, modifiers)
                    self._notify_remaining(owner.consumer_id, event, modifiers)
                    if verdict.kind is not KeyboardRoutingVerdictKind.PASS_THROUGH:
                        return verdict
                    return KeyboardRoutingVerdict.swallow(owner_id)
                self._notify_all(event, modifiers)
                return KeyboardRoutingVerdict.swallow(owner_id)

            self._notify_all(event, modifiers)
            if mask_was_needed:
                self._win_release_mask_pending = True
            return KeyboardRoutingVerdict.pass_through()

        for consumer in self._consumers:
            verdict = consumer.process_key_event(event, modifiers)
            if verdict.kind is not KeyboardRoutingVerdictKind.PASS_THROUGH:
                self._swallowed_down_ownership[event.vk_code] = (
                    consumer.consumer_id)
                if modifiers & ChordModifiers.WIN:
                    self._consumed_win_chord_while_win_held = True
                return verdict
        return KeyboardRoutingVerdict.pass_through()

    def _notify_all(self, event: RawKeyboardEvent,
                    modifiers: ChordModifiers) -> None:
        for consumer in self._consumers:
            consumer.process_key_event(event, modifiers)

    def _notify_remaining(self, excluded_id: str, event: RawKeyboardEvent,
                          modifiers: ChordModifiers) -> None:
        for consumer in self._consumers:
            if consumer.consumer_id != excluded_id:
                consumer.process_key_event(event, modifiers)

    def _update_modifier_state(self, event: RawKeyboardEvent) -> None:
        vk = event.vk_code
        if vk == 0xA0:
            self.l_shift = event.is_down
        elif vk == 0xA1:
            self.r_shift = event.is_down
        elif vk == 0x10:
            if not event.is_down:
                self.l_shift = self.r_shift = False
            elif not self.shift_held:
                self.l_shift = True
        elif vk == 0xA2:
            self.l_ctrl = event.is_down
        elif vk == 0xA3:
            self.r_ctrl = event.is_down
        elif vk == 0x11:
            if not event.is_down:
                self.l_ctrl = self.r_ctrl = False
            elif not self.ctrl_held:
                self.l_ctrl = True
        elif vk == 0xA4:
            self.l_alt = event.is_down
        elif vk == 0xA5:
            self.r_alt = event.is_down
        elif vk == 0x12:
            if not event.is_down:
                self.l_alt = self.r_alt = False
            elif not self.alt_held:
                self.l_alt = True
        elif vk == 0x5B:
            self.l_win = event.is_down
        elif vk == 0x5C:
            self.r_win = event.is_down

    def reset(self) -> None:
        self.l_shift = self.r_shift = False
        self.l_ctrl = self.r_ctrl = False
        self.l_alt = self.r_alt = False
        self.l_win = self.r_win = False
        self._swallowed_down_ownership.clear()
        self._consumed_win_chord_while_win_held = False
        self._win_release_mask_pending = False


class InputHookPolicy:
    DISABLE_VARIABLE = "ESOTERICOS_NO_INPUT_HOOKS"
    VETO_REASON = (
        "Low-level input hooks are disabled for this session by "
        "ESOTERICOS_NO_INPUT_HOOKS."
    )

    @classmethod
    def hooks_allowed(cls) -> bool:
        value = os.environ.get(cls.DISABLE_VARIABLE)
        return not (value and value.strip() and value != "0"
                    and value.lower() != "false")


class SingleHookOwnerPolicy:
    """Process-local gate enforcing one WH_KEYBOARD_LL owner at a time."""

    _gate = threading.Lock()
    _owner: object | None = None

    @classmethod
    def claim(cls, owner: object) -> bool:
        with cls._gate:
            if cls._owner is not None and cls._owner is not owner:
                return False
            cls._owner = owner
            return True

    @classmethod
    def release(cls, owner: object) -> None:
        with cls._gate:
            if cls._owner is owner:
                cls._owner = None

    @classmethod
    def is_owner(cls, owner: object) -> bool:
        with cls._gate:
            return cls._owner is owner


# ---- Windows fill ------------------------------------------------------------


def vk_to_canonical(vk_code: int) -> str:
    named = {
        0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
        0x20: "Space", 0x0D: "Enter", 0x1B: "Escape", 0x09: "Tab",
        0x08: "Backspace", 0x2E: "Delete", 0x2D: "Insert",
        0x24: "Home", 0x23: "End", 0x21: "PageUp", 0x22: "PageDown",
        0x2C: "PrintScreen", 0x5B: "Win", 0x5C: "Win",
        0x10: "Shift", 0xA0: "Shift", 0xA1: "Shift",
        0x11: "Ctrl", 0xA2: "Ctrl", 0xA3: "Ctrl",
        0x12: "Alt", 0xA4: "Alt", 0xA5: "Alt",
    }
    if vk_code in named:
        return named[vk_code]
    if 0x41 <= vk_code <= 0x5A or 0x30 <= vk_code <= 0x39:
        return chr(vk_code)
    if 0x70 <= vk_code <= 0x87:
        return f"F{vk_code - 0x70 + 1}"
    if 0x60 <= vk_code <= 0x69:
        return f"Numpad{vk_code - 0x60}"
    return f"VK_{vk_code}"


@dataclasses.dataclass(slots=True)
class _WindowsBindings:
    ctypes: object
    user32: object
    kernel32: object
    hook_proc_type: object
    keyboard_data_type: object
    input_type: object
    keybdinput_type: object
    msg_type: object


def _windows_bindings() -> _WindowsBindings:
    import ctypes
    from ctypes import wintypes

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 32)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("data",)
        _fields_ = [("type", wintypes.DWORD), ("data", INPUTUNION)]

    hook_proc_type = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int, hook_proc_type, ctypes.c_void_p, wintypes.DWORD]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.CallNextHookEx.argtypes = [
        ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendInput.restype = wintypes.UINT
    user32.SendInput.argtypes = [
        wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND,
        wintypes.UINT, wintypes.UINT]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND,
        wintypes.UINT, wintypes.UINT, wintypes.UINT]
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    user32.PostThreadMessageW.argtypes = [
        wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    return _WindowsBindings(ctypes, user32, kernel32, hook_proc_type,
                            KBDLLHOOKSTRUCT, INPUT, KEYBDINPUT, wintypes.MSG)


class KeyboardInterceptionService:
    """The sole lawful WH_KEYBOARD_LL owner; consumers register here."""

    WH_KEYBOARD_LL = 13
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    WM_SYSKEYDOWN = 0x0104
    WM_SYSKEYUP = 0x0105
    WM_QUIT = 0x0012
    LLKHF_INJECTED = 0x10
    LLKHF_LOWER_IL_INJECTED = 0x02

    def __init__(self, router: KeyboardRouter | None = None) -> None:
        self.router = router or KeyboardRouter()
        self._consumers: list[KeyboardConsumer] = []
        self._gate = threading.RLock()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hook: int | None = None
        self._callback: object | None = None
        self._started = threading.Event()
        self._startup_error: BaseException | None = None
        self._stopping = False
        self._observe_only = False
        self._action_queue: queue.SimpleQueue[Callable[[], None] | None] = (
            queue.SimpleQueue())
        self._action_thread: threading.Thread | None = None
        self.observed_event_count = 0
        self.observed_injected = False

    @property
    def is_hook_installed(self) -> bool:
        return self._hook is not None

    @property
    def active_consumer_ids(self) -> tuple[str, ...]:
        with self._gate:
            return tuple(consumer.consumer_id for consumer in self._consumers)

    def register_consumer(self, consumer: KeyboardConsumer) -> None:
        with self._gate:
            self._consumers[:] = [item for item in self._consumers
                                 if item.consumer_id != consumer.consumer_id]
            self._consumers.append(consumer)
            self.router.unregister_consumer(consumer.consumer_id)
            self.router.register_consumer(consumer)

    def unregister_consumer(self, consumer_id: str) -> None:
        with self._gate:
            self._consumers[:] = [item for item in self._consumers
                                 if item.consumer_id != consumer_id]
            self.router.unregister_consumer(consumer_id)

    def start(self, *, observe_only: bool = False) -> None:
        """Explicitly claim the one-hook gate and start the message-pump thread."""
        with self._gate:
            if self._thread is not None and self._thread.is_alive():
                if self._observe_only != observe_only:
                    raise RuntimeError("Keyboard hook already started in another mode.")
                return
            if os.name != "nt":
                raise OSError("WH_KEYBOARD_LL is available only on Windows.")
            if not InputHookPolicy.hooks_allowed():
                raise PermissionError(InputHookPolicy.VETO_REASON)
            if not SingleHookOwnerPolicy.claim(self):
                raise RuntimeError(
                    "A WH_KEYBOARD_LL owner is already active in this process.")

            self._observe_only = observe_only
            self._stopping = False
            self._startup_error = None
            self._started.clear()
            self._action_queue = queue.SimpleQueue()
            self._action_thread = threading.Thread(
                target=self._action_main,
                name="EsotericOS.KeyboardActions", daemon=True)
            self._action_thread.start()
            self._thread = threading.Thread(
                target=self._thread_main,
                name="EsotericOS.KeyboardInterceptionService", daemon=True)
            try:
                self._thread.start()
            except BaseException:
                self._action_queue.put(None)
                SingleHookOwnerPolicy.release(self)
                self._thread = None
                raise

        if not self._started.wait(5.0):
            self.stop()
            raise TimeoutError("Keyboard hook thread did not start within 5 seconds.")
        if self._startup_error is not None:
            error = self._startup_error
            self.stop()
            raise error

    def stop(self) -> None:
        """Uninstall the hook on its owning thread and reset router state."""
        with self._gate:
            thread = self._thread
            if thread is None:
                SingleHookOwnerPolicy.release(self)
                return
            self._stopping = True
            thread_id = self._thread_id
        if thread_id:
            try:
                bindings = _windows_bindings()
                bindings.user32.PostThreadMessageW(
                    thread_id, self.WM_QUIT, 0, 0)
            except BaseException:
                pass
        thread.join(3.0)
        if thread.is_alive():
            raise TimeoutError(
                "Keyboard interception hook thread did not exit within 3 seconds.")
        with self._gate:
            self._thread = None
            self._thread_id = 0
            self.router.reset()
        action_thread = self._action_thread
        self._action_queue.put(None)
        if action_thread is not None:
            action_thread.join(3.0)
        self._action_thread = None

    def describe_active_hooks(self) -> tuple[str, ...]:
        if not self.is_hook_installed:
            return ()
        mode = "observe-only" if self._observe_only else "central routing"
        return (f"WH_KEYBOARD_LL keyboard interception ({mode}).",)

    def dispose(self) -> None:
        self.stop()
        with self._gate:
            self._consumers.clear()
            self.router.clear_consumers()

    def _action_main(self) -> None:
        while True:
            action = self._action_queue.get()
            if action is None:
                return
            try:
                action()
            except BaseException:
                pass

    def _thread_main(self) -> None:
        bindings: _WindowsBindings | None = None
        try:
            bindings = _windows_bindings()
            ctypes = bindings.ctypes
            message = bindings.msg_type()
            # Force creation of this thread's message queue before start() can
            # attempt PostThreadMessageW during a quick shutdown.
            bindings.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            self._thread_id = int(bindings.kernel32.GetCurrentThreadId())
            self._callback = bindings.hook_proc_type(
                self._make_hook_proc(bindings))
            module = bindings.kernel32.GetModuleHandleW(None)
            hook = bindings.user32.SetWindowsHookExW(
                self.WH_KEYBOARD_LL, self._callback, module, 0)
            if not hook:
                error = ctypes.get_last_error()
                raise OSError(error,
                              "Central WH_KEYBOARD_LL hook installation failed")
            self._hook = int(hook)
            self._started.set()

            while not self._stopping:
                result = int(bindings.user32.GetMessageW(
                    ctypes.byref(message), None, 0, 0))
                if result <= 0:
                    break
                bindings.user32.TranslateMessage(ctypes.byref(message))
                bindings.user32.DispatchMessageW(ctypes.byref(message))
        except BaseException as error:
            self._startup_error = error
            self._started.set()
        finally:
            if bindings is not None and self._hook is not None:
                try:
                    bindings.user32.UnhookWindowsHookEx(self._hook)
                except BaseException:
                    pass
            self._hook = None
            self._callback = None
            SingleHookOwnerPolicy.release(self)

    def _make_hook_proc(self, bindings: _WindowsBindings) -> Callable[..., int]:
        ctypes = bindings.ctypes

        def call_next(code: int, w_param: int, l_param: int) -> int:
            try:
                return int(bindings.user32.CallNextHookEx(
                    self._hook, code, w_param, l_param))
            except BaseException:
                return 0

        def hook_proc(code: int, w_param: int, l_param: int) -> int:
            # A ctypes Win32 callback must NEVER raise.  In a windowed frozen
            # build an escaping callback exception can hard-crash the process
            # with 0xc000041d in _ctypes.pyd.  Fall through to the next hook on
            # every failure, including failures outside Exception's hierarchy.
            try:
                if code < 0:
                    return call_next(code, w_param, l_param)
                message = int(w_param)
                is_down = message in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN)
                is_up = message in (self.WM_KEYUP, self.WM_SYSKEYUP)
                if not is_down and not is_up:
                    return call_next(code, w_param, l_param)

                data = ctypes.cast(
                    l_param,
                    ctypes.POINTER(bindings.keyboard_data_type)).contents
                injected = bool(data.flags & (
                    self.LLKHF_INJECTED | self.LLKHF_LOWER_IL_INJECTED))
                self.observed_event_count += 1
                self.observed_injected |= injected
                if self._observe_only:
                    return call_next(code, w_param, l_param)

                event = RawKeyboardEvent(
                    int(data.vkCode), int(data.scanCode), is_down, injected,
                    int(data.dwExtraInfo), vk_to_canonical(int(data.vkCode)))
                verdict = self.router.route_key_event(event)
                if verdict.kind is KeyboardRoutingVerdictKind.PASS_THROUGH:
                    if (is_up and data.vkCode in (0x5B, 0x5C)
                            and self.router.should_mask_win_release):
                        self._inject_masking_key(bindings)
                    return call_next(code, w_param, l_param)
                if (verdict.kind is
                        KeyboardRoutingVerdictKind.SWALLOW_WITH_ACTION
                        and verdict.action is not None):
                    self._action_queue.put(verdict.action)
                return 1
            except BaseException:
                return call_next(code, w_param, l_param)

        return hook_proc

    @staticmethod
    def _inject_masking_key(bindings: _WindowsBindings) -> None:
        try:
            ctypes = bindings.ctypes
            input_type = bindings.input_type
            key_type = bindings.keybdinput_type
            inputs = (input_type * 2)()
            inputs[0].type = 1
            inputs[0].ki = key_type(
                0xE8, 0, 0, 0,
                KeyboardRouter.ESOTERICOS_EXTRA_INFO_SIGNATURE)
            inputs[1].type = 1
            inputs[1].ki = key_type(
                0xE8, 0, 2, 0,
                KeyboardRouter.ESOTERICOS_EXTRA_INFO_SIGNATURE)
            bindings.user32.SendInput(2, inputs, ctypes.sizeof(input_type))
        except BaseException:
            pass


def _probe() -> int:
    service = KeyboardInterceptionService()
    service.start(observe_only=True)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        time.sleep(min(0.05, deadline - time.monotonic()))
    service.stop()
    print(f"Observed key events: {service.observed_event_count}")
    print(f"Any marked injected: {'yes' if service.observed_injected else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_probe())
