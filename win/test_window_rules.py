"""C# parity, watcher-discipline, persistence, and structural checks."""

import ast
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from config_store import ConfigStore
import window_rules as rules
from window_tiling import PixelRect, TileZone
from window_tracker import (TrackedWindow, WindowAppeared, WindowIdentity,
                            WindowMoved)


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


def rule(process=None, title=None, class_name=None,
         action=rules.RuleAction.NONE, priority=0, zone=None,
         x=None, y=None, width=None, height=None):
    return rules.WindowRule(process, title, class_name, action, None, zone,
                            x, y, width, height, False, priority)


def window(process="code", title="Untitled - Visual Studio Code",
           class_name="Chrome_WidgetWin_1"):
    return rules.WindowFacts(process, title, class_name)


# ---- every WindowRuleTests.cs case -------------------------------------------

vague = rule(process="code", action=rules.RuleAction.MAXIMIZE)
specific = rule(process="code", class_name="Chrome_WidgetWin_1",
                action=rules.RuleAction.ZONE, zone="left")
check("a rule naming more criteria beats a vaguer one at the same priority",
      rules.select_rule([vague, specific], window()) is specific)

check("specificity counts only criteria that are actually named",
      rule().specificity == 0
      and rule(process="code").specificity == 1
      and rule(process="code", title="Code").specificity == 2
      and rule(process="code", title="Code", class_name="X").specificity == 3
      and rule(process="   ", title="", class_name=None).specificity == 0)

first = rule(process="code", action=rules.RuleAction.MAXIMIZE)
second = rule(process="code", action=rules.RuleAction.FLOAT)
check("a full tie is broken by declaration order",
      rules.select_rule([first, second], window()) is first
      and rules.select_rule([second, first], window()) is second)

specific = rule(process="code", title="Untitled",
                class_name="Chrome_WidgetWin_1", action=rules.RuleAction.ZONE,
                zone="left", priority=0)
urgent = rule(action=rules.RuleAction.FLOAT, priority=10)
winner = rules.select_rule([specific, urgent], window())
check("priority overrides specificity",
      winner is urgent and winner.action is rules.RuleAction.FLOAT)

fallback = rule(process="code", action=rules.RuleAction.MAXIMIZE, priority=-5)
ordinary = rule(action=rules.RuleAction.FLOAT)
check("negative priority pushes a rule below defaults",
      rules.select_rule([fallback, ordinary], window()) is ordinary)

check("process and title matching ignore case",
      rules.match(rule(process="CODE", title="VISUAL STUDIO"),
                  window(process="code", title="Untitled - Visual Studio Code"))
      and rules.match(rule(process="code", title="visual studio"),
                      window(process="CODE",
                             title="UNTITLED - VISUAL STUDIO CODE")))

check("a process may be written with or without the exe suffix",
      rules.match(rule(process="chrome.exe"), window(process="chrome"))
      and rules.match(rule(process="CHROME.EXE"), window(process="chrome"))
      and rules.match(rule(process="chrome"), window(process="chrome"))
      and not rules.match(rule(process="chrome"), window(process="chromium")))

check("title matching is substring and class matching is whole",
      rules.match(rule(title="Studio"),
                  window(title="Untitled - Visual Studio Code"))
      and not rules.match(rule(class_name="Chrome_Widget"),
                          window(class_name="Chrome_WidgetWin_1"))
      and rules.match(rule(class_name="chrome_widgetwin_1"),
                      window(class_name="Chrome_WidgetWin_1")))

catch_all = rule(action=rules.RuleAction.MONITOR)
named = rule(process="notepad", action=rules.RuleAction.MAXIMIZE)
check("a criterion-free rule matches everything and loses to anything specific",
      catch_all.is_catch_all
      and rules.match(catch_all, window())
      and rules.match(catch_all,
                      window(process="notepad", title="", class_name="Notepad"))
      and rules.match(catch_all, rules.WindowFacts())
      and rules.select_rule([catch_all, named], window(process="notepad")) is named
      and rules.select_rule([named, catch_all], window(process="notepad")) is named
      and rules.select_rule([catch_all, named], window(process="code")) is catch_all)

check("no rules matches nothing rather than inventing a default",
      rules.resolve([], window()) is None
      and rules.resolve([rule(process="notepad")], window(process="code")) is None)

catch_all = rule(action=rules.RuleAction.FLOAT)
by_process = rule(process="code", action=rules.RuleAction.MAXIMIZE)
urgent = rule(process="code", action=rules.RuleAction.MONITOR, priority=5)
check("match all explains the decision strongest first",
      rules.matching_rules([catch_all, by_process, urgent], window())
      == (urgent, by_process, catch_all))

parsed = rules.parse_rules(r'''
[
  { "process": "code", "action": "maximize" },
  { "process": "bad", "action": "teleport" },
  "not an object",
  { "process": "worse", "action": "rect" },
  { "process": "priority", "action": "float", "priority": "high" },
  { "process": "numbers", "action": "rect", "width": "wide", "height": 100 },
  { "process": "zoned", "action": "zone", "zone": "diagonal" },
  { "process": "notepad", "action": "zone", "zone": "top-left" }
]
''')
check("unparseable rules are skipped not thrown",
      len(parsed.rules) == 2
      and parsed.rules[0].process == "code"
      and parsed.rules[1].process == "notepad"
      and len(parsed.problems) == 6
      and all("skipped" in problem.lower() for problem in parsed.problems))

malformed = rules.parse_rules('[ { "process": ')
not_array = rules.parse_rules('{ "process": "code" }')
check("malformed JSON yields a problem and no rules",
      not malformed.rules and len(malformed.problems) == 1
      and not not_array.rules and len(not_array.problems) == 1
      and not rules.parse_rules(None).rules
      and not rules.parse_rules(None).problems
      and not rules.parse_rules("   ").problems
      and not rules.parse_rules("[]").problems)

parsed_rule = rules.parse_rules(r'''[{
  "process": "code.exe", "titleContains": "Esoteric",
  "className": "Chrome_WidgetWin_1", "action": "zone",
  "monitor": "primary", "zone": "top-left", "floating": true,
  "priority": 3, "workspace": "mon-a:1"
}]''').rules[0]
check("a parsed rule carries every field the module acts on",
      parsed_rule.process == "code.exe"
      and parsed_rule.title_contains == "Esoteric"
      and parsed_rule.action is rules.RuleAction.ZONE
      and parsed_rule.monitor_key == "primary"
      and parsed_rule.floating
      and parsed_rule.priority == 3
      and parsed_rule.workspace_id == "mon-a:1"
      and rules.parse_zone(parsed_rule.zone) is TileZone.TOP_LEFT)

float_rule = rules.parse_rules(
    '[{ "process": "calc", "action": "float" }]').rules[0]
check("the float action implies floating when the flag is absent",
      float_rule.floating)

check("zone names ignore case hyphens and spaces",
      rules.parse_zone("TOP LEFT") is TileZone.TOP_LEFT
      and rules.parse_zone("bottom-right") is TileZone.BOTTOM_RIGHT
      and rules.parse_zone("left") is TileZone.LEFT_HALF
      and rules.parse_zone("middle") is None
      and rules.parse_zone(None) is None)

work = PixelRect(-1920, 0, 1920, 1040)
rect = rules.try_resolve_rect(
    rule(action=rules.RuleAction.RECT, x=.5, y=0, width=.5, height=1), work)
check("fractions resolve against work area including negative origin",
      rect == PixelRect(-960, 0, 960, 1040))

work = PixelRect(0, 0, 3840, 2100)
rect = rules.try_resolve_rect(
    rule(action=rules.RuleAction.RECT, x=100, y=60,
         width=1280, height=800), work)
check("numbers above one are absolute virtual-screen pixels",
      rect == PixelRect(100, 60, 1280, 800))

work = PixelRect(0, 0, 1920, 1040)
check("a rule without a size describes no rectangle",
      rules.try_resolve_rect(rule(action=rules.RuleAction.RECT,
                                  x=.25, y=.25), work) is None
      and rules.try_resolve_rect(rule(action=rules.RuleAction.RECT,
                                      width=0, height=0), work) is None)

source = PixelRect(0, 0, 3840, 2100)
target = PixelRect(3840, 0, 1920, 1040)
current = PixelRect(960, 525, 800, 600)
check("moving monitors keeps relative position and size",
      rules.move_between_work_areas(current, source, target)
      == PixelRect(4320, 260, 800, 600))

source = PixelRect(0, 0, 3840, 2100)
target = PixelRect(-1920, 0, 1920, 1040)
moved = rules.move_between_work_areas(
    PixelRect(0, 0, 3000, 1800), source, target)
check("an oversized window is shrunk rather than hung off the edge",
      moved == PixelRect(-1920, 0, 1920, 1040)
      and target.intersect(moved) == moved)

target = PixelRect(0, 0, 1920, 1040)
moved = rules.move_between_work_areas(
    PixelRect(3600, 2000, 400, 300), source, target)
check("a window near the far edge is pulled onto target",
      moved == target.intersect(moved) and moved.x == 1520 and moved.y == 740)

current = PixelRect(10, 10, 100, 100)
empty = PixelRect(0, 0, 0, 0)
check("moving between degenerate areas leaves window alone",
      rules.move_between_work_areas(current, empty,
                                    PixelRect(0, 0, 800, 600)) == current
      and rules.move_between_work_areas(
          current, PixelRect(0, 0, 800, 600), empty) == current)

left = PixelRect(-1920, 120, 1920, 1040)
right = PixelRect(0, 0, 3840, 2100)
check("union of work areas is the virtual desktop",
      rules.union_rects([left, right]) == PixelRect(-1920, 0, 5760, 2100)
      and rules.union_rects([right, empty]) == right
      and rules.union_rects([]).is_empty)


# ---- brief additions ----------------------------------------------------------

resolved = rules.resolve(
    [rule(action=rules.RuleAction.FLOAT),
     rule(process="code", action=rules.RuleAction.MAXIMIZE),
     rule(process="code", action=rules.RuleAction.MONITOR, priority=4)],
    window())
check("resolve applies precedence when several rules match",
      resolved is not None and resolved.kind is rules.RuleAction.MONITOR)


class FakeScheduled:
    def __init__(self, callback):
        self.callback = callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeScheduler:
    def __init__(self):
        self.pending = []

    def call_later(self, delay, callback):
        item = FakeScheduled(callback)
        self.pending.append(item)
        return item

    def run_all(self):
        while self.pending:
            item = self.pending.pop(0)
            if not item.cancelled:
                item.callback()


identity = WindowIdentity(r"C:\Apps\code.exe", "Chrome_WidgetWin_1", "Code")
tracked = TrackedWindow(77, identity, 100)
scheduler = FakeScheduler()
attempts = []
watcher = rules.WindowRuleWatcher(
    [rule(process="code", action=rules.RuleAction.MAXIMIZE)],
    lambda handle, action: attempts.append((handle, action)) or False,
    scheduler=scheduler, max_attempts=3)
watcher.on_event(WindowAppeared(tracked))
scheduler.run_all()
check("bounded retries give up rather than looping",
      len(attempts) == 3 and not scheduler.pending)

scheduler = FakeScheduler()
attempts = []
watcher = rules.WindowRuleWatcher(
    [rule(process="code", action=rules.RuleAction.MAXIMIZE)],
    lambda handle, action: attempts.append(handle) or False,
    scheduler=scheduler, max_attempts=3)
watcher.on_event(WindowAppeared(tracked))
watcher.on_event(WindowMoved(tracked))
scheduler.run_all()
check("a user move cancels placement without a fight", not attempts)

with tempfile.TemporaryDirectory() as directory:
    store = ConfigStore(directory)
    original = rules.WindowRule(
        process="code.exe", title_contains="Esoteric",
        class_name="Chrome_WidgetWin_1", action=rules.RuleAction.ZONE,
        monitor_key="primary", zone="top-left", x=.1, y=.2,
        width=.5, height=.75, floating=True, priority=3,
        workspace_id="mon-a:1")
    rules.save_rules(store, [original])
    reloaded = rules.load_rules(ConfigStore(directory))
check("persistence round-trips through a temp-rooted ConfigStore",
      reloaded.rules == (original,) and not reloaded.problems)

source_path = pathlib.Path(rules.__file__)
tree = ast.parse(source_path.read_text(encoding="utf-8"))
top_level_calls = [node for node in tree.body if isinstance(node, ast.Expr)
                   and isinstance(node.value, ast.Call)]
check("import and watcher construction install nothing",
      not top_level_calls and "SetWinEventHook" not in source_path.read_text())
