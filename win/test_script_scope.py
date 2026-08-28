"""Exhaustive resolution checks for script_scope.py.

Nothing here touches a keyboard, a window, a monitor or a file, and that is the
point: resolution is a pure function of (chord, window facts, screen key,
binding set), so every nesting case can be stated as an equation.

    python win\\test_script_scope.py
"""

import ast
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from keyboard_interception import PROTECTED_CHORDS, ChordModifiers, KeyChord
import script_scope as sc
from script_scope import (Action, Binding, BindingSet, ResolutionVerdict,
                          Scope, ScopeLevel, ScreenFacts, Verb, WindowFacts,
                          ordered_matches, resolve)


fails = []


def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        if detail:
            print("      " + str(detail))
        fails.append(name)


CHORD = KeyChord(ChordModifiers.CTRL | ChordModifiers.ALT, "H")
OTHER = KeyChord(ChordModifiers.CTRL | ChordModifiers.ALT, "J")
CODE = WindowFacts("code", "esoteric-path - Visual Studio Code",
                   "Chrome_WidgetWin_1")
NOTEPAD = WindowFacts("notepad", "Untitled - Notepad", "Notepad")
DESK = ScreenFacts("9f2c4a1b7e0d3355", True)
SIDE = ScreenFacts("00112233445566aa", False)
NAMELESS = ScreenFacts(None, False)

CENTER = (Action(Verb.WINDOW, "center"),)
MAXIMIZE = (Action(Verb.WINDOW, "tile", "left"),)
FALL = (Action(Verb.PASS),)


def bind(scope=Scope(), priority=0, actions=CENTER, order=0, chord=CHORD,
         line=1, source="a.eos"):
    return Binding(chord, scope, actions, priority, source, line, order)


# ---- the level is derived from the narrowest constraint named ----------------

check("a scope naming nothing is os level",
      Scope().level is ScopeLevel.OS, Scope().level)
check("a scope naming only a screen is screen level",
      Scope(screen_key="primary").level is ScopeLevel.SCREEN)
check("a scope naming a window criterion is window level",
      Scope(process="code").level is ScopeLevel.WINDOW
      and Scope(title_contains="Code").level is ScopeLevel.WINDOW
      and Scope(class_name="Notepad").level is ScopeLevel.WINDOW)
check("naming a screen AND a window is window level -- that is the nesting",
      Scope(screen_key="primary", process="code").level is ScopeLevel.WINDOW)
check("a blank criterion is not a criterion",
      Scope(process="   ", screen_key="").level is ScopeLevel.OS)

check("specificity counts the screen and every window criterion",
      Scope().specificity == 0
      and Scope(screen_key="primary").specificity == 1
      and Scope(process="code").specificity == 1
      and Scope(screen_key="primary", process="code").specificity == 2
      and Scope(screen_key="primary", process="code", title_contains="x",
                class_name="y").specificity == 4)


# ---- window matching is borrowed from window_rules, not rewritten ------------

check("the matcher is window_rules.match, reached through a criteria rule",
      sc.match_window.__module__ == "window_rules"
      and isinstance(Scope(process="code").window_rule,
                     sc.WindowRule))
check("process matching strips .exe and ignores case, as window_rules does",
      Scope(process="CODE.EXE").matches(CODE, DESK)
      and not Scope(process="notepad").matches(CODE, DESK))
check("title is a case-insensitive substring",
      Scope(title_contains="visual studio").matches(CODE, DESK)
      and not Scope(title_contains="emacs").matches(CODE, DESK))
check("class name is matched whole and case-insensitively",
      Scope(class_name="chrome_widgetwin_1").matches(CODE, DESK)
      and not Scope(class_name="Chrome").matches(CODE, DESK))
check("every named criterion must match, not any",
      Scope(process="code", class_name="Notepad").matches(CODE, DESK) is False)


# ---- screen matching --------------------------------------------------------

check("primary is the one reserved screen name",
      Scope(screen_key="primary").matches(CODE, DESK)
      and not Scope(screen_key="primary").matches(CODE, SIDE))
check("a stable key matches its screen, case-insensitively",
      Scope(screen_key="00112233445566AA").matches(CODE, SIDE)
      and not Scope(screen_key="00112233445566aa").matches(CODE, DESK))
check("a screen that announces no durable key matches no key",
      not Scope(screen_key="9f2c4a1b7e0d3355").matches(CODE, NAMELESS))
check("a screen-less scope ignores the screen entirely",
      Scope(process="code").matches(CODE, NAMELESS))
check("nesting requires both halves",
      Scope(screen_key="primary", process="code").matches(CODE, DESK)
      and not Scope(screen_key="primary", process="code").matches(CODE, SIDE)
      and not Scope(screen_key="primary", process="code").matches(NOTEPAD,
                                                                  DESK))


# ---- ordering: level first, then priority, specificity, declaration order ----

os_rule = bind(Scope(), priority=100, order=0)
window_rule = bind(Scope(process="code"), priority=0, order=1)
result = resolve([os_rule, window_rule], CHORD, CODE, DESK)
check("a narrower scope wins even against a much higher outer priority",
      result.binding is window_rule, result.describe())

screen_rule = bind(Scope(screen_key="primary"), priority=50, order=1)
check("window beats screen beats os",
      resolve([os_rule, screen_rule, window_rule], CHORD, CODE,
              DESK).binding is window_rule
      and resolve([os_rule, screen_rule], CHORD, CODE,
                  DESK).binding is screen_rule
      and resolve([os_rule], CHORD, CODE, DESK).binding is os_rule)

vague_urgent = bind(Scope(process="code"), priority=10, order=0)
precise_calm = bind(Scope(process="code", class_name="Chrome_WidgetWin_1",
                          title_contains="Visual"), priority=0, order=1)
check("inside one level, priority still outranks specificity",
      resolve([precise_calm, vague_urgent], CHORD, CODE,
              DESK).binding is vague_urgent)

vague = bind(Scope(process="code"), order=0)
precise = bind(Scope(process="code", class_name="Chrome_WidgetWin_1"),
               order=1)
check("inside one level and priority, more named criteria wins",
      resolve([vague, precise], CHORD, CODE, DESK).binding is precise
      and resolve([precise, vague], CHORD, CODE, DESK).binding is precise)

first = bind(Scope(process="code"), order=0, actions=CENTER)
second = bind(Scope(process="code"), order=1, actions=MAXIMIZE)
check("a complete tie is broken by declaration order",
      resolve([first, second], CHORD, CODE, DESK).binding is first
      and resolve([second, first], CHORD, CODE, DESK).binding is first)

unordered_a = bind(Scope(process="code"), order=0)
unordered_b = bind(Scope(process="code"), order=0)
check("with no declaration index, sequence position breaks the tie",
      resolve([unordered_a, unordered_b], CHORD, CODE,
              DESK).binding is unordered_a
      and resolve([unordered_b, unordered_a], CHORD, CODE,
                  DESK).binding is unordered_b)

demoted = bind(Scope(process="code"), priority=-5, order=0)
ordinary = bind(Scope(process="code"), priority=0, order=1)
check("negative priority pushes a binding below its neighbours in the level",
      resolve([demoted, ordinary], CHORD, CODE, DESK).binding is ordinary)


# ---- explicit pass ----------------------------------------------------------

falls = bind(Scope(process="code"), actions=FALL, order=0)
catches = bind(Scope(screen_key="primary"), actions=CENTER, order=1)
result = resolve([falls, catches], CHORD, CODE, DESK)
check("an explicit pass falls through to the next-most-specific match",
      result.binding is catches and result.passed_over == (falls,),
      result.describe())

check("a pass binding is recognised only when pass stands alone",
      falls.is_pass
      and not bind(actions=CENTER).is_pass
      and not bind(actions=(Action(Verb.PASS), Action(Verb.WINDOW,
                                                      "center"))).is_pass)

all_pass = [bind(Scope(process="code"), actions=FALL, order=0),
            bind(Scope(screen_key="primary"), actions=FALL, order=1),
            bind(Scope(), actions=FALL, order=2)]
result = resolve(all_pass, CHORD, CODE, DESK)
check("when every match passes, the chord passes to the OS",
      result.verdict is ResolutionVerdict.PASS_TO_OS
      and result.binding is None
      and len(result.passed_over) == 3
      and result.reason == "every match passed", result.reason)

check("pass falls through levels in order, not straight to os",
      resolve([bind(Scope(process="code"), actions=FALL, order=0),
               bind(Scope(screen_key="primary"), actions=CENTER, order=1),
               bind(Scope(), actions=MAXIMIZE, order=2)],
              CHORD, CODE, DESK).binding.scope.level is ScopeLevel.SCREEN)


# ---- what never gets claimed ------------------------------------------------

for protected in sorted(PROTECTED_CHORDS, key=str):
    claimed = bind(Scope(), chord=protected, actions=CENTER)
    verdict = resolve([claimed], protected, CODE, DESK)
    check(f"the protected chord {protected} always passes to Windows",
          verdict.verdict is ResolutionVerdict.PASS_TO_OS
          and verdict.binding is None
          and verdict.reason == "protected chord", verdict.reason)

check("a chord nobody bound passes to the OS",
      resolve([bind(Scope())], OTHER, CODE, DESK).verdict
      is ResolutionVerdict.PASS_TO_OS)
check("a bound chord in a scope that does not match passes to the OS",
      resolve([bind(Scope(process="notepad"))], CHORD, CODE, DESK).verdict
      is ResolutionVerdict.PASS_TO_OS)
check("an empty binding set passes everything",
      resolve([], CHORD, CODE, DESK).verdict is ResolutionVerdict.PASS_TO_OS)


# ---- ordered_matches reports the whole reasoning ----------------------------

everything = [bind(Scope(), order=0),
              bind(Scope(screen_key="primary"), order=1),
              bind(Scope(process="code"), order=2),
              bind(Scope(process="notepad"), order=3)]
ordered = ordered_matches(everything, CHORD, CODE, DESK)
check("ordered_matches returns every match, strongest first, matches only",
      [item.scope.level for item in ordered] == [ScopeLevel.WINDOW,
                                                 ScopeLevel.SCREEN,
                                                 ScopeLevel.OS],
      [item.scope.describe() for item in ordered])


# ---- determinism and index equivalence --------------------------------------

check("the same inputs give the same answer twice",
      resolve(everything, CHORD, CODE, DESK)
      == resolve(everything, CHORD, CODE, DESK))
check("a third and fourth call agree with the first",
      len({resolve(everything, CHORD, CODE, DESK) for _ in range(4)}) == 1)

mixed = list(everything) + [bind(Scope(process="code"), chord=OTHER, order=4)]
indexed = BindingSet(mixed)
check("resolving through the chord index equals resolving the whole set",
      indexed.resolve(CHORD, CODE, DESK) == resolve(mixed, CHORD, CODE, DESK)
      and indexed.resolve(OTHER, CODE, DESK) == resolve(mixed, OTHER, CODE,
                                                        DESK))
check("the index reports which chords are claimed at all",
      indexed.claims(CHORD) and indexed.claims(OTHER)
      and not indexed.claims(KeyChord(ChordModifiers.CTRL, "Z")))
check("an empty set claims nothing and resolves to a pass",
      not BindingSet().claims(CHORD)
      and BindingSet().resolve(CHORD, CODE, DESK).verdict
      is ResolutionVerdict.PASS_TO_OS)


# ---- the module does what its docstring promises -----------------------------

source = pathlib.Path(__file__).with_name("script_scope.py").read_text(
    encoding="utf-8")
tree = ast.parse(source)
banned = {"SetWindowsHookExW", "SetWindowsHookExA", "RegisterHotKey",
          "SendInput", "WinDLL", "GetForegroundWindow", "open", "read_text"}
called = {node.func.attr if isinstance(node.func, ast.Attribute)
          else getattr(node.func, "id", "")
          for node in ast.walk(tree) if isinstance(node, ast.Call)}
check("the pure model calls nothing native and reads no file",
      not (banned & called), sorted(banned & called))
check("it imports ctypes nowhere",
      "ctypes" not in {name.name for node in ast.walk(tree)
                       if isinstance(node, ast.Import)
                       for name in node.names})


print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
