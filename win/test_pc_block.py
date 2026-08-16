"""The PC block: derived from Windows, sized by EDID, and never overlapping.

Where the PC's own screens sit relative to each other is settled in Windows
Display Settings. Until pc_block_layout existed the arrangement canvas let them
be dragged apart one at a time, so the app could hold a picture of the desk that
disagreed with Windows about the PC's own layout while both looked perfectly
plausible -- and every portal on those edges was computed from the wrong one.

So the block is DERIVED: each screen is seeded from its Windows offset to the
primary, scaled by the primary's desk-units-per-pixel ratio, then snapped
against the screens already placed. The snap is not decoration. Two panels with
the same pixel count and different physical sizes are different rectangles on
the desk, and the difference is exactly the distance between "touching" and "a
hundred units apart" -- which is the difference between having a portal and not.

Everything here runs on Doug's real desk, DISPLAY5 primary at (0,0), DISPLAY1 at
(4,-1080) and DISPLAY4 at (-1920,0), because two of the three sit at NEGATIVE
origins and a formula verified only on the primary is not verified. The live
config on this machine is read, never written.

Exit 0 = all pass.
"""

import copy
import json
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from openspan_targets import (  # noqa: E402
    BASE_PORT, merge_live_monitors, normalize_config, overlapping_surfaces,
    pc_block_layout, physical_size, rects_overlap,
)


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


def monitor(name, x, y, primary=False, w=1920, h=1080):
    """One Windows monitor, exactly as enum_monitors reports it."""
    return {"name": name, "x": x, "y": y, "w": w, "h": h, "primary": primary,
            "refresh_hz": 60.0}


def rect(row):
    return (row["layout_x"], row["layout_y"], row["layout_w"], row["layout_h"])


def by_name(rows):
    return {row["name"]: row for row in rows}


# Doug's desk. DISPLAY5 and DISPLAY4 are the same 15.7" panel; DISPLAY1 is the
# 17.1" one, so two screens with identical pixel counts are deliberately
# different rectangles -- the case the snap exists for.
DESK = [
    monitor(r"\\.\DISPLAY5", 0, 0, primary=True),
    monitor(r"\\.\DISPLAY1", 4, -1080),
    monitor(r"\\.\DISPLAY4", -1920, 0),
]
EDID = {r"\\.\DISPLAY5": 15.7, r"\\.\DISPLAY1": 17.1, r"\\.\DISPLAY4": 15.7}

SMALL = physical_size(15.7, 1920, 1080, 0)
BIG = physical_size(17.1, 1920, 1080, 0)


def sized(rows, sizes):
    """The rows as they reach pc_block_layout: desk SIZE already derived."""
    out = []
    for row in rows:
        row = dict(row)
        diagonal = sizes[row["name"]]
        row["diagonal_in"] = diagonal
        row["layout_x"], row["layout_y"] = row["x"], row["y"]
        row["layout_w"], row["layout_h"] = physical_size(
            diagonal, row["w"], row["h"], 0)
        out.append(row)
    return out


# === 1. the block reproduces Windows' topology ==============================
source = sized(DESK, EDID)
placed = by_name(pc_block_layout(source))
m5, m1, m4 = (placed[name] for name in
              (r"\\.\DISPLAY5", r"\\.\DISPLAY1", r"\\.\DISPLAY4"))

check("every panel is drawn at the size its diagonal implies, not its pixels",
      (m5["layout_w"], m5["layout_h"]) == SMALL
      and (m4["layout_w"], m4["layout_h"]) == SMALL
      and (m1["layout_w"], m1["layout_h"]) == BIG
      and BIG[0] > SMALL[0])
check("the primary is placed at the anchor, and the anchor defaults to where "
      "it already was",
      (m5["layout_x"], m5["layout_y"]) == (0, 0))
check("DISPLAY4, at a NEGATIVE origin, touches the primary's LEFT edge",
      m4["layout_x"] + m4["layout_w"] == m5["layout_x"])
check("...with their tops level, exactly as Windows has them",
      m4["layout_y"] == m5["layout_y"])
check("DISPLAY1, at a NEGATIVE origin, touches the primary's TOP edge",
      m1["layout_y"] + m1["layout_h"] == m5["layout_y"])
check("...with their left edges aligned -- Windows' 4px offset is noise, and "
      "the two panels are different widths, so only the snap can do this",
      m1["layout_x"] == m5["layout_x"])
check("a bigger panel above a smaller one still lands flush, which mapping "
      "pixel offsets alone could not do",
      m1["layout_w"] != m5["layout_w"]
      and m1["layout_y"] != -m5["layout_h"])
check("no two PC screens overlap",
      not rects_overlap(rect(m5), rect(m1))
      and not rects_overlap(rect(m5), rect(m4))
      and not rects_overlap(rect(m1), rect(m4)))
check("the input rows are not touched -- new dicts come back",
      all(row["layout_x"] == row["x"] and row["layout_y"] == row["y"]
          for row in source)
      and all(placed[row["name"]] is not row for row in source))


# === 2. idempotence =========================================================
again = by_name(pc_block_layout(list(placed.values())))
check("running the layout on its own output reproduces it exactly",
      {name: rect(row) for name, row in again.items()}
      == {name: rect(row) for name, row in placed.items()})
third = by_name(pc_block_layout(list(again.values()), anchor=(0, 0)))
check("...and passing the anchor it already used changes nothing either",
      {name: rect(row) for name, row in third.items()}
      == {name: rect(row) for name, row in placed.items()})

moved = by_name(pc_block_layout(source, anchor=(-4000, 250)))
check("the anchor moves the whole block and nothing else",
      all((moved[name]["layout_x"] - placed[name]["layout_x"],
           moved[name]["layout_y"] - placed[name]["layout_y"])
          == (-4000, 250) for name in placed))


# === 3. a fourth screen Windows reports with a real gap =====================
# Windows' own settings page will not leave a gap, but the enumerator is not
# the settings page: whatever it reports has to come out as a legible desk. A
# gap is seeded through the same ratio as everything else and simply survives,
# because nothing is within snapping distance of anything.
GAPPED = DESK + [monitor(r"\\.\DISPLAY7", 2400, 600)]
GAP_EDID = dict(EDID, **{r"\\.\DISPLAY7": 24.0})
four = by_name(pc_block_layout(sized(GAPPED, GAP_EDID)))
seven = four[r"\\.\DISPLAY7"]
ratio = four[r"\\.\DISPLAY5"]["layout_w"] / 1920.0

check("the other three still land exactly where they did without it",
      {name: rect(four[name]) for name in placed}
      == {name: rect(placed[name]) for name in placed})
check("the fourth screen keeps its 480px Windows gap, scaled to the desk",
      abs((seven["layout_x"]
           - (four[r"\\.\DISPLAY5"]["layout_x"]
              + four[r"\\.\DISPLAY5"]["layout_w"])) - 480 * ratio) <= 2)
check("a gap is never closed by force -- it is still clear of every screen",
      not any(rects_overlap(rect(seven), rect(four[name])) for name in placed))
check("and it is sized by its own diagonal, not by the primary's ratio",
      (seven["layout_w"], seven["layout_h"])
      == physical_size(24.0, 1920, 1080, 0))


# === 4. push-out: two screens can share one pixel rectangle =================
# Duplicating a display gives two monitors the SAME Windows rectangle. The
# desk cannot draw one on top of the other, so the second is pushed clear along
# the axis it has penetrated least.
CLONE = [
    monitor(r"\\.\DISPLAY5", 0, 0, primary=True),
    monitor(r"\\.\DISPLAY6", 0, 0),
]
cloned = by_name(pc_block_layout(
    sized(CLONE, {r"\\.\DISPLAY5": 15.7, r"\\.\DISPLAY6": 24.0})))
host, twin = cloned[r"\\.\DISPLAY5"], cloned[r"\\.\DISPLAY6"]
check("a duplicated display is pushed off the screen it seeded on top of",
      not rects_overlap(rect(host), rect(twin)))
check("...the short way out: it leaves along the axis of least penetration, "
      "still touching",
      twin["layout_y"] == host["layout_y"] + host["layout_h"]
      and twin["layout_x"] == host["layout_x"])
check("the push is deterministic",
      rect(by_name(pc_block_layout(
          sized(CLONE, {r"\\.\DISPLAY5": 15.7,
                        r"\\.\DISPLAY6": 24.0})))[r"\\.\DISPLAY6"])
      == rect(twin))


# === 5. one screen, and none ================================================
solo = pc_block_layout(sized([monitor(r"\\.\DISPLAY5", -1920, -1080,
                                      primary=True)], {r"\\.\DISPLAY5": 15.7}))
check("a single monitor is simply anchored where it already is",
      len(solo) == 1
      and (solo[0]["layout_x"], solo[0]["layout_y"]) == (-1920, -1080))
check("...and an explicit anchor moves it there",
      (lambda row: (row["layout_x"], row["layout_y"]))(
          pc_block_layout(solo, anchor=(120, -40))[0]) == (120, -40))
check("no monitors at all is an empty list, not an exception",
      pc_block_layout([]) == [] and pc_block_layout(None) == [])


# === 6. rects_overlap: touching is not overlapping ==========================
check("two rectangles that cover each other overlap",
      rects_overlap((0, 0, 100, 100), (50, 50, 100, 100)))
check("two rectangles that are nowhere near each other do not",
      not rects_overlap((0, 0, 100, 100), (400, 400, 100, 100)))
check("a SHARED EDGE is not an overlap -- every portal in this app is a "
      "shared edge, so a flush drop has to be legal",
      not rects_overlap((0, 0, 100, 100), (100, 0, 100, 100))
      and not rects_overlap((0, 0, 100, 100), (0, 100, 100, 100)))
check("...and neither is a shared corner",
      not rects_overlap((0, 0, 100, 100), (100, 100, 100, 100)))
check("overlapping on ONE axis only is not an overlap",
      not rects_overlap((0, 0, 100, 100), (50, 400, 100, 100))
      and not rects_overlap((0, 0, 100, 100), (400, 50, 100, 100)))
check("the tolerance is the width of that shared edge",
      not rects_overlap((0, 0, 100, 100), (98, 0, 100, 100))
      and rects_overlap((0, 0, 100, 100), (97, 0, 100, 100)))
check("a wider tolerance forgives a wider trespass",
      not rects_overlap((0, 0, 100, 100), (90, 0, 100, 100), tolerance=10)
      and rects_overlap((0, 0, 100, 100), (90, 0, 100, 100), tolerance=9))
check("negative coordinates are not a special case",
      rects_overlap((-1920, -1080, 1920, 1080), (-1000, -600, 400, 400))
      and not rects_overlap((-1920, -1080, 1920, 1080), (0, -1080, 100, 100)))


# === 7. overlapping_surfaces: what a drop would land on =====================
CONFIG = normalize_config({
    "links": [],
    "monitors": [dict(DESK[0], layout_x=0, layout_y=0,
                      layout_w=SMALL[0], layout_h=SMALL[1],
                      diagonal_in=15.7)],
    "devices": [{
        "id": "ipad", "name": "iPad", "port": BASE_PORT, "enabled": True,
        "displays": [{"id": "ipad-main", "name": "iPad",
                      "x": SMALL[0], "y": 0, "w": 816, "h": 612,
                      "res_w": 1080, "res_h": 810, "rotation": 0}]},
        {
        "id": "mac", "name": "Managed Mac", "port": BASE_PORT + 1,
        "enabled": True,
        "displays": [{"id": "mac-1", "name": "Mac Display 1",
                      "x": -2800, "y": 0, "w": 1400, "h": 800,
                      "res_w": 2560, "res_h": 1440, "rotation": 0}]},
    ],
}, [DESK[0]])
IPAD_KEY = ("target", "ipad", "ipad-main")
MONITOR_KEY = ("local", "windows", r"\\.\DISPLAY5")

check("dropping a screen squarely on the PC names the PC screen",
      overlapping_surfaces(CONFIG, IPAD_KEY, (100, 100, 816, 612))
      == [r"\\.\DISPLAY5"])
check("a surface never reports overlapping ITSELF",
      overlapping_surfaces(CONFIG, IPAD_KEY, (SMALL[0], 0, 816, 612)) == []
      and overlapping_surfaces(CONFIG, MONITOR_KEY, (0, 0, *SMALL)) == [])
check("parking flush against the PC's right edge is legal",
      overlapping_surfaces(CONFIG, IPAD_KEY, (SMALL[0], 0, 816, 612)) == [])
check("one drop can land on two surfaces at once, and both are named",
      overlapping_surfaces(CONFIG, MONITOR_KEY, (-2000, 0, 3500, 700))
      == ["iPad · iPad", "Managed Mac · Mac Display 1"])
check("a key that names nothing compares against every surface",
      overlapping_surfaces(CONFIG, None, (100, 100, 200, 200))
      == [r"\\.\DISPLAY5"]
      and overlapping_surfaces(CONFIG, ("target", "gone", "gone"),
                               (100, 100, 200, 200)) == [r"\\.\DISPLAY5"])
check("a disabled device is not on the desk and cannot be landed on",
      overlapping_surfaces(
          dict(CONFIG, devices=[dict(row, enabled=False)
                                for row in CONFIG["devices"]]),
          MONITOR_KEY, (-2000, 0, 3000, 700)) == [])


# === 8. EDID precedence: user beats edid beats legacy beats nothing =========
def loaded(saved_diagonal=None, source=None, sizes=None):
    saved = dict(DESK[0])
    if saved_diagonal is not None:
        saved["diagonal_in"] = saved_diagonal
    if source is not None:
        saved["diagonal_source"] = source
    return normalize_config(
        {"links": [], "monitors": [saved]}, [DESK[0]],
        sizes=sizes)["monitors"][0]


typed = loaded(17.0, "user", {r"\\.\DISPLAY5": 15.7})
check("a diagonal the user TYPED outranks the panel's own EDID",
      typed["diagonal_in"] == 17.0 and typed["diagonal_source"] == "user"
      and (typed["layout_w"], typed["layout_h"])
      == physical_size(17.0, 1920, 1080, 0))
edid = loaded(17.0, None, {r"\\.\DISPLAY5": 15.7})
check("EDID outranks a legacy value with nobody's name on it",
      edid["diagonal_in"] == 15.7 and edid["diagonal_source"] == "edid"
      and (edid["layout_w"], edid["layout_h"]) == SMALL)
legacy = loaded(17.0, None, None)
check("with no EDID reading the legacy value stands, and stays unattributed",
      legacy["diagonal_in"] == 17.0 and "diagonal_source" not in legacy
      and (legacy["layout_w"], legacy["layout_h"])
      == physical_size(17.0, 1920, 1080, 0))
fresh = loaded(None, None, {r"\\.\DISPLAY5": 15.7})
check("a screen nobody ever measured takes EDID's answer",
      fresh["diagonal_in"] == 15.7 and fresh["diagonal_source"] == "edid")
blind = loaded(None, None, None)
check("and with no source at all it is not given a size it does not have",
      "diagonal_in" not in blind
      and (blind["layout_w"], blind["layout_h"]) == (1920, 1080))
check("an EDID reading for some OTHER monitor is not applied to this one",
      loaded(None, None, {r"\\.\DISPLAY9": 32.0}).get("diagonal_in") is None)
check("sizes=None and sizes={} are the same as not passing it at all",
      loaded(17.0, None, {}) == legacy
      and normalize_config({"links": [], "monitors": [DESK[0]]}, [DESK[0]])
      == normalize_config({"links": [], "monitors": [DESK[0]]}, [DESK[0]],
                          sizes={}))


# === 9. the merge says when a screen changed SIZE ===========================
SAVED = [dict(DESK[0], layout_x=-59, layout_y=0, layout_w=1482,
              layout_h=833, diagonal_in=17.0),
         dict(DESK[1], layout_x=-59, layout_y=-833, layout_w=1482,
              layout_h=833, diagonal_in=17.0, diagonal_source="user")]
merged, report = merge_live_monitors(SAVED, DESK[:2], sizes=EDID)
check("a screen resized by its own EDID is REPORTED, never redrawn silently",
      report["resized"] == [(r"\\.\DISPLAY5", 17.0, 15.7)])
check("...and the typed one is left alone, so it is not reported either",
      by_name(merged)[r"\\.\DISPLAY1"]["diagonal_in"] == 17.0)
check("a monitor that had no size at all and now has one is reported too",
      merge_live_monitors(
          [dict(DESK[0], layout_x=0, layout_y=0)], [DESK[0]],
          sizes=EDID)[1]["resized"] == [(r"\\.\DISPLAY5", None, 15.7)])
check("a re-read with no EDID readings changes no size and reports none",
      merge_live_monitors(SAVED, DESK[:2])[1]["resized"] == []
      and merge_live_monitors(SAVED, DESK[:2])[1]["removed"] == [])
check("measuring the same panel twice is not a size change",
      merge_live_monitors(merged, DESK[:2], sizes=EDID)[1]["resized"] == [])
check("a refresh re-derives the PC block from Windows",
      (lambda rows: rows[r"\\.\DISPLAY1"]["layout_y"]
       + rows[r"\\.\DISPLAY1"]["layout_h"]
       == rows[r"\\.\DISPLAY5"]["layout_y"])(by_name(merged)))
check("the block as a whole stays where the user had put it",
      by_name(merged)[r"\\.\DISPLAY5"]["layout_x"] == -59)

# Windows can move the primary flag onto a panel that was only just plugged in.
# The block must not teleport to that panel's raw pixel origin over a cable
# change, so it stays pinned by a screen that already had a place.
promoted, _report = merge_live_monitors(
    [dict(DESK[1], layout_x=-59, layout_y=-833, layout_w=1482, layout_h=833,
          diagonal_in=17.0)],
    [dict(DESK[1], primary=False), dict(DESK[0], primary=True)])
check("a newly primary screen does not drag the whole PC across the desk -- "
      "the screen that already had a place keeps it, to within the 4px of "
      "Windows noise the snap levels out",
      abs(by_name(promoted)[r"\\.\DISPLAY1"]["layout_x"] + 59) <= 5
      and by_name(promoted)[r"\\.\DISPLAY1"]["layout_y"] == -833)


# === 10. the desk this is running on ========================================
# The rule the whole feature has to survive: Doug's saved layout already agrees
# with Windows, so deriving it from Windows must not move anything. If this
# fails, the next launch on this machine silently rearranges his screens.
CONFIG_PATH = pathlib.Path(__file__).parents[1] / "openspan_config.json"
live_raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
live_monitors = [row for row in live_raw.get("monitors", [])
                 if isinstance(row, dict)]
saved_positions = {row["name"]: (row["layout_x"], row["layout_y"])
                   for row in live_monitors}
check("the live config on this machine really does hold both PC screens",
      len(live_monitors) >= 2
      and {r"\\.\DISPLAY5", r"\\.\DISPLAY1"} <= set(saved_positions))

derived = {row["name"]: (row["layout_x"], row["layout_y"])
           for row in pc_block_layout(copy.deepcopy(live_monitors))}
drift = {name: (abs(derived[name][0] - saved[0]),
                abs(derived[name][1] - saved[1]))
         for name, saved in saved_positions.items()}
check("deriving the block from Windows leaves every saved position where it "
      f"was, within snap tolerance: {drift}",
      all(dx < 5 and dy < 5 for dx, dy in drift.values()))

reloaded = normalize_config(copy.deepcopy(live_raw),
                            copy.deepcopy(live_monitors))
through_load = {row["name"]: (row["layout_x"], row["layout_y"])
                for row in reloaded["monitors"]}
check("...and so does a whole load of that config, which is what actually "
      f"runs at launch: {through_load}",
      all(abs(through_load[name][0] - saved[0]) < 5
          and abs(through_load[name][1] - saved[1]) < 5
          for name, saved in saved_positions.items()))
check("the live config was only ever read",
      json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["monitors"]
      == live_raw["monitors"])

print("RESULT: ALL PASS")
