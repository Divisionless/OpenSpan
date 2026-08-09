"""Display arrangement profiles: pure model, fake apply, and safety guards."""

import ast
import dataclasses
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import display_profiles as dp
from monitor_identity import MonitorIdentity, MonitorMatch


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


def panel(serial, friendly="DELL U2720Q", manufacturer="DEL", product="A0B1",
          w=3840, h=2160, device=r"\\.\DISPLAY1", x=0, y=0):
    return MonitorIdentity(manufacturer, product, serial, friendly, w, h,
                           device, x, y)


def entry(identity, x, y, w=3840, h=2160, hz=60, primary=False,
          orientation=dp.DisplayOrientation.LANDSCAPE):
    return dp.DisplayProfileEntry(identity, x, y, w, h, hz, orientation, primary)


LAPTOP = panel("SN-LAPTOP", "Internal Display", "LEN", "4001", 2560, 1600,
               r"\\.\DISPLAY1")
DESK = panel("SN-DESK", "DELL U2720Q", "DEL", "A0B1", 3840, 2160,
             r"\\.\DISPLAY2")
PORTRAIT = panel("SN-PORT", "LG 27UP", "GSM", "5B09", 3840, 2160,
                 r"\\.\DISPLAY3")


# ---- faithful translations of every DisplayProfileTests.cs test --------------------

profile = dp.DisplayProfile("Desk", [
    entry(LAPTOP, 0, 0, 2560, 1600, primary=True), entry(DESK, 2560, 0)])
attached = [dataclasses.replace(DESK, device_name=r"\\.\DISPLAY1", virtual_x=0),
            dataclasses.replace(LAPTOP, device_name=r"\\.\DISPLAY2", virtual_x=3840)]
check("a profile matches the same panels whatever order and port they return on",
      dp.matches(profile, attached))

laptop_only = dp.DisplayProfile("Laptop only", [
    entry(LAPTOP, 0, 0, 2560, 1600, primary=True)])
check("a profile covering only some attached monitors does not match",
      dp.matches(laptop_only, [LAPTOP])
      and not dp.matches(laptop_only, [LAPTOP, DESK])
      and not dp.matches(laptop_only, [DESK]))

replaced = [LAPTOP, PORTRAIT]
check("a replaced panel breaks the match rather than being guessed",
      not dp.matches(profile, replaced) and not dp.build_plan(profile, replaced))

anonymous = MonitorIdentity(native_width=1920, native_height=1080,
                            device_name=r"\\.\DISPLAY1")
anon_profile = dp.DisplayProfile("Anon", [entry(
    anonymous, 0, 0, 1920, 1080, primary=True)])
check("matching never falls back to position",
      dp.MINIMUM_MATCH == MonitorMatch.NAME
      and not dp.matches(anon_profile, [anonymous]))

empty = dp.DisplayProfile("Empty", [])
check("an empty profile is never applicable",
      not dp.matches(empty, []) and not empty.is_usable)

travel = dp.DisplayProfile("Travel", [entry(
    LAPTOP, 0, 0, 2560, 1600, primary=True)])
check("exactly one matching profile is chosen",
      dp.choose_applicable([profile, travel], [LAPTOP, DESK]) is profile
      and dp.choose_applicable([profile, travel], [LAPTOP]) is travel)

stacked = dp.DisplayProfile("Stacked", [
    entry(LAPTOP, 0, 1600, 2560, 1600), entry(DESK, 0, 0, primary=True)])
side_by_side = dp.DisplayProfile("Side by side", [
    entry(LAPTOP, 0, 0, 2560, 1600, primary=True), entry(DESK, 2560, 0)])
check("two profiles for the same hardware are ambiguous",
      dp.choose_applicable([stacked, side_by_side], [LAPTOP, DESK]) is None)
check("no matching profile chooses nothing",
      dp.choose_applicable([profile], [PORTRAIT]) is None
      and dp.choose_applicable([], [LAPTOP]) is None)

renamed = [dataclasses.replace(LAPTOP, device_name=r"\\.\DISPLAY4"),
           dataclasses.replace(DESK, device_name=r"\\.\DISPLAY5")]
plan = dp.build_plan(profile, renamed)
check("the plan targets the device names the panels have now",
      len(plan) == 2 and plan[0].device_name == r"\\.\DISPLAY4"
      and plan[1].device_name == r"\\.\DISPLAY5")

right_primary = dp.DisplayProfile("Desk", [
    entry(LAPTOP, -2560, 280, 2560, 1600),
    entry(DESK, 0, 0, primary=True)])
plan = dp.build_plan(right_primary, [LAPTOP, DESK])
check("the primary comes first and every position is relative to it",
      plan[0].is_primary and plan[0].device_name == DESK.device_name
      and (plan[0].x, plan[0].y) == (0, 0)
      and not plan[1].is_primary and (plan[1].x, plan[1].y) == (-2560, 280))

shifted = dp.DisplayProfile("Shifted", [
    entry(LAPTOP, 3840, 0, 2560, 1600, primary=True), entry(DESK, 0, 0)])
plan = dp.build_plan(shifted, [LAPTOP, DESK])
check("a profile with a shifted primary is translated to the origin",
      (plan[0].x, plan[0].y) == (0, 0)
      and (plan[1].x, plan[1].y) == (-3840, 0))

unflagged = dp.DisplayProfile("Unflagged", [
    entry(LAPTOP, -2560, 0, 2560, 1600), entry(DESK, 0, 0)])
plan = dp.build_plan(unflagged, [LAPTOP, DESK])
check("a profile flagging no primary uses the monitor at the origin",
      plan[0].is_primary and plan[0].device_name == DESK.device_name
      and sum(change.is_primary for change in plan) == 1)

confused = dp.DisplayProfile("Confused", [
    entry(LAPTOP, 100, 100, 2560, 1600, primary=True),
    entry(DESK, 0, 0, primary=True)])
plan = dp.build_plan(confused, [LAPTOP, DESK])
check("several primary flags still produce exactly one primary",
      sum(change.is_primary for change in plan) == 1
      and plan[0].is_primary and plan[0].x == 0)

portrait_right = dp.DisplayProfile("Portrait right", [
    entry(DESK, 0, 0, 3840, 2160, 144, True),
    entry(PORTRAIT, 3840, 0, 2160, 3840, 60, False,
          dp.DisplayOrientation.PORTRAIT)])
plan = dp.build_plan(portrait_right, [DESK, PORTRAIT])
check("mode refresh rate and orientation survive the round trip",
      plan[0].refresh_hz == 144
      and plan[0].orientation is dp.DisplayOrientation.LANDSCAPE
      and plan[1].orientation is dp.DisplayOrientation.PORTRAIT
      and (plan[1].width, plan[1].height) == (2160, 3840))

first = dp.build_plan(profile, [LAPTOP, DESK])
check("the plan is identical across repeated builds",
      all(dp.build_plan(profile, [LAPTOP, DESK]) == first for _ in range(5)))

original = dp.DisplayProfile("Desk", [entry(
    LAPTOP, 0, 0, 2560, 1600, primary=True)])
travel = dp.DisplayProfile("Travel", [entry(DESK, 0, 0, primary=True)])
updated = dp.DisplayProfile(" desk ", [entry(DESK, 0, 0, primary=True)])
result = dp.upsert([original, travel], updated)
check("saving an existing name replaces it in place",
      len(result) == 2 and result[0] is updated and result[1] is travel)

result = dp.upsert([], travel)
check("a new name appends and lookup ignores case and padding",
      result == [travel] and dp.find(result, "  TRAVEL ") is travel
      and dp.find(result, "desk") is None and dp.find(result, None) is None)

saved = [
    dp.DisplayProfile("Desk", [
        entry(DESK, 0, 0, 3840, 2160, 144, True),
        entry(PORTRAIT, 3840, -200, 2160, 3840, 60, False,
              dp.DisplayOrientation.PORTRAIT_FLIPPED)]),
    dp.DisplayProfile("Travel", [entry(
        LAPTOP, 0, 0, 2560, 1600, 120, True)]),
]
restored, problems = dp.read_profiles(dp.write_profiles(saved))
check("profiles survive a settings-file round trip",
      not problems and restored == saved)

text = dp.write_profiles([dp.DisplayProfile("P", [entry(
    PORTRAIT, 0, 0, orientation=dp.DisplayOrientation.PORTRAIT)])])
check("orientation is a readable word and derived keys are not written",
      '"Portrait"' in text and "StableKey" not in text)

mixed = r'''
[
  { "Name": "Good", "Entries": [ { "Identity": { "FriendlyName": "P", "NativeWidth": 1920,
      "NativeHeight": 1080, "DeviceName": "\\\\.\\DISPLAY1" },
      "X": 0, "Y": 0, "Width": 1920, "Height": 1080, "RefreshHz": 60,
      "Orientation": "Landscape", "IsPrimary": true } ] },
  { "Name": "Nameless entries", "Entries": [] },
  { "Entries": [] },
  "not an object"
]
'''
kept, problems = dp.read_profiles(mixed)
check("one unreadable profile does not take the others with it",
      len(kept) == 1 and kept[0].name == "Good"
      and kept[0].entries[0].width == 1920 and len(problems) == 3)

check("malformed or absent JSON yields no profiles rather than an exception",
      dp.read_profiles(None) == ([], []) and dp.read_profiles("   ")[1] == []
      and len(dp.read_profiles("[ {")[1]) == 1
      and len(dp.read_profiles('{ "Name": "x" }')[1]) == 1
      and dp.read_profiles("[]")[1] == [])


# ---- Python-specific acceptance tests from the brief -----------------------------

current = [
    dp.AttachedDisplay(LAPTOP, 0, 0, 2560, 1600, 60,
                       dp.DisplayOrientation.LANDSCAPE, True),
    dp.AttachedDisplay(DESK, 2560, 0, 3840, 2160, 60,
                       dp.DisplayOrientation.LANDSCAPE, False),
]
check("the plan is empty when the topology already matches",
      dp.build_plan(profile, current) == ())

shuffled_current = [
    dataclasses.replace(current[1], identity=dataclasses.replace(
        DESK, device_name=r"\\.\DISPLAY8")),
    dataclasses.replace(current[0], identity=dataclasses.replace(
        LAPTOP, device_name=r"\\.\DISPLAY9")),
]
check("profile matching survives a device-name shuffle",
      dp.matches(profile, shuffled_current)
      and profile.monitor_keys == (LAPTOP.stable_key, DESK.stable_key))

target = dp.DisplayProfile("Stacked", [
    entry(LAPTOP, 0, 2160, 2560, 1600),
    entry(DESK, 0, 0, primary=True)])
calls = []


def fake_applier(plan_to_apply):
    calls.append(tuple(plan_to_apply))
    return dp.ApplyOutcome(True)


transaction = dp.apply_profile(target, fake_applier, current=current,
                               confirmation=lambda _seconds: False, timeout=0)
check("no confirmation reverts through the injected fake applier",
      transaction.status == "reverted" and transaction.reverted
      and len(calls) == 2 and calls[0] == transaction.plan
      and calls[1][0].device_name == LAPTOP.device_name
      and (calls[1][0].x, calls[1][0].y) == (0, 0))

calls.clear()
transaction = dp.apply_profile(target, fake_applier, current=current,
                               confirmation=True, timeout=0)
check("confirmation keeps the provisional arrangement",
      transaction.kept and len(calls) == 1)

calls.clear()


def broken_confirmation(_seconds):
    raise RuntimeError("confirmation UI disappeared")


transaction = dp.apply_profile(target, fake_applier, current=current,
                               confirmation=broken_confirmation, timeout=0)
check("a broken confirmation path is treated as silence and reverts",
      transaction.reverted and len(calls) == 2)

path = pathlib.Path(dp.__file__)
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)
parents = {}
for parent in ast.walk(tree):
    for child in ast.iter_child_nodes(parent):
        parents[child] = parent


def enclosing_function(node):
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return None


top_imports = [node for node in tree.body
               if isinstance(node, (ast.Import, ast.ImportFrom))]
check("ctypes imports occur only inside functions",
      all(not (isinstance(node, ast.Import)
               and any(alias.name == "ctypes" for alias in node.names))
          and not (isinstance(node, ast.ImportFrom) and node.module == "ctypes")
          for node in top_imports))

mutating_name = "Change" + "DisplaySettingsExW"
sites = [node for node in ast.walk(tree)
         if isinstance(node, ast.Constant) and node.value == mutating_name]
check("the real display-changing API is confined to its factory",
      len(sites) == 1 and enclosing_function(sites[0]) == "make_real_applier")

top_level_calls = [node for statement in tree.body
                   if not isinstance(statement, (ast.FunctionDef, ast.ClassDef,
                                                  ast.If, ast.Import, ast.ImportFrom))
                   for node in ast.walk(statement) if isinstance(node, ast.Call)]
check("import and construction cannot apply a display change",
      not top_level_calls and "make_real_applier()" not in
      source.split("def apply_profile", 1)[0])

print("\nRESULT: ALL PASS")
