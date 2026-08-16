"""The PC block: derived from Windows, sized by EDID, and never overlapping.

Where the PC's own screens sit relative to each other is settled in Windows
Display Settings. Until pc_block_layout existed the arrangement canvas let them
be dragged apart one at a time, so the app could hold a picture of the desk that
disagreed with Windows about the PC's own layout while both looked perfectly
plausible -- and every portal on those edges was computed from the wrong one.

So the block is DERIVED, and each screen is placed off the screen it TOUCHES:
the shared edge is mapped exactly and only the offset along it is scaled. Two
panels with the same pixel count and different physical sizes are different
rectangles on the desk, and the difference is exactly the distance between
"touching" and "a hundred units apart" -- which is the difference between
having a portal and not. Scaling every screen by the PRIMARY's ratio, which is
what this used to do, is the same fault one step further out: it held only
while every panel had the same pixel density, and the fuzz in section 12 is
what says so.

Two authorities meet on this desk and neither has heard of the other. Windows
arranges the PC's screens; the user arranges the devices. So the block also has
to be told where the devices are, or it is derived straight through an iPad --
section 10.

Everything here runs on Doug's real desk, DISPLAY5 primary at (0,0), DISPLAY1 at
(4,-1080) and DISPLAY4 at (-1920,0), because two of the three sit at NEGATIVE
origins and a formula verified only on the primary is not verified. The last
section asks the question of the REAL inputs -- the monitors Windows reports
now and the sizes the panels state in their EDID -- because those are what the
app is handed at launch. The live config on this machine is read, never
written.

Exit 0 = all pass.
"""

import copy
import json
import pathlib
import random
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from openspan_targets import (  # noqa: E402
    BASE_PORT, _push_out, last_normalize_report, layout_surfaces,
    merge_live_monitors, normalize_config, overlapping_surfaces,
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


# === 10. the block never lands on a device ==================================
# Windows arranges the PC's screens; the USER arranges the devices; neither
# authority has heard of the other. So a block derived purely from Windows can
# be derived straight through an iPad, and it was: loading the saved "Mac 2k 2"
# arrangement -- written when DISPLAY4 was a 32" primary, replayed on a desk
# where DISPLAY5 is primary and DISPLAY4 is a 15.7" panel -- put DISPLAY4 at
# (-96, 1280) on top of the Mac's third display at (-210, 1569) and left the
# desk with one portal where it had six.
#
# The fix is not to move the offending screen. Moving one screen would make the
# block disagree with Windows, which is the fault the block exists to prevent.
# The whole block steps clear, as one thing.
MAC_2K_2 = {
    # The shape of profiles/Mac 2k 2.json, copied here rather than read: that
    # file is the user's and can be re-saved at any moment, and a test that
    # changes meaning when he re-saves an arrangement is not a test.
    "links": [],
    "monitors": [
        {"name": r"\\.\DISPLAY5", "x": 2560, "y": 1440, "w": 1920, "h": 1080,
         "primary": False, "refresh_hz": 60.0, "layout_x": 1272,
         "layout_y": 1280, "layout_w": 1482, "layout_h": 833,
         "diagonal_in": 17.0},
        {"name": r"\\.\DISPLAY1", "x": 2560, "y": 360, "w": 1920, "h": 1080,
         "primary": False, "refresh_hz": 60.0, "layout_x": 1272,
         "layout_y": 447, "layout_w": 1482, "layout_h": 833,
         "diagonal_in": 17.0},
        {"name": r"\\.\DISPLAY4", "x": 0, "y": 0, "w": 2560, "h": 1440,
         "primary": True, "refresh_hz": 120.0, "layout_x": -1517,
         "layout_y": 0, "layout_w": 2789, "layout_h": 1569,
         "diagonal_in": 32.0},
    ],
    "devices": [
        {"id": "ipad", "name": "iPad", "port": BASE_PORT, "enabled": True,
         "displays": [{"id": "ipad-main", "name": "iPad", "x": -1026,
                       "y": 1569, "w": 816, "h": 612, "res_w": 1080,
                       "res_h": 810, "rotation": 0, "diagonal_in": 10.2}]},
        {"id": "mac", "name": "Managed Mac", "port": BASE_PORT + 1,
         "enabled": True,
         "displays": [
             {"id": "mac-1", "name": "Mac Display 1", "x": -4655, "y": -610,
              "w": 1569, "h": 2789, "res_w": 3840, "res_h": 2160,
              "rotation": 90, "diagonal_in": 32.0},
             {"id": "mac-2", "name": "Mac Display 2", "x": -3086, "y": -610,
              "w": 1569, "h": 2789, "res_w": 3840, "res_h": 2160,
              "rotation": 90, "diagonal_in": 32.0},
             {"id": "mac-3", "name": "Mac Display 3", "x": -210, "y": 1569,
              "w": 1482, "h": 833, "res_w": 2560, "res_h": 1440,
              "rotation": 0, "diagonal_in": 17.0}]},
        {"id": "laptop", "name": "Managed Laptop", "port": BASE_PORT + 2,
         "enabled": True,
         "displays": [
             {"id": "laptop-1", "name": "MacAir-E", "x": -1517, "y": -637,
              "w": 1133, "h": 637, "res_w": 1920, "res_h": 1080,
              "rotation": 0, "diagonal_in": 13.0},
             {"id": "laptop-2", "name": "MacAir", "x": -384, "y": -637,
              "w": 1133, "h": 637, "res_w": 3840, "res_h": 2160,
              "rotation": 0, "diagonal_in": 13.0}]},
    ],
}


def pc_rects(config):
    return {row["name"]: rect(row) for row in config["monitors"]}


def device_screens(config):
    """Every device screen on the desk, as (name, rect)."""
    return [(f"{device['name']} · {display['name']}",
             (display["x"], display["y"], display["w"], display["h"]))
            for device in config["devices"] if device.get("enabled", True)
            for display in device["displays"]]


def collisions(config, screens=None):
    """Every (PC screen, device screen) pair that covers the other.

    `screens` names which devices to measure against, because the probe below
    switches them off to see where the block WOULD have been derived -- and a
    switched-off device is not on the desk, so its own config can no longer be
    asked where it was."""
    screens = device_screens(config) if screens is None else screens
    return [(name, label) for name, mine in pc_rects(config).items()
            for label, theirs in screens if rects_overlap(mine, theirs)]


landed = normalize_config(copy.deepcopy(MAC_2K_2), DESK, sizes=EDID)
# The same config with every device switched off: nothing is then on the desk
# to avoid, so this is the block as it would have been derived before devices
# were considered at all -- the picture the defect was reported against.
free = normalize_config(
    dict(copy.deepcopy(MAC_2K_2),
         devices=[dict(device, enabled=False)
                  for device in copy.deepcopy(MAC_2K_2)["devices"]]),
    DESK, sizes=EDID)

check("the arrangement being loaded really is the awkward one: it was saved "
      "with a different screen as primary",
      not by_name(MAC_2K_2["monitors"])[r"\\.\DISPLAY5"]["primary"]
      and by_name(DESK)[r"\\.\DISPLAY5"]["primary"])
check("with the devices ignored the block really does land on one -- this is "
      f"the reported defect: {collisions(free, device_screens(landed))}",
      collisions(free, device_screens(landed)) != [])
check("with the devices on the desk, no PC screen covers any device screen: "
      f"{collisions(landed)}",
      collisions(landed) == [])
deltas = {name: (landed_rect[0] - free_rect[0], landed_rect[1] - free_rect[1])
          for (name, landed_rect), free_rect
          in zip(pc_rects(landed).items(), pc_rects(free).values())}
check("the whole block moved, by ONE delta -- no screen was moved out of the "
      f"formation Windows put it in: {deltas}",
      len(set(deltas.values())) == 1 and set(deltas.values()) != {(0, 0)})
moved = by_name(landed["monitors"])
check("...so DISPLAY4 still touches the primary's left edge afterwards",
      moved[r"\\.\DISPLAY4"]["layout_x"] + moved[r"\\.\DISPLAY4"]["layout_w"]
      == moved[r"\\.\DISPLAY5"]["layout_x"]
      and moved[r"\\.\DISPLAY4"]["layout_y"]
      == moved[r"\\.\DISPLAY5"]["layout_y"])
check("...and DISPLAY1 still touches its top",
      moved[r"\\.\DISPLAY1"]["layout_y"] + moved[r"\\.\DISPLAY1"]["layout_h"]
      == moved[r"\\.\DISPLAY5"]["layout_y"])
check("the escape is deterministic -- the same desk twice is the same desk",
      pc_rects(normalize_config(copy.deepcopy(MAC_2K_2), DESK, sizes=EDID))
      == pc_rects(landed))

# The second way in, and the one that needs no saved arrangement at all: a
# screen Windows has only just reported has no place on the desk yet, so it is
# derived from Windows -- and Windows has never heard of the iPad parked
# against the primary's left edge.
IPAD_LEFT = {
    "links": [],
    # Only the two screens that were here before. DISPLAY4 is the new arrival.
    "monitors": [
        dict(DESK[0], layout_x=-59, layout_y=0, layout_w=SMALL[0],
             layout_h=SMALL[1], diagonal_in=15.7),
        dict(DESK[1], layout_x=-59, layout_y=-838, layout_w=BIG[0],
             layout_h=BIG[1], diagonal_in=17.1),
    ],
    "devices": [{
        "id": "ipad", "name": "iPad", "port": BASE_PORT, "enabled": True,
        # Flush against the primary's LEFT edge, at a negative origin, which
        # is exactly where DISPLAY4 is about to be derived to.
        "displays": [{"id": "ipad-main", "name": "iPad", "x": -59 - 816,
                      "y": 0, "w": 816, "h": 612, "res_w": 1080,
                      "res_h": 810, "rotation": 0}]}],
}
arrived = normalize_config(copy.deepcopy(IPAD_LEFT), DESK, sizes=EDID)
arrived_free = normalize_config(
    dict(copy.deepcopy(IPAD_LEFT),
         devices=[dict(IPAD_LEFT["devices"][0], enabled=False)]),
    DESK, sizes=EDID)
check("a screen that was not there before is derived onto the iPad when the "
      f"iPad is not looked at: "
      f"{collisions(arrived_free, device_screens(arrived))}",
      collisions(arrived_free, device_screens(arrived)) != [])
check(f"looking at it, the block steps clear of the iPad instead: "
      f"{collisions(arrived)}",
      collisions(arrived) == [])
arrived_deltas = {
    name: (landed_rect[0] - free_rect[0], landed_rect[1] - free_rect[1])
    for (name, landed_rect), free_rect
    in zip(pc_rects(arrived).items(), pc_rects(arrived_free).values())}
check(f"and again it moves as one block: {arrived_deltas}",
      len(set(arrived_deltas.values())) == 1
      and set(arrived_deltas.values()) != {(0, 0)})
check("the new screen is still where Windows has it: flush left of the "
      "primary, tops level",
      (lambda rows: rows[r"\\.\DISPLAY4"]["layout_x"]
       + rows[r"\\.\DISPLAY4"]["layout_w"] == rows[r"\\.\DISPLAY5"]["layout_x"]
       and rows[r"\\.\DISPLAY4"]["layout_y"]
       == rows[r"\\.\DISPLAY5"]["layout_y"])(by_name(arrived["monitors"])))

# The other half of the rule, and the one that protects everybody who has no
# collision: a block with nothing in its way must not move at all.
CLEAR_DESK = pc_block_layout(sized(DESK, EDID))
check("a block that is already clear of every device does not move a unit",
      [rect(row) for row in pc_block_layout(
          sized(DESK, EDID),
          obstacles=[(-4000, -4000, 500, 500), (5000, 5000, 500, 500)])]
      == [rect(row) for row in CLEAR_DESK])
check("no obstacles at all is the same as obstacles nothing reaches",
      [rect(row) for row in pc_block_layout(sized(DESK, EDID), obstacles=[])]
      == [rect(row) for row in CLEAR_DESK])


# === 11. _push_out never hands back a position it knows is bad ==============
# Two obstacles can hand a rectangle back and forth: each push lands it on the
# other, the passes run out, and the last position tried was returned as if it
# were a result. The caller then appended a KNOWN-BAD rectangle to the list
# every later screen is measured against.
PING_PONG = [(0, 0, 2745, 1544), (1372, 2316, 2035, 1272),
             (3407, 1544, 2745, 1544), (3660, -257, 1368, 770)]
STUCK = (3660, 513, 2353, 1324)
out = _push_out(STUCK, PING_PONG)
check(f"the rectangle that used to come back still overlapping does not: {out}",
      not any(rects_overlap((out[0], out[1], STUCK[2], STUCK[3]), other)
              for other in PING_PONG))
check("...and it is deterministic",
      _push_out(STUCK, PING_PONG) == out)
BOXED = [(-600, -200, 400, 400), (200, -200, 400, 400),
         (-200, -600, 400, 400), (-200, 200, 400, 400),
         (-200, -200, 400, 400)]
boxed_out = _push_out((-200, -200, 400, 400), BOXED)
check(f"a rectangle boxed in on all four sides still comes back clear, and at "
      f"a negative origin: {boxed_out}",
      not any(rects_overlap((boxed_out[0], boxed_out[1], 400, 400), other)
              for other in BOXED))
check("an escape direction sends it that way: flush past everything on x",
      _push_out(STUCK, PING_PONG, escape=("x", 1))
      == (max(x + w for x, _y, w, _h in PING_PONG), 513))
check("...and the other way, to a NEGATIVE origin",
      _push_out(STUCK, PING_PONG, escape=("x", -1))
      == (min(x for x, _y, _w, _h in PING_PONG) - STUCK[2], 513))
check("a rectangle that lands on nothing is not moved at all",
      _push_out((-9000, -9000, 100, 100), PING_PONG) == (-9000, -9000))
check("nothing placed at all is not a special case either",
      _push_out((-50, -60, 100, 100), []) == (-50, -60))


# === 12. the fuzz: Windows-legal desks, 400 of each size ====================
# The block used to be seeded from the PRIMARY's desk-per-pixel ratio for every
# screen, which is only correct while every panel has the same pixel density.
# Fuzzing Windows-legal arrangements against that rule dropped an adjacency
# Windows had on 24.5% of three-screen desks and 44.5% of four-screen ones, and
# drew one screen on top of another on 6.5% of five-screen ones.
#
# Each screen is now placed off the screen it TOUCHES, so what is checked here
# is the promise that makes: Windows' own adjacencies survive onto the desk.
# The named case first -- five real panels, and the one that used to end with a
# 14" laptop screen sitting exactly on the primary at (0, 0).
FIVE_PANEL = [
    (r"\\.\SCREEN0", 0, 0, 1920, 1080, 17.1, True),
    (r"\\.\SCREEN1", -1920, -453, 1920, 1080, 17.1, False),
    (r"\\.\SCREEN2", -3840, -700, 1920, 1200, 24.0, False),
    (r"\\.\SCREEN3", 435, -2160, 3840, 2160, 31.5, False),
    (r"\\.\SCREEN4", 4275, -1193, 1366, 768, 14.0, False),
]
five = by_name(pc_block_layout(sized(
    [monitor(name, x, y, primary=primary, w=w, h=h)
     for name, x, y, w, h, _diagonal, primary in FIVE_PANEL],
    {name: diagonal for name, _x, _y, _w, _h, diagonal, _p in FIVE_PANEL})))
check("the 14\" panel four screens out no longer lands on the primary",
      (five[r"\\.\SCREEN4"]["layout_x"], five[r"\\.\SCREEN4"]["layout_y"])
      != (five[r"\\.\SCREEN0"]["layout_x"],
          five[r"\\.\SCREEN0"]["layout_y"]))
check("...and no two of the five cover each other at all",
      not any(rects_overlap(rect(five[one]), rect(five[two]))
              for index, one in enumerate(five)
              for two in list(five)[index + 1:]))
check("...while the three screens at NEGATIVE origins keep the edges Windows "
      "gave them",
      five[r"\\.\SCREEN1"]["layout_x"] + five[r"\\.\SCREEN1"]["layout_w"]
      == five[r"\\.\SCREEN0"]["layout_x"]
      and five[r"\\.\SCREEN2"]["layout_x"] + five[r"\\.\SCREEN2"]["layout_w"]
      == five[r"\\.\SCREEN1"]["layout_x"]
      and five[r"\\.\SCREEN3"]["layout_y"] + five[r"\\.\SCREEN3"]["layout_h"]
      == five[r"\\.\SCREEN0"]["layout_y"])
FUZZ_SEED = 20260815
FUZZ_ARRANGEMENTS = 400
FUZZ_PANELS = (15.7, 17.1, 24.0, 27.0, 31.5, 14.0)
FUZZ_MODES = ((1920, 1080), (1920, 1200), (2560, 1440), (3840, 2160),
              (1366, 768))
# Desks of four and five screens can be arranged in Windows in ways no real
# desk can hold: a 24" panel drawn beside a 17" one protrudes past both its
# neighbour's edges, into space its pixels never occupied, and a third screen
# seeded into that space has to give way. Measured today, that costs 7 of 1201
# touching pairs at four screens and 24 of 1605 at five, and at five screens it
# strands a screen with nothing to touch on 3 desks in 400. Both budgets are
# regression guards on those numbers rather than permission; the two things
# with no budget at all are screens drawn on top of each other and a layout
# that will not reproduce itself.
FUZZ_LOST_PAIR_BUDGET = 0.02
FUZZ_STRANDED_BUDGET = 0.01


def px_covers(first, second):
    return (min(first["x"] + first["w"], second["x"] + second["w"])
            - max(first["x"], second["x"]) > 0
            and min(first["y"] + first["h"], second["y"] + second["h"])
            - max(first["y"], second["y"]) > 0)


def px_touches(first, second):
    """Do two monitors share an edge in Windows, with real overlap along it?"""
    if (min(first["y"] + first["h"], second["y"] + second["h"])
            - max(first["y"], second["y"]) > 0):
        if (first["x"] + first["w"] == second["x"]
                or second["x"] + second["w"] == first["x"]):
            return True
    if (min(first["x"] + first["w"], second["x"] + second["w"])
            - max(first["x"], second["x"]) > 0):
        if (first["y"] + first["h"] == second["y"]
                or second["y"] + second["h"] == first["y"]):
            return True
    return False


def desk_touches(first, second, tolerance=3):
    """The same question on the desk. An edge to within `tolerance` counts --
    that is the band compute_portals matches on -- and the perpendicular spans
    have to genuinely overlap, or the two only meet at a corner."""
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    if min(ay + ah, by + bh) - max(ay, by) > 0:
        if abs((ax + aw) - bx) <= tolerance or abs((bx + bw) - ax) <= tolerance:
            return True
    if min(ax + aw, bx + bw) - max(ax, bx) > 0:
        if abs((ay + ah) - by) <= tolerance or abs((by + bh) - ay) <= tolerance:
            return True
    return False


def windows_desk(rng, count):
    """`count` monitors arranged the way WINDOWS allows: every screen flush
    against one already placed, no gaps, no overlaps, primary at the origin --
    so most screens sit at a negative origin on one axis or both."""
    rows = []
    for index in range(count):
        mode_w, mode_h = rng.choice(FUZZ_MODES)
        row = {"name": f"\\\\.\\FUZZ{index}", "w": mode_w, "h": mode_h,
               "primary": index == 0, "diagonal_in": rng.choice(FUZZ_PANELS)}
        if index == 0:
            row["x"], row["y"] = 0, 0
            rows.append(row)
            continue
        for _attempt in range(200):
            host = rng.choice(rows)
            side = rng.choice(("right", "left", "below", "above"))
            if side in ("right", "left"):
                row["x"] = (host["x"] + host["w"] if side == "right"
                            else host["x"] - row["w"])
                row["y"] = rng.randint(host["y"] - row["h"] + 1,
                                       host["y"] + host["h"] - 1)
            else:
                row["y"] = (host["y"] + host["h"] if side == "below"
                            else host["y"] - row["h"])
                row["x"] = rng.randint(host["x"] - row["w"] + 1,
                                       host["x"] + host["w"] - 1)
            if not any(px_covers(row, other) for other in rows):
                rows.append(row)
                break
        else:
            return None
    return rows


fuzz_negative = 0
for screens in (2, 3, 4, 5):
    rng = random.Random(FUZZ_SEED)
    desks = overlapped = adrift = lost = pairs = restless = 0
    while desks < FUZZ_ARRANGEMENTS:
        rows = windows_desk(rng, screens)
        if rows is None:
            continue
        desks += 1
        fuzz_negative += any(row["x"] < 0 or row["y"] < 0 for row in rows)
        placed_rows = pc_block_layout(sized(
            rows, {row["name"]: row["diagonal_in"] for row in rows}))
        desk = {row["name"]: rect(row) for row in placed_rows}
        if {row["name"]: rect(row)
                for row in pc_block_layout(placed_rows)} != desk:
            restless += 1
        touching = {name: False for name in desk}
        for index, first in enumerate(rows):
            for second in rows[index + 1:]:
                one, two = desk[first["name"]], desk[second["name"]]
                if rects_overlap(one, two):
                    overlapped += 1
                if desk_touches(one, two):
                    touching[first["name"]] = True
                    touching[second["name"]] = True
                if px_touches(first, second):
                    pairs += 1
                    lost += not desk_touches(one, two)
        adrift += not all(touching.values())
    check(f"{screens} screens: not one of {desks} desks has two PC screens "
          f"covering each other ({overlapped} found)", overlapped == 0)
    if screens <= 4:
        check(f"{screens} screens: no screen is left floating free of the "
              f"block ({adrift} desks)", adrift == 0)
    else:
        check(f"{screens} screens: {desks - adrift} of {desks} desks come out "
              f"whole ({adrift} stranded a screen where the arrangement will "
              f"not fit, budget {100.0 * FUZZ_STRANDED_BUDGET:.0f}%)",
              adrift <= FUZZ_STRANDED_BUDGET * desks)
    check(f"{screens} screens: laying out the block again reproduces it "
          f"({restless} desks moved)", restless == 0)
    if screens <= 3:
        check(f"{screens} screens: every one of the {pairs} pairs Windows has "
              f"touching touches on the desk too ({lost} lost)", lost == 0)
    else:
        check(f"{screens} screens: {pairs - lost} of {pairs} pairs Windows "
              f"has touching still touch ({100.0 * lost / pairs:.2f}% lost, "
              f"budget {100.0 * FUZZ_LOST_PAIR_BUDGET:.0f}%)",
              lost <= FUZZ_LOST_PAIR_BUDGET * pairs)
check("the fuzz really did exercise negative origins, on most desks",
      fuzz_negative > 3 * FUZZ_ARRANGEMENTS)

# Why the budget above is not zero, demonstrated rather than asserted. Windows
# can put one screen flush under TWO others whose bottoms are level in pixels;
# if those two are different physical heights their bottoms are NOT level on
# the desk, and no position for the third can touch both. The block keeps one
# contact, and keeps every screen clear, which is all that is left to keep.
IMPOSSIBLE = [
    monitor(r"\\.\DISPLAY1", 0, 0, primary=True),
    monitor(r"\\.\DISPLAY2", 1920, 0),
    monitor(r"\\.\DISPLAY3", 960, 1080, w=3840, h=1080),
]
IMPOSSIBLE_EDID = {r"\\.\DISPLAY1": 15.7, r"\\.\DISPLAY2": 31.5,
                   r"\\.\DISPLAY3": 24.0}
impossible = by_name(pc_block_layout(sized(IMPOSSIBLE, IMPOSSIBLE_EDID)))
check("Windows has the third screen touching both of the others",
      px_touches(IMPOSSIBLE[0], IMPOSSIBLE[2])
      and px_touches(IMPOSSIBLE[1], IMPOSSIBLE[2]))
check("...and their two bottom edges, level in pixels, cannot both be met on "
      "a desk where one panel is 15.7\" and the other 31.5\"",
      impossible[r"\\.\DISPLAY1"]["layout_y"]
      + impossible[r"\\.\DISPLAY1"]["layout_h"]
      != impossible[r"\\.\DISPLAY2"]["layout_y"]
      + impossible[r"\\.\DISPLAY2"]["layout_h"])
check("so one contact is kept, and nothing is left on top of anything",
      (desk_touches(rect(impossible[r"\\.\DISPLAY1"]),
                    rect(impossible[r"\\.\DISPLAY3"]))
       or desk_touches(rect(impossible[r"\\.\DISPLAY2"]),
                       rect(impossible[r"\\.\DISPLAY3"])))
      and not any(rects_overlap(rect(impossible[a]), rect(impossible[b]))
                  for a, b in ((r"\\.\DISPLAY1", r"\\.\DISPLAY2"),
                               (r"\\.\DISPLAY1", r"\\.\DISPLAY3"),
                               (r"\\.\DISPLAY2", r"\\.\DISPLAY3"))))


# === 13. loading says what it resized =======================================
# The merge has always reported a screen redrawn because its EDID was finally
# read. Loading does the same resizing and said nothing, so a launch could
# redraw a screen smaller in silence. It is reported BESIDE the function
# instead of inside the config, because the config is written straight back to
# disk -- a "_resized" key in it would land in openspan_config.json and in
# every saved arrangement, and outlive the load it described.
SAVED_17 = {"links": [], "monitors": [dict(DESK[0], layout_x=-59, layout_y=0,
                                           diagonal_in=17.0)]}
resized_config = normalize_config(copy.deepcopy(SAVED_17), [DESK[0]],
                                  sizes=EDID)
check("a screen the panel's own EDID resized is named, with both numbers: "
      f"{last_normalize_report()}",
      last_normalize_report() == [(r"\\.\DISPLAY5", 17.0, 15.7)])
check("the report is not smuggled into the config, which is saved verbatim",
      set(resized_config) == {"version", "monitors", "devices", "portals",
                              "links"}
      and json.dumps(resized_config, default=str).find("_resized") == -1)
check("reading it twice does not empty it, and a reader cannot rewrite it",
      (lambda first: (first.append(("stolen", 1, 2)),
                      last_normalize_report()
                      == [(r"\\.\DISPLAY5", 17.0, 15.7)])[1])(
          last_normalize_report()))
normalize_config(copy.deepcopy(SAVED_17), [DESK[0]], sizes={})
check("a load that resizes nothing reports nothing, rather than the last "
      "load's answer",
      last_normalize_report() == [])
check("a screen that had no size at all and now has one counts as resized",
      (lambda _cfg: last_normalize_report()
       == [(r"\\.\DISPLAY5", None, 15.7)])(
          normalize_config({"links": [], "monitors": [dict(DESK[0])]},
                           [DESK[0]], sizes=EDID)))
try:
    normalize_config({"links": []}, [])
except ValueError:
    pass
check("a call that could not run leaves no stale report looking current",
      last_normalize_report() == [])


# === 14. the anchor is a function of the hardware, not of the enum order ====
# When the primary has no saved position the block is pinned by a screen that
# does have one. Two screens can both qualify, and EnumDisplayMonitors makes no
# promise about order, so choosing "the first one" moved the whole PC between
# launches with nothing having changed. It is chosen by NAME.
PROMOTED_SAVED = [
    dict(DESK[1], layout_x=-59, layout_y=-838, layout_w=BIG[0],
         layout_h=BIG[1], diagonal_in=17.1),
    dict(DESK[2], layout_x=-1427, layout_y=0, layout_w=SMALL[0],
         layout_h=SMALL[1], diagonal_in=15.7),
]
one_order, _report = merge_live_monitors(
    PROMOTED_SAVED, [DESK[1], DESK[2], DESK[0]], sizes=EDID)
other_order, _report = merge_live_monitors(
    PROMOTED_SAVED, [DESK[2], DESK[0], DESK[1]], sizes=EDID)
check("the same three screens in a different enumeration order give the same "
      "desk",
      {name: rect(row) for name, row in by_name(one_order).items()}
      == {name: rect(row) for name, row in by_name(other_order).items()})
check("...and it is DISPLAY1, the first by name, that keeps its saved place",
      (by_name(one_order)[r"\\.\DISPLAY1"]["layout_x"],
       by_name(one_order)[r"\\.\DISPLAY1"]["layout_y"]) == (-59, -838))
check("a merge told where the devices are keeps the block off them",
      merge_live_monitors(
          PROMOTED_SAVED, [DESK[1], DESK[2], DESK[0]], sizes=EDID,
          obstacles=[(-1427, 0, 1368, 770)])[0]
      != one_order)
check("...and told nothing, behaves exactly as it always did",
      [rect(row) for row in merge_live_monitors(
          PROMOTED_SAVED, [DESK[1], DESK[2], DESK[0]], sizes=EDID,
          obstacles=None)[0]] == [rect(row) for row in one_order])


# === 15. the desk this is running on ========================================
# The rule the whole feature has to survive: Doug's saved layout already agrees
# with Windows, so deriving it from Windows must not move anything. If this
# fails, the next launch on this machine silently rearranges his screens.
#
# It is asked of the REAL inputs -- the monitors EnumDisplayMonitors reports
# and the diagonals the panels state in their EDID -- because those are what
# adopt() hands normalize_config at launch. Feeding it the SAVED rows instead
# was how this section passed while the app was about to move a screen: the
# saved rows are two, this desk has three, and no saved row carries the EDID
# size the load actually applies.
CONFIG_PATH = pathlib.Path(__file__).parents[1] / "openspan_config.json"
try:
    import monitor_edid
    import openspan_setup

    live_monitors = openspan_setup.enum_monitors()
    live_sizes = {name: panel["diagonal_in"]
                  for name, panel in (monitor_edid.physical_diagonals()
                                      or {}).items()
                  if panel and panel.get("diagonal_in")}
except Exception as exc:                                        # noqa: BLE001
    live_monitors, live_sizes = [], {}
    print(f"     (Windows displays unreadable here: {exc})")

THIS_DESK = {r"\\.\DISPLAY5": (-59, 0), r"\\.\DISPLAY1": (-59, -838),
             r"\\.\DISPLAY4": (-1427, 0)}
live_names = {str(row.get("name", "")) for row in live_monitors}
if CONFIG_PATH.exists() and set(THIS_DESK) <= (live_names & set(live_sizes)):
    live_raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    loaded_desk = normalize_config(copy.deepcopy(live_raw),
                                   copy.deepcopy(live_monitors),
                                   sizes=live_sizes)
    running = by_name(loaded_desk["monitors"])
    derived = {name: (row["layout_x"], row["layout_y"])
               for name, row in running.items()}
    check("the load holds all three of this desk's screens, DISPLAY4 included "
          f"-- the one the enumerator used to drop: {sorted(derived)}",
          set(THIS_DESK) <= set(derived))
    check(f"every screen lands exactly where it was measured to: {derived}",
          all(derived[name] == where for name, where in THIS_DESK.items()))
    check("the primary did not move: it is still where the user put the block",
          derived[r"\\.\DISPLAY5"] == (-59, 0))
    check("DISPLAY4 is flush against the primary's left edge, tops level",
          running[r"\\.\DISPLAY4"]["layout_x"]
          + running[r"\\.\DISPLAY4"]["layout_w"]
          == running[r"\\.\DISPLAY5"]["layout_x"]
          and running[r"\\.\DISPLAY4"]["layout_y"]
          == running[r"\\.\DISPLAY5"]["layout_y"])
    check("DISPLAY1 is flush above it, left edges aligned -- the 5 units it "
          "drops is 17.0\" giving way to the 17.1\" the panel states",
          running[r"\\.\DISPLAY1"]["layout_y"]
          + running[r"\\.\DISPLAY1"]["layout_h"]
          == running[r"\\.\DISPLAY5"]["layout_y"]
          and running[r"\\.\DISPLAY1"]["layout_x"]
          == running[r"\\.\DISPLAY5"]["layout_x"])
    check("nothing on this desk covers anything else on it",
          [(one["name"], two["name"])
           for index, one in enumerate(layout_surfaces(loaded_desk))
           for two in layout_surfaces(loaded_desk)[index + 1:]
           if rects_overlap(one["rect"], two["rect"])] == [])
    check("the live config was only ever read",
          json.loads(CONFIG_PATH.read_text(encoding="utf-8")) == live_raw)
else:
    print("SKIP  this desk's three screens are not all attached and readable "
          f"-- live {sorted(live_names)}, EDID {sorted(live_sizes)}")

print("RESULT: ALL PASS")
