"""Native scoped scripting for EsotericOS: parser, runner, consumer, lifetime.

Importing this module and constructing every class in it installs no hook,
starts no thread, registers no consumer, reads no file and creates no config.
That is the same contract ``hotkey_host`` states, and for the same reason: the
one lawful ``WH_KEYBOARD_LL`` lives in ``keyboard_interception``, and joining it
is an explicit :meth:`ScriptEngine.start` operation.

The resolution model is not here -- it is in ``script_scope``, which is pure.
This module only does the three impure things a script system needs: read text
off disk, decide a verdict inside a hook, and perform a verb afterwards.

A bad script never takes the engine down.  A line that will not parse is
reported with its file, line and column and that one binding is dropped; a file
that cannot be read or decoded is disabled with a reason and every other file
still loads.

There is no mouse in v1.  There is no shared mouse router in this product --
``screen_zoom`` and ``openspan_portal`` each own a ``WH_MOUSE_LL`` hook
independently -- so a script cannot bind a mouse event, and this module
installs no mouse hook to give it one.
"""

from __future__ import annotations

import dataclasses
import os
import re
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from keyboard_interception import (
    ChordModifiers,
    KeyChord,
    KeySequence,
    KeyboardInterceptionService,
    KeyboardRouter,
    KeyboardRoutingVerdict,
    RawKeyboardEvent,
    is_protected,
    vk_to_canonical,
)
from script_scope import (
    Action,
    Binding,
    BindingSet,
    PRIMARY_SCREEN_KEY,
    Resolution,
    STABLE_KEY_PATTERN,
    Scope,
    ScreenFacts,
    Verb,
    WindowFacts,
    resolve,
)
from settings_service import FeatureDeclaration, FeatureRegistry, SettingsService
from window_rules import parse_zone
from window_tiling import TileDirection


FEATURE_ID = "native-scripts"
CONSUMER_ID = "EsotericOS.ScriptEngine"
EXTENSION = ".eos"
DIRECTORY_SETTING = "directory"
PRIORITY_SETTING = "priority"

# Below hotkey_host's 50, because the router sorts consumers ascending and
# consults them in that order: a script Doug wrote by hand should be able to
# take a chord back from a shipped default.  A script that does not match, or
# that says `pass`, returns pass-through, so the built-in still gets it.
DEFAULT_PRIORITY = 40

FEATURE_DECLARATION = FeatureDeclaration(
    FEATURE_ID,
    "Native scoped scripts",
    "Features",
    False,
    {},
    {DIRECTORY_SETTING: "", PRIORITY_SETTING: DEFAULT_PRIORITY},
    ("WH_KEYBOARD_LL through the central KeyboardInterceptionService",),
)


# ---- problems ----------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ScriptProblem:
    """One reported reason, always locatable.  Never raised at a caller."""

    source: str
    line: int
    column: int
    message: str
    fatal: bool = False

    def describe(self) -> str:
        where = self.source or "(inline)"
        if self.line:
            return f"{where}:{self.line}:{self.column}: {self.message}"
        return f"{where}: {self.message}"

    def __str__(self) -> str:
        return self.describe()


class _ParseError(Exception):
    """Internal only.  Every one of these becomes a ScriptProblem."""

    def __init__(self, column: int, message: str) -> None:
        super().__init__(message)
        self.column = column
        self.message = message


# ---- key injection planning (pure) -------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class KeyStroke:
    vk: int
    is_down: bool
    extended: bool = False


def _build_vk_table() -> dict[str, int]:
    """Invert the router's own vk_to_canonical, then fill in the OEM keys.

    Deriving it keeps `send` spelling identical to what the hook reports, so a
    chord that triggers a binding is spelled the same way when it is sent.
    """
    table: dict[str, int] = {}
    for vk in range(0x08, 0x100):
        name = vk_to_canonical(vk)
        if name.startswith("VK_") or name in ("Shift", "Ctrl", "Alt", "Win"):
            continue
        table.setdefault(name, vk)
    # vk_to_canonical reports OEM keys as VK_nnn; canonical_key spells them.
    table.update({
        "Grave": 0xC0, "Minus": 0xBD, "Equals": 0xBB,
        "LeftBracket": 0xDB, "RightBracket": 0xDD, "Backslash": 0xDC,
        "Semicolon": 0xBA, "Quote": 0xDE, "Comma": 0xBC,
        "Period": 0xBE, "Slash": 0xBF,
    })
    return table


VK_BY_KEY = _build_vk_table()

# Keys whose scan code needs KEYEVENTF_EXTENDEDKEY to mean what it says.
EXTENDED_VKS = frozenset({
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,
    0x2C, 0x2D, 0x2E, 0x5B, 0x5C, 0x90, 0xA3, 0xA5,
})

# Ordered so a plan is deterministic; ups walk it in reverse.
MODIFIER_VKS: tuple[tuple[ChordModifiers, int], ...] = (
    (ChordModifiers.WIN, 0x5B),
    (ChordModifiers.CTRL, 0xA2),
    (ChordModifiers.ALT, 0xA4),
    (ChordModifiers.SHIFT, 0xA0),
)


def _stroke(vk: int, is_down: bool) -> KeyStroke:
    return KeyStroke(vk, is_down, vk in EXTENDED_VKS)


def _modifier_strokes(from_state: ChordModifiers, to_state: ChordModifiers,
                      out: list[KeyStroke]) -> None:
    for flag, vk in reversed(MODIFIER_VKS):
        if flag & from_state and not flag & to_state:
            out.append(_stroke(vk, False))
    for flag, vk in MODIFIER_VKS:
        if flag & to_state and not flag & from_state:
            out.append(_stroke(vk, True))


def plan_send(sequence: KeySequence,
              held: ChordModifiers = ChordModifiers.NONE) -> tuple[KeyStroke, ...]:
    """Strokes that make *sequence* arrive, given what is physically held.

    A script chord is reached through modifiers, and those modifiers are still
    down when the action runs.  Sending Ctrl+S while Ctrl+Alt is held would
    deliver Ctrl+Alt+S.  So the plan moves the modifier state to what each
    chord wants, taps the key, and puts the physical state back at the end --
    minimally, so a wanted Alt is never released and re-tapped.

    Pure.  The stroke list is the whole decision; injecting it is separate.
    """
    strokes: list[KeyStroke] = []
    current = held
    for chord in sequence.chords:
        _modifier_strokes(current, chord.modifiers, strokes)
        current = chord.modifiers
        vk = VK_BY_KEY[chord.key]
        strokes.append(_stroke(vk, True))
        strokes.append(_stroke(vk, False))
    _modifier_strokes(current, held, strokes)
    return tuple(strokes)


def send_strokes(strokes: Sequence[KeyStroke]) -> bool:
    """Inject *strokes* with SendInput, tagged as ours.

    dwExtraInfo carries KeyboardRouter.ESOTERICOS_EXTRA_INFO_SIGNATURE, which
    the router already answers with pass-through before any consumer is asked.
    That is what stops a `send` from re-triggering the binding that sent it.
    """
    if not strokes:
        return True
    import ctypes
    from ctypes import wintypes

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

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.restype = wintypes.UINT
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT),
                                 ctypes.c_int]
    key_up = 0x0002
    extended = 0x0001
    buffer = (INPUT * len(strokes))()
    for index, stroke in enumerate(strokes):
        flags = (0 if stroke.is_down else key_up)
        if stroke.extended:
            flags |= extended
        buffer[index].type = 1
        buffer[index].ki = KEYBDINPUT(
            stroke.vk, 0, flags, 0,
            KeyboardRouter.ESOTERICOS_EXTRA_INFO_SIGNATURE)
    sent = int(user32.SendInput(len(strokes), buffer, ctypes.sizeof(INPUT)))
    return sent == len(strokes)


# ---- the format --------------------------------------------------------------

WINDOW_VERBS: dict[str, int] = {
    "tile": 1, "refine": 1, "restore": 0, "center": 0,
    "apply-rules": 0, "save-preset": 1, "restore-preset": 1,
}
REFINE_DIRECTIONS = {
    "left": TileDirection.LEFT, "right": TileDirection.RIGHT,
    "up": TileDirection.UP, "down": TileDirection.DOWN,
}
_SCOPE_KEYWORDS = ("os", "screen", "window")
_ARROW = re.compile(r"(?<=\s)->(?=\s|$)")
_SEMICOLON = re.compile(r"(?<=\s);(?=\s|$)")
_RECORD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\\/-]*$")


def _strip_comment(line: str) -> str:
    """Drop a # comment, unless the # is inside a double-quoted value."""
    in_string = False
    escaped = False
    for index, char in enumerate(line):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "#":
            return line[:index]
    return line


def _tokenize(text: str, base: int) -> list[tuple[str, int]]:
    """Whitespace-separated tokens with 1-based columns; quotes group."""
    tokens: list[tuple[str, int]] = []
    current: list[str] = []
    start = 0
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char.isspace():
            if current:
                tokens.append(("".join(current), base + start))
                current = []
            index += 1
            continue
        if not current:
            start = index
        if char == '"':
            index += 1
            closed = False
            while index < length:
                inner = text[index]
                if inner == "\\" and index + 1 < length:
                    current.append(text[index + 1])
                    index += 2
                    continue
                if inner == '"':
                    closed = True
                    index += 1
                    break
                current.append(inner)
                index += 1
            if not closed:
                raise _ParseError(base + start, "unterminated quoted value")
            continue
        current.append(char)
        index += 1
    if current:
        tokens.append(("".join(current), base + start))
    return tokens


def _split_spaced(pattern: re.Pattern[str], text: str,
                  base: int) -> list[tuple[str, int]]:
    """Split on a separator that must have whitespace on each side."""
    parts: list[tuple[str, int]] = []
    cursor = 0
    for found in pattern.finditer(text):
        parts.append((text[cursor:found.start()], base + cursor))
        cursor = found.end()
    parts.append((text[cursor:], base + cursor))
    return parts


def _parse_criterion(token: str, column: int) -> tuple[str, str]:
    position = min((token.find(mark) for mark in "=~" if mark in token),
                   default=-1)
    if position <= 0:
        raise _ParseError(
            column,
            f"'{token}' is not a window criterion; write process=NAME, "
            "class=NAME or title~TEXT")
    name = token[:position].strip().lower()
    operator = token[position]
    value = token[position + 1:]
    if name in ("process", "exe"):
        wanted = "="
        field = "process"
    elif name in ("class", "classname", "class_name"):
        wanted = "="
        field = "class_name"
    elif name == "title":
        wanted = "~"
        field = "title_contains"
    else:
        raise _ParseError(
            column, f"unknown window criterion '{name}'; use process, class "
                    "or title")
    if operator != wanted:
        raise _ParseError(
            column + position,
            f"{name} is matched with '{wanted}', not '{operator}'"
            + (" (title is a substring match)" if field == "title_contains"
               else ""))
    if not value.strip():
        raise _ParseError(column + position + 1,
                          f"{name} was given no value")
    return field, value


def _parse_scope(tokens: Sequence[tuple[str, int]]) -> Scope:
    if not tokens:
        raise _ParseError(1, "scope needs os, screen <key>, or window <criteria>")
    fields: dict[str, str] = {}
    saw: set[str] = set()
    index = 0
    while index < len(tokens):
        token, column = tokens[index]
        word = token.lower()
        if word not in _SCOPE_KEYWORDS:
            raise _ParseError(
                column,
                f"expected os, screen or window, not '{token}'")
        if word in saw:
            raise _ParseError(column, f"'{word}' is named twice in one scope")
        saw.add(word)
        index += 1
        if word == "os":
            if len(tokens) > 1:
                raise _ParseError(column,
                                  "os is the widest scope and stands alone")
            continue
        if word == "screen":
            if index >= len(tokens):
                raise _ParseError(
                    column, "screen needs a monitor key or the word primary")
            key, key_column = tokens[index]
            index += 1
            lowered = key.strip().lower()
            if (lowered != PRIMARY_SCREEN_KEY
                    and not STABLE_KEY_PATTERN.match(lowered)):
                raise _ParseError(
                    key_column,
                    f"'{key}' is not a monitor key; use primary or a "
                    "16-character MonitorIdentity.stable_key")
            fields["screen_key"] = lowered
            continue
        criteria = 0
        while index < len(tokens):
            token, column = tokens[index]
            if token.lower() in _SCOPE_KEYWORDS and not any(
                    mark in token for mark in "=~"):
                break
            field, value = _parse_criterion(token, column)
            if field in fields:
                raise _ParseError(column, f"{field} is named twice in one scope")
            fields[field] = value
            criteria += 1
            index += 1
        if not criteria:
            raise _ParseError(
                column, "window needs at least one of process=, class=, title~")
    return Scope(**fields)


def _parse_action(text: str, base: int) -> Action:
    tokens = _tokenize(text, base)
    if not tokens:
        raise _ParseError(base, "an action is missing after ->")
    verb_text, verb_column = tokens[0]
    verb = verb_text.lower()

    if verb == "pass":
        if len(tokens) > 1:
            raise _ParseError(tokens[1][1], "pass takes no arguments")
        return Action(Verb.PASS)

    if verb == "send":
        remainder = text[verb_column - base + len(verb_text):]
        offset = base + verb_column - base + len(verb_text)
        keys = remainder.strip()
        if not keys:
            raise _ParseError(verb_column + len(verb_text),
                              "send needs keys, for example: send Ctrl+S")
        try:
            sequence = KeySequence.parse(keys)
        except ValueError as exc:
            raise _ParseError(offset + (len(remainder) - len(remainder.lstrip())),
                              str(exc)) from None
        for chord in sequence.chords:
            if chord.key not in VK_BY_KEY:
                raise _ParseError(
                    offset + 1,
                    f"'{chord.key}' cannot be injected; it has no virtual-key "
                    "code")
        return Action(Verb.SEND, argument=keys, payload=sequence)

    if verb == "window":
        if len(tokens) < 2:
            raise _ParseError(
                verb_column + len(verb_text),
                "window needs a verb: " + ", ".join(sorted(WINDOW_VERBS)))
        name_text, name_column = tokens[1]
        name = name_text.lower()
        if name not in WINDOW_VERBS:
            raise _ParseError(
                name_column,
                f"unknown window verb '{name_text}'; use "
                + ", ".join(sorted(WINDOW_VERBS)))
        wants = WINDOW_VERBS[name]
        given = tokens[2:]
        if len(given) < wants:
            raise _ParseError(name_column + len(name_text),
                              f"window {name} needs an argument")
        if len(given) > wants:
            raise _ParseError(given[wants][1],
                              f"window {name} takes {wants} argument(s)")
        if not wants:
            return Action(Verb.WINDOW, name)
        argument, argument_column = given[0]
        payload: Any = None
        if name == "tile":
            payload = parse_zone(argument)
            if payload is None:
                raise _ParseError(argument_column,
                                  f"'{argument}' is not a tiling zone")
        elif name == "refine":
            payload = REFINE_DIRECTIONS.get(argument.lower())
            if payload is None:
                raise _ParseError(
                    argument_column,
                    f"'{argument}' is not a direction; use left, right, up "
                    "or down")
        return Action(Verb.WINDOW, name, argument, payload)

    if verb == "catalog":
        if len(tokens) != 2:
            raise _ParseError(verb_column + len(verb_text),
                              "catalog needs exactly one control-catalog id")
        record_id, record_column = tokens[1]
        if not _RECORD_ID.match(record_id):
            raise _ParseError(record_column,
                              f"'{record_id}' is not a catalog id")
        return Action(Verb.CATALOG, argument=record_id)

    raise _ParseError(
        verb_column,
        f"unknown verb '{verb_text}'; use send, window, catalog or pass")


@dataclasses.dataclass(frozen=True, slots=True)
class ParsedScript:
    bindings: tuple[Binding, ...] = ()
    problems: tuple[ScriptProblem, ...] = ()


def parse_script(text: str, source: str = "(inline)", *,
                 start_order: int = 0) -> ParsedScript:
    """Parse one script.  Never raises; everything wrong becomes a problem.

    Recovery is per line, exactly as ``window_rules.parse_rules`` recovers per
    rule: one mistyped binding is dropped and named, and the other thirty in
    the file still run.
    """
    bindings: list[Binding] = []
    problems: list[ScriptProblem] = []
    scope = Scope()
    priority = 0
    order = start_order

    for number, raw in enumerate(text.splitlines(), 1):
        code = _strip_comment(raw).rstrip()
        if not code.strip():
            continue
        indent = len(code) - len(code.lstrip())
        body = code.strip()
        base = indent + 1
        try:
            arrow = _ARROW.search(code)
            if arrow is None and "->" in code:
                raise _ParseError(code.index("->") + 1,
                                  "the -> arrow needs a space on each side")
            if arrow is None:
                tokens = _tokenize(body, base)
                if not tokens:
                    raise _ParseError(base, "this line says nothing")
                head = tokens[0][0].lower()
                if head == "scope":
                    scope = _parse_scope(tokens[1:])
                    priority = 0
                elif head == "priority":
                    if len(tokens) != 2:
                        raise _ParseError(tokens[0][1],
                                          "priority needs one whole number")
                    try:
                        priority = int(tokens[1][0], 10)
                    except ValueError:
                        raise _ParseError(
                            tokens[1][1],
                            f"'{tokens[1][0]}' is not a whole number") from None
                else:
                    raise _ParseError(
                        tokens[0][1],
                        f"expected scope, priority, or 'chord -> action', "
                        f"not '{tokens[0][0]}'")
                continue

            chord_text = code[:arrow.start()].strip()
            if not chord_text:
                raise _ParseError(base, "a binding needs a chord before ->")
            try:
                sequence = KeySequence.parse(chord_text)
            except ValueError as exc:
                raise _ParseError(base, str(exc)) from None
            if not sequence.is_single:
                raise _ParseError(
                    base,
                    "a trigger must be one chord; there is no chord-sequence "
                    "state machine in this engine")
            chord = sequence.first
            if is_protected(chord):
                raise _ParseError(
                    base,
                    f"{chord} is a protected chord and always reaches Windows")

            fragments = _split_spaced(_SEMICOLON, code[arrow.end():],
                                      arrow.end() + 1)
            actions = tuple(_parse_action(fragment, offset)
                            for fragment, offset in fragments)
            if any(action.verb is Verb.PASS for action in actions) and (
                    len(actions) != 1):
                raise _ParseError(
                    fragments[0][1],
                    "pass stands alone; a binding either falls through or acts")
            bindings.append(Binding(chord, scope, actions, priority,
                                    source, number, order))
            order += 1
        except _ParseError as problem:
            problems.append(ScriptProblem(source, number, problem.column,
                                          problem.message))
    return ParsedScript(tuple(bindings), tuple(problems))


# ---- running an action -------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class StepOutcome:
    step: str
    performed: bool
    reason: str
    detail: Any = None


@dataclasses.dataclass(frozen=True, slots=True)
class RunOutcome:
    binding: str
    steps: tuple[StepOutcome, ...] = ()

    @property
    def performed(self) -> bool:
        return bool(self.steps) and all(step.performed for step in self.steps)


class ScriptRunner:
    """Perform verbs.  Every effect is injectable, so tests perform none.

    Nothing here runs inside the hook.  The consumer hands a callable to
    ``KeyboardRoutingVerdict.swallow_with_action`` and the interception
    service runs it on its own action thread.
    """

    def __init__(self, *, actions: Any | None = None,
                 actions_factory: Callable[[], Any] | None = None,
                 sender: Callable[[Sequence[KeyStroke]], bool] | None = None,
                 activator: Callable[[str], Any] | None = None) -> None:
        self._actions = actions
        self._actions_factory = actions_factory
        self._sender = sender if sender is not None else send_strokes
        self._activator = activator
        self._catalog: Any = None
        self.last_outcome: RunOutcome | None = None

    def window_actions(self) -> Any | None:
        if self._actions is None and self._actions_factory is not None:
            self._actions = self._actions_factory()
        return self._actions

    def run(self, binding: Binding,
            held: ChordModifiers = ChordModifiers.NONE) -> RunOutcome:
        steps = tuple(self._run_action(action, held)
                      for action in binding.actions)
        outcome = RunOutcome(binding.describe(), steps)
        self.last_outcome = outcome
        return outcome

    def _run_action(self, action: Action, held: ChordModifiers) -> StepOutcome:
        try:
            if action.verb is Verb.PASS:
                return StepOutcome("pass", True, "fell through")
            if action.verb is Verb.SEND:
                return self._send(action, held)
            if action.verb is Verb.WINDOW:
                return self._window(action)
            if action.verb is Verb.CATALOG:
                return self._catalog_action(action)
        except Exception as exc:  # noqa: BLE001 - a verb must never escape
            return StepOutcome(action.describe(), False, f"failed: {exc}")
        return StepOutcome(action.describe(), False, "unknown verb")

    def _send(self, action: Action, held: ChordModifiers) -> StepOutcome:
        strokes = plan_send(action.payload, held)
        sent = bool(self._sender(strokes))
        return StepOutcome(action.describe(), sent,
                           "sent" if sent else "SendInput refused",
                           {"strokes": len(strokes)})

    def _window(self, action: Action) -> StepOutcome:
        actions = self.window_actions()
        if actions is None:
            return StepOutcome(action.describe(), False,
                               "window actions are not available")
        name = action.name
        if name == "tile":
            result = actions.tile_focused(action.payload)
        elif name == "refine":
            result = actions.refine_focused(action.payload)
        elif name == "restore":
            result = actions.restore_focused()
        elif name == "center":
            result = actions.center_focused()
        elif name == "apply-rules":
            result = actions.apply_rules_now()
        elif name == "save-preset":
            result = actions.save_preset(action.argument)
        elif name == "restore-preset":
            result = actions.restore_preset(action.argument)
        else:
            return StepOutcome(action.describe(), False,
                               f"unknown window verb '{name}'")
        return StepOutcome(action.describe(), bool(result.performed),
                           result.reason, result)

    def _catalog_action(self, action: Action) -> StepOutcome:
        if self._activator is not None:
            result = self._activator(action.argument)
            performed = bool(getattr(result, "ok", result))
            reason = str(getattr(result, "reason", "activated"))
            return StepOutcome(action.describe(), performed, reason, result)
        try:
            import control_catalog
            from control_center_client import request_activation
        except ImportError as exc:
            return StepOutcome(action.describe(), False,
                               f"control catalog unavailable: {exc}")
        if self._catalog is None:
            self._catalog = control_catalog.build_live_catalog()
        result = request_activation(self._catalog, action.argument)
        return StepOutcome(action.describe(), bool(result.ok),
                           str(result.reason), result)


# ---- observing the desk ------------------------------------------------------


def default_probe() -> tuple[WindowFacts, ScreenFacts]:
    """Read the foreground window and the screen it is on.  Read-only.

    GetWindowTextW does not send WM_GETTEXT across a process boundary, so this
    cannot be made to block on a hung application, which is what makes it safe
    to call from the hook thread.
    """
    import ctypes
    from ctypes import wintypes

    # Every restype and argtype is declared: an HWND is pointer-sized, and
    # ctypes would otherwise pass and return it as a 32-bit int.
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetForegroundWindow.argtypes = []
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                     ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                      ctypes.c_int]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                ctypes.POINTER(wintypes.DWORD)]
    handle = user32.GetForegroundWindow()
    if not handle:
        return WindowFacts(), ScreenFacts()

    class_buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(handle, class_buffer, 256)
    title_buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(handle, title_buffer, 512)

    process_id = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
    facts = WindowFacts(_process_name(process_id.value), title_buffer.value,
                        class_buffer.value)
    return facts, _screen_for_window(handle)


def _process_name(process_id: int) -> str:
    if not process_id:
        return ""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                     wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    # PROCESS_QUERY_LIMITED_INFORMATION: the least that answers the question,
    # and the only right that works against an elevated or protected process.
    handle = kernel32.OpenProcess(0x1000, False, process_id)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(512)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer,
                                                   ctypes.byref(size)):
            return ""
        return os.path.splitext(os.path.basename(buffer.value))[0]
    finally:
        kernel32.CloseHandle(handle)


# Resolving a stable key means reading EDID out of the registry, which is far
# too slow to do on the hook thread for every scoped chord.  The map is cached
# by monitor handle and is self-invalidating: changing the display arrangement
# produces new HMONITORs, so the next lookup misses and rebuilds.  The app may
# also call reset_screen_cache() from its display watch -- for instance when
# only which display is *primary* changed, which keeps the handles.
_SCREEN_CACHE: dict[int, ScreenFacts] = {}
_SCREEN_GATE = threading.Lock()


def reset_screen_cache() -> None:
    with _SCREEN_GATE:
        _SCREEN_CACHE.clear()


def _rebuild_screen_cache() -> dict[int, ScreenFacts]:
    from monitor_identity import attached_identities
    from window_tiling import enumerate_work_areas

    identities = {item.device_name.lower(): item
                  for item in attached_identities()}
    built: dict[int, ScreenFacts] = {}
    for area in enumerate_work_areas():
        identity = identities.get(getattr(area, "device_name", "").lower())
        built[int(getattr(area, "handle", 0))] = ScreenFacts(
            identity.stable_key if identity is not None else None,
            bool(area.is_primary))
    return built


def _screen_for_window(handle: Any) -> ScreenFacts:
    """Resolve the window's monitor to a stable key, as WindowActions does."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    monitor = int(user32.MonitorFromWindow(handle, 2) or 0)
    if not monitor:
        return ScreenFacts()
    with _SCREEN_GATE:
        known = _SCREEN_CACHE.get(monitor)
        if known is not None:
            return known
    try:
        built = _rebuild_screen_cache()
    except (ImportError, OSError, RuntimeError):
        return ScreenFacts()
    with _SCREEN_GATE:
        if len(_SCREEN_CACHE) > 32:
            _SCREEN_CACHE.clear()
        _SCREEN_CACHE.update(built)
    return built.get(monitor, ScreenFacts())


# ---- the consumer ------------------------------------------------------------


class ScriptConsumer:
    """A KeyboardConsumer that answers with the pure resolver and nothing else.

    It probes the desk only when the chord is claimed by some binding, so an
    unclaimed keystroke costs one dictionary lookup and is passed on untouched.
    """

    def __init__(self, runner: ScriptRunner,
                 bindings: BindingSet | None = None, *,
                 probe: Callable[[], tuple[WindowFacts, ScreenFacts]] | None = None,
                 consumer_id: str = CONSUMER_ID,
                 priority: int = DEFAULT_PRIORITY) -> None:
        self.consumer_id = consumer_id
        self.priority = priority
        self.runner = runner
        self._probe = probe if probe is not None else default_probe
        self._bindings = bindings if bindings is not None else BindingSet()
        self._gate = threading.RLock()
        self.last_resolution: Resolution | None = None
        self.probe_failures = 0

    @property
    def bindings(self) -> BindingSet:
        with self._gate:
            return self._bindings

    def set_bindings(self, bindings: BindingSet) -> None:
        with self._gate:
            self._bindings = bindings

    def facts(self) -> tuple[WindowFacts, ScreenFacts]:
        try:
            return self._probe()
        except Exception:  # noqa: BLE001 - a hook must never raise
            self.probe_failures += 1
            return WindowFacts(), ScreenFacts()

    def process_key_event(
            self, event: RawKeyboardEvent,
            current_modifiers: ChordModifiers) -> KeyboardRoutingVerdict:
        if not event.is_down:
            return KeyboardRoutingVerdict.pass_through()
        chord = KeyChord(current_modifiers, event.canonical_key)
        if is_protected(chord):
            return KeyboardRoutingVerdict.pass_through()
        candidates = self.bindings.for_chord(chord)
        if not candidates:
            return KeyboardRoutingVerdict.pass_through()
        facts, screen = self.facts()
        resolution = resolve(candidates, chord, facts, screen)
        self.last_resolution = resolution
        if not resolution.handled or resolution.binding is None:
            return KeyboardRoutingVerdict.pass_through()
        binding = resolution.binding

        def invoke() -> None:
            self.runner.run(binding, current_modifiers)

        return KeyboardRoutingVerdict.swallow_with_action(invoke,
                                                          self.consumer_id)


# ---- files and lifetime ------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ScriptFile:
    name: str
    path: str
    enabled: bool
    bindings: int
    problems: tuple[ScriptProblem, ...] = ()
    disabled_reason: str = ""

    def describe(self) -> str:
        if not self.enabled:
            return f"{self.name}: disabled -- {self.disabled_reason}"
        note = f", {len(self.problems)} problem(s)" if self.problems else ""
        return f"{self.name}: {self.bindings} binding(s){note}"


@dataclasses.dataclass(frozen=True, slots=True)
class ReloadResult:
    directory: str
    files: tuple[ScriptFile, ...] = ()
    bindings: int = 0
    problems: tuple[ScriptProblem, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclasses.dataclass(frozen=True, slots=True)
class EngineResult:
    operation: str
    performed: bool
    reason: str


def load_directory(directory: os.PathLike[str] | str) -> tuple[BindingSet,
                                                               tuple[ScriptFile, ...]]:
    """Parse every ``*.eos`` in *directory*, in name order.

    A file that cannot be read or decoded is disabled with a reason; the other
    files still load.  A missing directory is not an error -- there are simply
    no scripts.
    """
    root = Path(directory)
    files: list[ScriptFile] = []
    bindings: list[Binding] = []
    problems: list[ScriptProblem] = []
    try:
        found = sorted(root.glob("*" + EXTENSION),
                       key=lambda item: item.name.lower())
    except OSError as exc:
        return BindingSet((), (ScriptProblem(str(root), 0, 0,
                                             f"cannot list scripts: {exc}",
                                             True),)), ()
    for path in found:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            reason = f"cannot read: {exc}"
            problem = ScriptProblem(path.name, 0, 0, reason, True)
            problems.append(problem)
            files.append(ScriptFile(path.name, str(path), False, 0,
                                    (problem,), reason))
            continue
        parsed = parse_script(text, path.name, start_order=len(bindings))
        bindings.extend(parsed.bindings)
        problems.extend(parsed.problems)
        files.append(ScriptFile(path.name, str(path), True,
                                len(parsed.bindings), parsed.problems))
    return BindingSet(bindings, problems), tuple(files)


class ScriptEngine:
    """Own the script set and one central-service consumer.

    Construction reads nothing and starts nothing.  ``reload`` reads; ``start``
    registers the consumer, and installs the hook only when nobody else has.
    When the app already runs the central ``KeyboardInterceptionService`` (for
    ``HotkeyHost``), the engine joins it and leaves its lifetime alone.
    """

    def __init__(self, *, store: Any | None = None,
                 settings_service: SettingsService | None = None,
                 service: Any | None = None,
                 router: KeyboardRouter | None = None,
                 directory: os.PathLike[str] | str | None = None,
                 actions: Any | None = None,
                 actions_factory: Callable[[], Any] | None = None,
                 runner: ScriptRunner | None = None,
                 probe: Callable[[], tuple[WindowFacts, ScreenFacts]] | None = None,
                 ) -> None:
        self._store = store
        self._settings_service = settings_service
        if service is None:
            self.router = router if router is not None else KeyboardRouter()
            self.service: Any = KeyboardInterceptionService(self.router)
        else:
            self.service = service
            self.router = (router if router is not None
                           else getattr(service, "router", None)
                           or KeyboardRouter())
        self._directory = directory
        self.runner = runner if runner is not None else ScriptRunner(
            actions=actions,
            actions_factory=(actions_factory if actions_factory is not None
                             else self._default_actions))
        self.consumer = ScriptConsumer(self.runner, probe=probe)
        self.files: tuple[ScriptFile, ...] = ()
        self._running = False
        self._owns_hook = False

    # -- collaborators, all built only when first needed ----------------------

    def _default_actions(self) -> Any:
        import hotkey_host

        return hotkey_host.WindowActions(store=self.store)

    @property
    def store(self) -> Any:
        if self._store is None:
            from config_store import ConfigStore

            self._store = ConfigStore()
        return self._store

    @property
    def settings_service(self) -> SettingsService:
        if self._settings_service is None:
            self._settings_service = SettingsService(
                self.store, FeatureRegistry([FEATURE_DECLARATION]))
        else:
            self.ensure_declaration(self._settings_service.registry)
        return self._settings_service

    @staticmethod
    def ensure_declaration(registry: FeatureRegistry) -> None:
        try:
            registry.get(FEATURE_ID)
        except KeyError:
            registry.register(FEATURE_DECLARATION)

    @property
    def directory(self) -> Path:
        if self._directory is not None:
            return Path(self._directory)
        try:
            configured = str(self.settings_service.get_setting(
                FEATURE_ID, DIRECTORY_SETTING) or "").strip()
        except (KeyError, OSError, TypeError, ValueError):
            configured = ""
        if configured:
            return Path(configured)
        return Path(self.store.data_directory) / "scripts"

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def bindings(self) -> BindingSet:
        return self.consumer.bindings

    # -- the API a Scripts surface calls --------------------------------------

    def scripts(self) -> tuple[ScriptFile, ...]:
        return self.files

    def problems(self) -> tuple[ScriptProblem, ...]:
        return self.bindings.problems

    def describe_bindings(self) -> tuple[str, ...]:
        return tuple(binding.describe() for binding in self.bindings.bindings)

    def explain(self, chord: KeyChord | str, facts: WindowFacts,
                screen: ScreenFacts) -> Resolution:
        """Answer 'what would this chord do, here?' without pressing it."""
        if isinstance(chord, str):
            chord = KeySequence.parse(chord).first
        return self.bindings.resolve(chord, facts, screen)

    def reload(self) -> ReloadResult:
        directory = self.directory
        bindings, files = load_directory(directory)
        self.files = files
        self.consumer.set_bindings(bindings)
        return ReloadResult(str(directory), files, len(bindings),
                            bindings.problems)

    def start(self) -> EngineResult:
        if self._running:
            return EngineResult("start", False, "already running")
        self.reload()
        try:
            self.consumer.priority = int(self.settings_service.get_setting(
                FEATURE_ID, PRIORITY_SETTING))
        except (KeyError, OSError, TypeError, ValueError):
            self.consumer.priority = DEFAULT_PRIORITY
        self.service.register_consumer(self.consumer)
        if bool(getattr(self.service, "is_hook_installed", False)):
            self._running = True
            self._owns_hook = False
            return EngineResult("start", True,
                                "joined the running keyboard hook")
        try:
            self.service.start()
        except (OSError, PermissionError, RuntimeError, TimeoutError) as exc:
            self.service.unregister_consumer(self.consumer.consumer_id)
            return EngineResult("start", False, str(exc))
        self._running = True
        self._owns_hook = True
        return EngineResult("start", True, "started")

    def stop(self) -> EngineResult:
        if not self._running:
            return EngineResult("stop", False, "not running")
        problem: str | None = None
        try:
            if self._owns_hook:
                self.service.stop()
        except (OSError, RuntimeError, TimeoutError) as exc:
            problem = str(exc)
        finally:
            self.service.unregister_consumer(self.consumer.consumer_id)
            self._running = False
            self._owns_hook = False
        if problem is not None:
            return EngineResult("stop", False, problem)
        return EngineResult("stop", True, "stopped")


def _probe() -> int:
    """Read-only: parse an inline script and explain it against this desk."""
    example = (
        "# EsotericOS script probe -- nothing is bound and nothing is sent.\n"
        "scope os\n"
        "  Ctrl+Alt+H -> window tile left-half\n"
        "  Ctrl+Alt+K -> send Ctrl+S\n"
        "\n"
        "scope screen primary\n"
        "  Ctrl+Alt+H -> window center\n"
        "\n"
        "scope window process=notepad\n"
        "  priority 5\n"
        "  Ctrl+Alt+H -> pass\n"
        "  Ctrl+Alt+J -> send Ctrl+Shift+S\n"
    )
    parsed = parse_script(example, "probe" + EXTENSION)
    print(f"PARSED {len(parsed.bindings)} binding(s), "
          f"{len(parsed.problems)} problem(s)")
    for problem in parsed.problems:
        print("  " + problem.describe())
    bindings = BindingSet(parsed.bindings, parsed.problems)
    try:
        facts, screen = default_probe()
    except Exception as exc:  # noqa: BLE001
        print(f"PROBE facts unavailable: {exc}")
        facts, screen = WindowFacts(), ScreenFacts()
    print(f"FOREGROUND process={facts.process or '(unknown)'} "
          f"class={facts.class_name or '(unknown)'}")
    print(f"SCREEN key={screen.stable_key or '(none)'} "
          f"primary={screen.is_primary}")
    for chord in bindings.chords:
        print("  " + bindings.resolve(chord, facts, screen).describe())
    print("Probe complete: installed 0 hooks, sent 0 keys.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_probe())
