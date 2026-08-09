"""Pure window-placement rules plus an event-driven, injected applicator.

Importing this module and constructing :class:`WindowRuleWatcher` perform no
native calls and install no hooks.  A caller gives ``watcher.on_event`` to the
existing ``window_tracker.WindowTracker`` and explicitly starts that tracker.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import math
import ntpath
import threading
from collections.abc import Callable, Iterable, Sequence
from typing import Any, Protocol

from window_tiling import PixelRect, TileZone, compute
from window_tracker import (TrackedWindow, WindowAppeared, WindowMoved,
                            WindowRenamed, WindowVanished)


FEATURE_ID = "window-rules"
SETTINGS_KEY = "rules"


class RuleAction(enum.Enum):
    NONE = "none"
    MONITOR = "monitor"
    ZONE = "zone"
    RECT = "rect"
    MAXIMIZE = "maximize"
    FLOAT = "float"


@dataclasses.dataclass(frozen=True, slots=True)
class WindowFacts:
    process: str = ""
    title: str = ""
    class_name: str = ""

    @classmethod
    def from_tracked(cls, window: TrackedWindow) -> WindowFacts:
        return cls(window.identity.executable_name, window.identity.title,
                   window.identity.window_class)


@dataclasses.dataclass(frozen=True, slots=True)
class WindowAction:
    """A fully resolved action; zone names stay unresolved for the mover."""

    kind: RuleAction
    monitor_key: str | None = None
    zone: str | None = None
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None
    floating: bool = False
    workspace_id: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class WindowRule:
    process: str | None = None
    title_contains: str | None = None
    class_name: str | None = None
    action: RuleAction = RuleAction.NONE
    monitor_key: str | None = None
    zone: str | None = None
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None
    floating: bool = False
    priority: int = 0
    workspace_id: str | None = None

    @staticmethod
    def _specified(value: str | None) -> bool:
        return value is not None and bool(value.strip())

    @property
    def specificity(self) -> int:
        return sum(self._specified(value) for value in
                   (self.process, self.title_contains, self.class_name))

    @property
    def is_catch_all(self) -> bool:
        return self.specificity == 0

    def matches(self, facts: WindowFacts) -> bool:
        return match(self, facts)

    def resolved_action(self) -> WindowAction:
        return WindowAction(
            self.action, self.monitor_key, self.zone,
            self.x, self.y, self.width, self.height,
            self.floating, self.workspace_id)

    def describe(self) -> str:
        criteria: list[str] = []
        if self._specified(self.process):
            criteria.append(f"process={self.process.strip()}")
        if self._specified(self.title_contains):
            criteria.append("title~=(set)")
        if self._specified(self.class_name):
            criteria.append(f"class={self.class_name.strip()}")
        what = " ".join(criteria) if criteria else "any window"
        return f"[{what}] -> {self.action.value} (priority {self.priority})"


@dataclasses.dataclass(frozen=True, slots=True)
class WindowRuleSet:
    rules: tuple[WindowRule, ...] = ()
    problems: tuple[str, ...] = ()


def _same_case_insensitive(left: str, right: str) -> bool:
    # This deliberately mirrors OrdinalIgnoreCase more closely than casefold.
    return left.lower() == right.lower()


def match(rule: WindowRule, facts: WindowFacts) -> bool:
    """Return whether all specified criteria on *rule* match *facts*."""
    if rule._specified(rule.process):
        wanted = rule.process.strip()
        if wanted.lower().endswith(".exe"):
            wanted = wanted[:-4]
        if not _same_case_insensitive(wanted, facts.process):
            return False
    if (rule._specified(rule.title_contains)
            and rule.title_contains.strip().lower() not in facts.title.lower()):
        return False
    if (rule._specified(rule.class_name)
            and not _same_case_insensitive(rule.class_name.strip(),
                                           facts.class_name)):
        return False
    return True


def matching_rules(rules: Sequence[WindowRule],
                   facts: WindowFacts) -> tuple[WindowRule, ...]:
    """Return all matches strongest first, retaining order for complete ties."""
    indexed = ((index, rule) for index, rule in enumerate(rules)
               if match(rule, facts))
    return tuple(rule for _, rule in sorted(
        indexed, key=lambda item: (-item[1].priority,
                                   -item[1].specificity, item[0])))


def select_rule(rules: Sequence[WindowRule],
                facts: WindowFacts) -> WindowRule | None:
    ordered = matching_rules(rules, facts)
    return ordered[0] if ordered else None


def resolve(rules: Sequence[WindowRule],
            facts: WindowFacts) -> WindowAction | None:
    """Resolve by priority, specificity, then declaration order."""
    winner = select_rule(rules, facts)
    return winner.resolved_action() if winner is not None else None


def parse_zone(text: str | None) -> TileZone | None:
    if text is None or not text.strip():
        return None
    normalized = "".join(character for character in text.lower()
                         if character.isalnum())
    return {
        "left": TileZone.LEFT_HALF,
        "lefthalf": TileZone.LEFT_HALF,
        "right": TileZone.RIGHT_HALF,
        "righthalf": TileZone.RIGHT_HALF,
        "top": TileZone.TOP_HALF,
        "tophalf": TileZone.TOP_HALF,
        "bottom": TileZone.BOTTOM_HALF,
        "bottomhalf": TileZone.BOTTOM_HALF,
        "topleft": TileZone.TOP_LEFT,
        "lefttop": TileZone.TOP_LEFT,
        "topright": TileZone.TOP_RIGHT,
        "righttop": TileZone.TOP_RIGHT,
        "bottomleft": TileZone.BOTTOM_LEFT,
        "leftbottom": TileZone.BOTTOM_LEFT,
        "bottomright": TileZone.BOTTOM_RIGHT,
        "rightbottom": TileZone.BOTTOM_RIGHT,
    }.get(normalized)


def _relaxed_json(text: str) -> str:
    """Remove C# JsonDocument's supported comments and trailing commas."""
    without_comments: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        if in_string:
            without_comments.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            without_comments.append(char)
            index += 1
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index + 2)
            index = len(text) if newline < 0 else newline
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end < 0 else end + 2
            continue
        without_comments.append(char)
        index += 1

    source = "".join(without_comments)
    result: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(source):
        char = source[index]
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
        if char == ",":
            lookahead = index + 1
            while (lookahead < len(source)
                   and source[lookahead].isspace()):
                lookahead += 1
            if lookahead < len(source) and source[lookahead] in "]}":
                index += 1
                continue
        result.append(char)
        index += 1
    return "".join(result)


def _string(item: dict[str, Any], *names: str) -> str | None:
    for name in names:
        value = item.get(name)
        if isinstance(value, str):
            return value
    return None


def _number(item: dict[str, Any], name: str, position: int,
            problems: list[str]) -> tuple[float | None, bool]:
    if name not in item or item[name] is None:
        return None, False
    value = item[name]
    if (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value)):
        return float(value), False
    problems.append(
        f"Rule {position} has a non-numeric '{name}'; skipped.")
    return None, True


def parse_rules(value: str | Sequence[Any] | None) -> WindowRuleSet:
    """Parse independently recoverable rules from JSON text or decoded data."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return WindowRuleSet()
    if isinstance(value, str):
        try:
            decoded = json.loads(_relaxed_json(value))
        except (json.JSONDecodeError, UnicodeError) as exc:
            return WindowRuleSet(
                problems=(f"Rules are not valid JSON ({exc}); "
                          "no rules are active.",))
    else:
        decoded = value
    if (not isinstance(decoded, Sequence)
            or isinstance(decoded, (str, bytes, bytearray))):
        return WindowRuleSet(
            problems=("Rules must be a JSON array; no rules are active.",))

    rules: list[WindowRule] = []
    problems: list[str] = []
    action_names = {action.value.replace("-", ""): action
                    for action in RuleAction}
    for position, item in enumerate(decoded, 1):
        if not isinstance(item, dict):
            problems.append(f"Rule {position} is not an object; skipped.")
            continue
        action_text = _string(item, "action")
        if action_text is None or not action_text.strip():
            action = RuleAction.NONE
        else:
            action = action_names.get(
                action_text.replace("-", "").strip().lower())
            if action is None:
                problems.append(
                    f"Rule {position} has an unknown action "
                    f"'{action_text}'; skipped.")
                continue
        zone = _string(item, "zone")
        if action is RuleAction.ZONE and parse_zone(zone) is None:
            problems.append(
                f"Rule {position} asks for zone '{zone}', which is not a "
                "tiling zone; skipped.")
            continue
        numbers: dict[str, float | None] = {}
        malformed = False
        for name in ("x", "y", "width", "height"):
            numbers[name], bad = _number(item, name, position, problems)
            malformed = malformed or bad
        if malformed:
            continue
        if (action is RuleAction.RECT
                and (numbers["width"] is None or numbers["height"] is None)):
            problems.append(
                f"Rule {position} asks for an explicit rect but gives no "
                "width/height; skipped.")
            continue
        priority = item.get("priority", 0)
        if isinstance(priority, bool) or not isinstance(priority, int):
            problems.append(
                f"Rule {position} has a non-integer priority; skipped.")
            continue
        floating = item.get("floating", False)
        if not isinstance(floating, bool):
            problems.append(
                f"Rule {position} has a non-boolean 'floating'; skipped.")
            continue
        rules.append(WindowRule(
            process=_string(item, "process"),
            title_contains=_string(item, "titleContains", "title"),
            class_name=_string(item, "className", "class"),
            action=action,
            monitor_key=_string(item, "monitor", "monitorKey"),
            zone=zone,
            x=numbers["x"], y=numbers["y"],
            width=numbers["width"], height=numbers["height"],
            floating=floating or action is RuleAction.FLOAT,
            priority=priority,
            workspace_id=_string(item, "workspace", "workspaceId"),
        ))
    return WindowRuleSet(tuple(rules), tuple(problems))


def _number_for_json(value: float | None) -> int | float | None:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def rules_to_data(rules: Iterable[WindowRule]) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = []
    for rule in rules:
        item: dict[str, Any] = {"action": rule.action.value}
        optional = {
            "process": rule.process,
            "titleContains": rule.title_contains,
            "className": rule.class_name,
            "monitor": rule.monitor_key,
            "zone": rule.zone,
            "x": _number_for_json(rule.x),
            "y": _number_for_json(rule.y),
            "width": _number_for_json(rule.width),
            "height": _number_for_json(rule.height),
            "workspace": rule.workspace_id,
        }
        item.update((key, value) for key, value in optional.items()
                    if value is not None)
        if rule.floating:
            item["floating"] = True
        if rule.priority:
            item["priority"] = rule.priority
        data.append(item)
    return data


def load_rules(store: Any) -> WindowRuleSet:
    return parse_rules(store.get_feature_setting(
        FEATURE_ID, SETTINGS_KEY, []))


def save_rules(store: Any, rules: Iterable[WindowRule]) -> None:
    store.set_feature_setting(FEATURE_ID, SETTINGS_KEY,
                              rules_to_data(rules))


_INT_MIN = -(2**31)
_INT_MAX = 2**31 - 1


def _round_away(value: float) -> int:
    value = min(max(value, _INT_MIN), _INT_MAX)
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def try_resolve_rect(rule: WindowRule,
                     work_area: PixelRect) -> PixelRect | None:
    if rule.width is None or rule.height is None:
        return None
    x = rule.x if rule.x is not None else 0.0
    y = rule.y if rule.y is not None else 0.0
    values = (x, y, rule.width, rule.height)
    fractional = all(0 <= value <= 1 for value in values)
    if fractional:
        rect = PixelRect(
            work_area.x + _round_away(x * work_area.width),
            work_area.y + _round_away(y * work_area.height),
            _round_away(rule.width * work_area.width),
            _round_away(rule.height * work_area.height))
    else:
        rect = PixelRect(*(_round_away(value) for value in values))
    return None if rect.is_empty else rect


def move_between_work_areas(window: PixelRect, source: PixelRect,
                            target: PixelRect) -> PixelRect:
    if source.is_empty or target.is_empty:
        return window
    fraction_x = (window.x - source.x) / source.width
    fraction_y = (window.y - source.y) / source.height
    width = min(window.width, target.width)
    height = min(window.height, target.height)
    x = target.x + _round_away(fraction_x * target.width)
    y = target.y + _round_away(fraction_y * target.height)
    x = min(max(x, target.x), target.right - width)
    y = min(max(y, target.y), target.bottom - height)
    return PixelRect(x, y, width, height)


def union_rects(rectangles: Iterable[PixelRect]) -> PixelRect:
    nonempty = [rect for rect in rectangles if not rect.is_empty]
    if not nonempty:
        return PixelRect(0, 0, 0, 0)
    return PixelRect.from_edges(
        min(rect.left for rect in nonempty),
        min(rect.top for rect in nonempty),
        max(rect.right for rect in nonempty),
        max(rect.bottom for rect in nonempty))


def zone_rect(action: WindowAction,
              work_area: PixelRect) -> PixelRect | None:
    zone = parse_zone(action.zone)
    return compute(work_area, zone) if zone is not None else None


class Mover(Protocol):
    def __call__(self, handle: int, action: WindowAction) -> bool: ...


def apply_action(handle: int, action: WindowAction, mover: Mover) -> bool:
    """Apply once through *mover*; NONE/FLOAT deliberately touch nothing."""
    if action.kind in (RuleAction.NONE, RuleAction.FLOAT):
        return True
    return bool(mover(handle, action))


class _TimerScheduler:
    def call_later(self, delay: float, callback: Callable[[], None]) -> Any:
        timer = threading.Timer(max(0.0, delay), callback)
        timer.daemon = True
        timer.start()
        return timer


@dataclasses.dataclass(slots=True)
class _Pending:
    window: TrackedWindow
    attempts: int = 0
    scheduled: Any = None


class WindowRuleWatcher:
    """Debounce tracker arrivals and retry refused placements a bounded amount.

    A mover returns true when placement stuck.  False schedules another single
    attempt, up to ``max_attempts`` total.  A user ``WindowMoved`` event owns
    the window immediately and cancels all pending work, so retries never fight
    a drag.  No loop, polling thread, or native hook is created here.
    """

    def __init__(self, rules: Sequence[WindowRule], mover: Mover,
                 *, scheduler: Any | None = None,
                 settle_seconds: float = 0.25,
                 retry_seconds: float = 0.25,
                 max_attempts: int = 3) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        self.rules = tuple(rules)
        self.mover = mover
        self.scheduler = scheduler if scheduler is not None else _TimerScheduler()
        self.settle_seconds = max(0.0, settle_seconds)
        self.retry_seconds = max(0.0, retry_seconds)
        self.max_attempts = max_attempts
        self._pending: dict[int, _Pending] = {}
        self._handled: set[int] = set()
        self._user_owned: set[int] = set()
        self._lock = threading.RLock()

    def on_event(self, event: object) -> None:
        if isinstance(event, WindowAppeared):
            self._on_appeared(event.window)
        elif isinstance(event, WindowRenamed):
            with self._lock:
                pending = self._pending.get(event.window.handle)
                if pending is not None:
                    pending.window = event.window
        elif isinstance(event, WindowMoved):
            with self._lock:
                self._user_owned.add(event.window.handle)
                self._cancel(event.window.handle)
        elif isinstance(event, WindowVanished):
            with self._lock:
                self._cancel(event.handle)
                self._handled.discard(event.handle)
                self._user_owned.discard(event.handle)

    def _on_appeared(self, window: TrackedWindow) -> None:
        with self._lock:
            handle = window.handle
            if (handle in self._pending or handle in self._handled
                    or handle in self._user_owned):
                return
            pending = _Pending(window)
            self._pending[handle] = pending
            pending.scheduled = self.scheduler.call_later(
                self.settle_seconds, lambda: self._attempt(handle))

    def _attempt(self, handle: int) -> None:
        with self._lock:
            pending = self._pending.get(handle)
            if pending is None or handle in self._user_owned:
                return
            action = resolve(self.rules, WindowFacts.from_tracked(pending.window))
            if action is None:
                self._pending.pop(handle, None)
                return
            pending.attempts += 1
            settled = apply_action(handle, action, self.mover)
            if settled or pending.attempts >= self.max_attempts:
                self._pending.pop(handle, None)
                self._handled.add(handle)
                return
            pending.scheduled = self.scheduler.call_later(
                self.retry_seconds, lambda: self._attempt(handle))

    def _cancel(self, handle: int) -> None:
        pending = self._pending.pop(handle, None)
        scheduled = pending.scheduled if pending is not None else None
        cancel = getattr(scheduled, "cancel", None)
        if callable(cancel):
            cancel()


class WindowRuleMatcher:
    match = staticmethod(select_rule)
    match_all = staticmethod(matching_rules)


class WindowRuleParser:
    parse = staticmethod(parse_rules)
    try_parse_zone = staticmethod(parse_zone)


class WindowRuleGeometry:
    try_resolve_rect = staticmethod(try_resolve_rect)
    move_between_work_areas = staticmethod(move_between_work_areas)
    union = staticmethod(union_rects)


def probe() -> None:
    """Explain inline example-rule matches for the live desk; move nothing."""
    from window_tracker import WindowTracker

    examples = (
        WindowRule(process="code", action=RuleAction.ZONE,
                   monitor_key="primary", zone="left", priority=10),
        WindowRule(process="notepad", action=RuleAction.MAXIMIZE),
        WindowRule(action=RuleAction.FLOAT, priority=-10),
    )
    print("READ-ONLY PROBE: no windows will be moved.")
    tracker = WindowTracker()
    tracker.scan()
    windows = tracker.windows
    print(f"Enumerated {len(windows)} manageable live window(s).")
    for window in windows:
        facts = WindowFacts.from_tracked(window)
        winner = select_rule(examples, facts)
        action = resolve(examples, facts)
        identity = (f"handle={window.handle:#x} process={facts.process or '(unknown)'} "
                    f"class={facts.class_name or '(unknown)'}")
        if winner is None or action is None:
            print(f"{identity}: no rule; WOULD do nothing.")
            continue
        target = f" monitor={action.monitor_key}" if action.monitor_key else ""
        zone = f" zone={action.zone}" if action.zone else ""
        print(f"{identity}: {winner.describe()}; WOULD {action.kind.value}"
              f"{target}{zone}.")
    print("Probe complete: moved 0 windows.")


if __name__ == "__main__":
    probe()
