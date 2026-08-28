"""Pure scope model and chord resolution for native EsotericOS scripts.

Importing this module, constructing anything in it, and calling every function
it exports performs no Windows call, installs no hook, reads no file and
touches no window.  Resolution is a function of its arguments and nothing else,
so the whole nesting model is exhaustively testable without a keyboard, a
screen, or a running app.

Window matching is deliberately NOT reimplemented here.  ``window_rules``
already owns the one lawful matcher and the one lawful ordering -- priority,
then specificity, then declaration order -- so a scope borrows it by building a
criteria-only :class:`WindowRule` and calling ``window_rules.match``.  The only
thing this module adds is the *level*: window beats screen beats os, ahead of
all three of those tiebreakers.

Nesting falls out of one rule: a scope is a conjunction of optional
constraints, and its level is the narrowest constraint it names.  A scope that
names a screen and a process is a window-level scope which must satisfy both,
which is exactly what "on that monitor, in that app" means.
"""

from __future__ import annotations

import dataclasses
import enum
import functools
import re
from collections.abc import Sequence
from typing import Any

from keyboard_interception import KeyChord, is_protected
from window_rules import WindowFacts, WindowRule
from window_rules import match as match_window


__all__ = [
    "Action",
    "Binding",
    "BindingSet",
    "PRIMARY_SCREEN_KEY",
    "Resolution",
    "ResolutionVerdict",
    "STABLE_KEY_PATTERN",
    "Scope",
    "ScopeLevel",
    "ScreenFacts",
    "Verb",
    "WindowFacts",
    "is_protected",
    "ordered_matches",
    "resolve",
]


# The one reserved screen name.  Every other screen name is a
# ``MonitorIdentity.stable_key``: sixteen lowercase hex characters.
PRIMARY_SCREEN_KEY = "primary"
STABLE_KEY_PATTERN = re.compile(r"^[0-9a-f]{16}$")


class ScopeLevel(enum.IntEnum):
    """Narrowness.  Higher wins, and it wins before priority."""

    OS = 0
    SCREEN = 1
    WINDOW = 2

    @property
    def label(self) -> str:
        return self.name.lower()


@dataclasses.dataclass(frozen=True, slots=True)
class ScreenFacts:
    """What the resolver needs to know about the screen a chord happened on.

    ``stable_key`` is a ``MonitorIdentity.stable_key`` or None when the display
    announces nothing durable; ``is_primary`` answers the reserved name.  Both
    are supplied by the caller, so resolution never enumerates a monitor.
    """

    stable_key: str | None = None
    is_primary: bool = False


def _specified(value: str | None) -> bool:
    # Mirrors WindowRule._specified so a blank string is not a criterion.
    return value is not None and bool(value.strip())


@functools.lru_cache(maxsize=512)
def _criteria_rule(process: str | None, title_contains: str | None,
                   class_name: str | None) -> WindowRule:
    """A criteria-only WindowRule, cached because the hook path is hot."""
    return WindowRule(process=process, title_contains=title_contains,
                      class_name=class_name)


@dataclasses.dataclass(frozen=True, slots=True)
class Scope:
    """The narrowest situation a binding is willing to fire in.

    Every field is optional and every named field must match.  ``screen_key``
    is ``"primary"`` or a monitor stable key; the remaining three are the
    ``WindowRule`` criteria, matched by ``window_rules.match`` with its own
    case-insensitive, ``.exe``-stripping, substring-title semantics.
    """

    screen_key: str | None = None
    process: str | None = None
    title_contains: str | None = None
    class_name: str | None = None

    @property
    def window_rule(self) -> WindowRule:
        return _criteria_rule(self.process, self.title_contains,
                              self.class_name)

    @property
    def names_screen(self) -> bool:
        return _specified(self.screen_key)

    @property
    def window_specificity(self) -> int:
        return self.window_rule.specificity

    @property
    def level(self) -> ScopeLevel:
        if self.window_specificity:
            return ScopeLevel.WINDOW
        if self.names_screen:
            return ScopeLevel.SCREEN
        return ScopeLevel.OS

    @property
    def specificity(self) -> int:
        """Count of named criteria, screen included.  Zero for bare os."""
        return self.window_specificity + (1 if self.names_screen else 0)

    def matches_screen(self, screen: ScreenFacts) -> bool:
        if not self.names_screen:
            return True
        wanted = self.screen_key.strip().lower()
        if wanted == PRIMARY_SCREEN_KEY:
            return bool(screen.is_primary)
        key = screen.stable_key
        return key is not None and key.strip().lower() == wanted

    def matches(self, facts: WindowFacts, screen: ScreenFacts) -> bool:
        return (self.matches_screen(screen)
                and match_window(self.window_rule, facts))

    def describe(self) -> str:
        if self.level is ScopeLevel.OS:
            return "os"
        parts: list[str] = []
        if self.names_screen:
            parts.append(f"screen {self.screen_key.strip()}")
        criteria: list[str] = []
        if _specified(self.process):
            criteria.append(f"process={self.process.strip()}")
        if _specified(self.class_name):
            criteria.append(f"class={self.class_name.strip()}")
        if _specified(self.title_contains):
            criteria.append(f"title~{self.title_contains.strip()}")
        if criteria:
            parts.append("window " + " ".join(criteria))
        return " ".join(parts)


OS_SCOPE = Scope()


class Verb(enum.Enum):
    """Only what EsotericOS can already do, plus the fall-through."""

    SEND = "send"
    WINDOW = "window"
    CATALOG = "catalog"
    PASS = "pass"


@dataclasses.dataclass(frozen=True, slots=True)
class Action:
    """One resolved script step.  Pure data; execution lives in the engine.

    ``payload`` carries whatever the parser already validated -- a KeySequence
    for SEND, a TileZone or TileDirection for WINDOW -- so nothing is parsed a
    second time inside a keyboard hook.  It is always hashable.
    """

    verb: Verb
    name: str = ""
    argument: str = ""
    payload: Any = None

    def describe(self) -> str:
        parts = [self.verb.value, self.name, self.argument]
        return " ".join(part for part in parts if part)


PASS_ACTION = Action(Verb.PASS)


@dataclasses.dataclass(frozen=True, slots=True)
class Binding:
    """One chord, in one scope, with the steps it runs."""

    chord: KeyChord
    scope: Scope = OS_SCOPE
    actions: tuple[Action, ...] = ()
    priority: int = 0
    source: str = ""
    line: int = 0
    order: int = 0

    @property
    def level(self) -> ScopeLevel:
        return self.scope.level

    @property
    def specificity(self) -> int:
        return self.scope.specificity

    @property
    def is_pass(self) -> bool:
        """True when this binding explicitly yields to the next match."""
        return len(self.actions) == 1 and self.actions[0].verb is Verb.PASS

    def matches(self, facts: WindowFacts, screen: ScreenFacts) -> bool:
        return self.scope.matches(facts, screen)

    def describe(self) -> str:
        steps = " ; ".join(action.describe() for action in self.actions)
        where = f"{self.source}:{self.line}" if self.source else "(inline)"
        return (f"[{self.scope.describe()}] {self.chord} -> {steps} "
                f"(priority {self.priority}, from {where})")


def _sort_key(item: tuple[int, Binding]) -> tuple[int, int, int, int, int]:
    position, binding = item
    return (-int(binding.level), -binding.priority, -binding.specificity,
            binding.order, position)


def ordered_matches(bindings: Sequence[Binding], chord: KeyChord,
                    facts: WindowFacts,
                    screen: ScreenFacts) -> tuple[Binding, ...]:
    """Every binding for *chord* that matches, strongest first.

    The order is window > screen > os, then priority, then specificity, then
    declaration order -- the last three exactly as ``window_rules`` already
    resolves competing rules.  ``Binding.order`` (the global declaration index
    assigned when a set is built) is compared before sequence position, so
    filtering the set down to one chord's candidates cannot change the answer.
    """
    indexed = [(position, binding)
               for position, binding in enumerate(bindings)
               if binding.chord == chord and binding.matches(facts, screen)]
    indexed.sort(key=_sort_key)
    return tuple(binding for _position, binding in indexed)


class ResolutionVerdict(enum.Enum):
    HANDLE = enum.auto()
    PASS_TO_OS = enum.auto()


@dataclasses.dataclass(frozen=True, slots=True)
class Resolution:
    """The whole answer, including the reasoning, for one chord."""

    chord: KeyChord
    verdict: ResolutionVerdict
    binding: Binding | None = None
    matched: tuple[Binding, ...] = ()
    passed_over: tuple[Binding, ...] = ()
    reason: str = ""

    @property
    def handled(self) -> bool:
        return self.verdict is ResolutionVerdict.HANDLE

    def describe(self) -> str:
        if self.binding is not None:
            return f"{self.chord} -> {self.binding.describe()}"
        return f"{self.chord} -> pass to Windows ({self.reason})"


def resolve(bindings: Sequence[Binding], chord: KeyChord,
            facts: WindowFacts, screen: ScreenFacts) -> Resolution:
    """Decide one chord.  Pure: same inputs, same answer, always.

    A protected chord is answered before any binding is consulted, so no script
    can ever claim Ctrl+Alt+Delete.  Otherwise the strongest match wins, unless
    it is an explicit ``pass``, in which case the next-most-specific match is
    tried, and so on; when every match passes, so does the chord.
    """
    if is_protected(chord):
        return Resolution(chord, ResolutionVerdict.PASS_TO_OS,
                          reason="protected chord")
    matched = ordered_matches(bindings, chord, facts, screen)
    if not matched:
        return Resolution(chord, ResolutionVerdict.PASS_TO_OS,
                          reason="no binding matched")
    passed_over: list[Binding] = []
    for binding in matched:
        if binding.is_pass:
            passed_over.append(binding)
            continue
        return Resolution(chord, ResolutionVerdict.HANDLE, binding, matched,
                          tuple(passed_over), "matched")
    return Resolution(chord, ResolutionVerdict.PASS_TO_OS, None, matched,
                      tuple(passed_over), "every match passed")


class BindingSet:
    """An ordered, chord-indexed collection of bindings plus its problems.

    The index is a pre-filter only.  ``for_chord`` preserves declaration order,
    and ``ordered_matches`` breaks ties on the global ``Binding.order`` before
    sequence position, so resolving against the index and resolving against the
    whole set are provably the same answer.
    """

    def __init__(self, bindings: Sequence[Binding] = (),
                 problems: Sequence[Any] = ()) -> None:
        self.bindings: tuple[Binding, ...] = tuple(bindings)
        self.problems: tuple[Any, ...] = tuple(problems)
        index: dict[KeyChord, list[Binding]] = {}
        for binding in self.bindings:
            index.setdefault(binding.chord, []).append(binding)
        self._by_chord = {chord: tuple(items)
                          for chord, items in index.items()}

    def __len__(self) -> int:
        return len(self.bindings)

    @property
    def chords(self) -> tuple[KeyChord, ...]:
        return tuple(self._by_chord)

    def claims(self, chord: KeyChord) -> bool:
        """Whether any binding mentions *chord* at all, in any scope."""
        return chord in self._by_chord

    def for_chord(self, chord: KeyChord) -> tuple[Binding, ...]:
        return self._by_chord.get(chord, ())

    def resolve(self, chord: KeyChord, facts: WindowFacts,
                screen: ScreenFacts) -> Resolution:
        return resolve(self.for_chord(chord), chord, facts, screen)
