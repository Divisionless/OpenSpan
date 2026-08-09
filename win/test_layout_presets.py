"""Contract checks for named window-layout presets; all desktop state is fake."""

import json
from pathlib import Path
import tempfile

from config_store import ConfigStore
import layout_presets as lp


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


def identity(app="editor.exe", title="Document", sibling=0, window_class="Editor"):
    return lp.WindowIdentity(
        executable_path=rf"c:\apps\{app}", window_class=window_class,
        title=title, sibling_index=sibling)


def monitor(key="panel-a", rect=None):
    return lp.MonitorState(key, rect or lp.PixelRect(0, 0, 1920, 1040))


def live(handle=1, bounds=None, window_identity=None, display=None):
    return lp.LiveWindow(
        handle, window_identity or identity(),
        bounds or lp.PixelRect(120, 80, 900, 700),
        display or monitor())


def intersection_area(left, right):
    width = max(0, min(left.right, right.right) - max(left.left, right.left))
    height = max(0, min(left.bottom, right.bottom) - max(left.top, right.top))
    return width * height


# ---- every LayoutPresetTests geometry case ------------------------------------


work_areas = (
    lp.PixelRect(0, 0, 1920, 1040), lp.PixelRect(0, 0, 2560, 1400),
    lp.PixelRect(-1920, 0, 1920, 1040), lp.PixelRect(0, -1080, 1920, 1032),
    lp.PixelRect(0, 0, 1921, 1041), lp.PixelRect(-7, -3, 1373, 767),
    lp.PixelRect(-1921, -1039, 999, 997), lp.PixelRect(0, 0, 1080, 1873),
)
partitions_are_exact = True
for work in work_areas:
    for preset in lp.BuiltInLayouts.FULL_PARTITIONS:
        zones = lp.resolve_layout(preset, work)
        total = sum(zone.width * zone.height for zone in zones)
        partitions_are_exact &= (
            bool(zones) and total == work.width * work.height
            and all(zone.width >= 0 and zone.height >= 0 for zone in zones)
            and all(zone.left >= work.left and zone.right <= work.right
                    and zone.top >= work.top and zone.bottom <= work.bottom
                    for zone in zones)
            and all(intersection_area(zones[a], zones[b]) == 0
                    for a in range(len(zones)) for b in range(a + 1, len(zones)))
            and min(zone.left for zone in zones) == work.left
            and min(zone.top for zone in zones) == work.top
            and max(zone.right for zone in zones) == work.right
            and max(zone.bottom for zone in zones) == work.bottom)
check("built-in presets partition hostile work areas exactly", partitions_are_exact)

focus_is_centered = True
for work in work_areas:
    zone, = lp.resolve_layout(lp.BuiltInLayouts.find("Focus"), work)
    focus_is_centered &= (
        work.left <= zone.left <= zone.right <= work.right
        and work.top <= zone.top <= zone.bottom <= work.bottom
        and int(work.width * .8) - 1 <= zone.width <= int(work.width * .8) + 1
        and int(work.height * .9) - 1 <= zone.height <= int(work.height * .9) + 1
        and abs((zone.left - work.left) - (work.right - zone.right)) <= 1
        and abs((zone.top - work.top) - (work.bottom - zone.bottom)) <= 1)
check("focus is a single centered slot inside every work area", focus_is_centered)

third_cases = ((1000, (333, 334, 333)), (1921, (640, 641, 640)),
               (1, (0, 1, 0)), (2, (1, 0, 1)))
thirds_are_cumulative = True
for width, expected in third_cases:
    work = lp.PixelRect(0, 0, width, 1000)
    zones = lp.resolve_layout(lp.BuiltInLayouts.find("Thirds"), work)
    thirds_are_cumulative &= (
        tuple(zone.width for zone in zones) == expected
        and sum(zone.width for zone in zones) == width
        and zones[0].left == work.left and zones[0].right == zones[1].left
        and zones[1].right == zones[2].left and zones[2].right == work.right)
check("thirds rounding uses cumulative edges", thirds_are_cumulative)

uneven = lp.ZonePreset("uneven", (
    lp.LayoutSlot(0, 0, 1 / 3, 1),
    lp.LayoutSlot(1 / 3, 0, 1 / 7, 1),
    lp.LayoutSlot(1 / 3 + 1 / 7, 0, 1 - (1 / 3 + 1 / 7), 1),
))
adjacent_edges_match = True
for width in range(997, 1010):
    work = lp.PixelRect(-31, -17, width, 811)
    zones = lp.resolve_layout(uneven, work)
    adjacent_edges_match &= (
        zones[0].right == zones[1].left
        and zones[1].right == zones[2].left
        and zones[0].left == work.left and zones[2].right == work.right
        and sum(zone.width for zone in zones) == width)
check("unrepresentable adjacent fractions share exact edges", adjacent_edges_match)

work = lp.PixelRect(-500, -250, 1601, 903)
oversized = lp.resolve_slot(lp.LayoutSlot(-.5, -2, 4, 9), work)
off_right = lp.resolve_slot(lp.LayoutSlot(1.5, 0, .5, 1), work)
inverted = lp.resolve_slot(lp.LayoutSlot(.5, .5, -.4, -.4), work)
nonsense = lp.resolve_slot(lp.LayoutSlot(float("nan"), float("nan"),
                                         float("nan"), float("nan")), work)
check("out-of-range fractions clamp into the work area",
      oversized == work and off_right.left == work.right and off_right.width == 0
      and inverted.width == inverted.height == 0
      and work.left <= inverted.x <= work.right and work.top <= inverted.y <= work.bottom
      and nonsense == lp.PixelRect(work.x, work.y, 0, 0))

empty = lp.PixelRect(10, 20, 0, 0)
check("degenerate work areas never produce negative rectangles",
      all(zone == empty for zone in
          lp.resolve_layout(lp.BuiltInLayouts.find("Quarters"), empty)))

expected_names = ("Halves", "Thirds", "Main + Side", "Quarters", "Focus")
actual_names = tuple(preset.name for preset in lp.BuiltInLayouts.ALL)
check("all built-ins are present and uniquely named",
      actual_names == expected_names and len(set(actual_names)) == len(actual_names)
      and len(lp.BuiltInLayouts.find("halves").slots) == 2
      and lp.BuiltInLayouts.find("no such preset") is None)

slots = (lp.LayoutSlot(0, 0, .5, 1),
         lp.LayoutSlot(.5, 0, .5, 1, match_process="code.exe"))
windows = (lp.LayoutWindow("Code", "LayoutPreset.cs - EsotericOS"),
           lp.LayoutWindow("firefox", "Docs"))
check("matching slots claim windows before positional fill",
      lp.assign_slots(slots, windows) == [1, 0])

slots = (lp.LayoutSlot(0, 0, .5, 1, "firefox", "release notes"),
         lp.LayoutSlot(.5, 0, .5, 1))
windows = (lp.LayoutWindow("firefox", "Inbox"),
           lp.LayoutWindow("chrome", "Release Notes"),
           lp.LayoutWindow("firefox", "EsotericOS Release Notes"),
           lp.LayoutWindow("explorer", "Downloads"))
assignment = lp.assign_slots(slots, windows)
check("title and process rules combine and leave extras unassigned",
      assignment == [2, 0] and len(windows) - sum(i >= 0 for i in assignment) == 2
      and lp.slot_matches(slots[0], windows[2])
      and not lp.slot_matches(slots[0], windows[0])
      and not lp.slot_matches(slots[0], windows[1])
      and not lp.slot_matches(slots[1], windows[0]))

quarter_slots = lp.BuiltInLayouts.find("Quarters").slots
check("slots beyond the window count remain empty",
      lp.assign_slots(quarter_slots, [lp.LayoutWindow("notepad", "Untitled")])
      == [0, -1, -1, -1]
      and lp.assign_slots(quarter_slots, []) == [-1, -1, -1, -1])


# ---- captured preset contract -------------------------------------------------


first = live()
preset = lp.capture_preset("Work", [first])
moves, unmatched = lp.plan_restore(preset, [first])
check("capture and restore round trip is a no-op plan",
      not moves and not unmatched
      and preset.windows[0].monitor_key == "panel-a"
      and preset.windows[0].monitor_ordinal == 0
      and preset.windows[0].relative_rect == lp.RelativeRect(
          120 / 1920, 80 / 1040, 900 / 1920, 700 / 1040))

twin_left = monitor("twin-panel", lp.PixelRect(0, 0, 1920, 1040))
twin_right = monitor("twin-panel", lp.PixelRect(1920, 0, 1920, 1040))
left_window = live(
    handle=10, bounds=lp.PixelRect(120, 80, 900, 700),
    window_identity=identity(title="Left"), display=twin_left)
right_window = live(
    handle=11, bounds=lp.PixelRect(2040, 80, 900, 700),
    window_identity=identity(app="browser.exe", title="Right", window_class="Browser"),
    display=twin_right)
twin_desktop = lp.LiveDesktop(
    (left_window, right_window), (twin_right, twin_left))
twin_preset = lp.capture_preset("Twins", twin_desktop)
moves, unmatched = lp.plan_restore(twin_preset, twin_desktop)
check("same-key twin monitors capture and restore as a no-op plan",
      not moves and not unmatched
      and tuple(window.monitor_ordinal for window in twin_preset.windows) == (0, 1))

right_only = lp.LayoutPreset("Right twin", (twin_preset.windows[1],))
surviving_candidate = live(
    handle=12, bounds=lp.PixelRect(120, 80, 900, 700),
    window_identity=right_window.identity, display=twin_left)
surviving_desktop = lp.LiveDesktop((surviving_candidate,), (twin_left,))
moves, unmatched = lp.plan_restore(right_only, surviving_desktop)
check("an unplugged twin is unmatched instead of substituted",
      not moves and len(unmatched) == 1
      and unmatched[0].reason == lp.MONITOR_INSTANCE_NOT_FOUND)

small = monitor("panel-a", lp.PixelRect(-1280, 20, 1280, 800))
moved_live = live(bounds=lp.PixelRect(0, 0, 100, 100), display=small)
moves, unmatched = lp.plan_restore(preset, [moved_live])
check("relative rectangles scale across differing monitor sizes",
      not unmatched and len(moves) == 1
      and moves[0].target == lp.PixelRect(-1200, 82, 600, 538))

stranger = live(window_identity=identity(app="browser.exe", window_class="Browser"))
moves, unmatched = lp.plan_restore(preset, [stranger])
check("unmatched windows are reported instead of guessed",
      not moves and len(unmatched) == 1
      and unmatched[0].reason == lp.WINDOW_NOT_FOUND)

other_monitor = monitor("panel-b")
matching_elsewhere = live(display=other_monitor)
desktop = lp.LiveDesktop((matching_elsewhere,), (other_monitor,))
moves, unmatched = lp.plan_restore(preset, desktop)
check("a missing monitor is reported instead of substituted",
      not moves and len(unmatched) == 1
      and unmatched[0].reason == lp.MONITOR_NOT_FOUND)

placed = []
applied, unmatched = lp.restore(preset, [moved_live],
                                lambda handle, target: not placed.append((handle, target)))
check("restore applies a completed plan through the injected mover",
      not unmatched and len(applied) == 1 and applied[0].succeeded
      and placed == [(moved_live.handle, applied[0].move.target)])

with tempfile.TemporaryDirectory() as directory:
    store = ConfigStore(directory)
    legacy_data = lp._preset_to_data(preset)
    legacy_data["windows"][0].pop("monitorOrdinal")
    store.set_feature_setting(lp.FEATURE_ID, lp.PRESETS_SETTING, [legacy_data])
    legacy_loaded = lp.load_presets(ConfigStore(directory))
    check("stored JSON without a monitor ordinal loads it as zero",
          len(legacy_loaded) == 1
          and legacy_loaded[0].windows[0].monitor_ordinal == 0)

with tempfile.TemporaryDirectory() as directory:
    store = ConfigStore(directory)
    lp.save_preset(store, preset)
    reloaded = ConfigStore(directory)
    loaded = lp.load_presets(reloaded)
    document = json.loads(Path(reloaded.config_file).read_text(encoding="utf-8"))
    settings = document["features"][lp.FEATURE_ID]["settings"]
    check("preset persistence round-trips through a temp-rooted ConfigStore",
          loaded == [preset] and lp.PRESETS_SETTING in settings
          and "deviceName" not in json.dumps(settings)
          and "DISPLAY" not in json.dumps(settings))
