"""Exhaustive pure-core and structural checks for window tiling."""

import ast
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import window_tiling as tiling


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


# ---- import and registration structure ----------------------------------------

check("importing tiling geometry does not pull in ctypes",
      "ctypes" not in sys.modules)

source_path = pathlib.Path(tiling.__file__)
source = source_path.read_text(encoding="utf-8")
tree = ast.parse(source)
parents = {}
for node in ast.walk(tree):
    for child in ast.iter_child_nodes(node):
        parents[child] = node


def enclosing_function(node):
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return None


ctypes_imports = [
    node for node in ast.walk(tree)
    if ((isinstance(node, ast.Import)
         and any(alias.name == "ctypes" or alias.name.startswith("ctypes.")
                 for alias in node.names))
        or (isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module == "ctypes" or node.module.startswith("ctypes."))))]
check("every ctypes import sits inside a function",
      ctypes_imports and all(enclosing_function(node) for node in ctypes_imports))

native_calls = {
    node.func.attr
    for node in ast.walk(tree)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
}
forbidden_registrations = {
    "RegisterHotKey", "SetWindowsHookExW", "SetWindowsHookExA",
    "SetWinEventHook", "RegisterRawInputDevices",
}
check("the module contains no hotkey or hook registration",
      not native_calls.intersection(forbidden_registrations)
      and "keyboard_interception" not in source)


# ---- every Halves_partition_exactly_with_no_gap_or_overlap case ---------------

half_cases = [
    (0, 0, 1920, 1040),
    (0, 0, 2560, 1400),
    (-1920, 0, 1920, 1040),
    (0, -1080, 1920, 1032),
    (48, 0, 1872, 1080),
    (0, 0, 1080, 1872),
    (0, 0, 1921, 1041),
    (-7, -3, 1373, 767),
]
for x, y, width, height in half_cases:
    work = tiling.Rect(x, y, width, height)
    left = tiling.compute(work, tiling.TileZone.LEFT_HALF)
    right = tiling.compute(work, tiling.TileZone.RIGHT_HALF)
    top = tiling.compute(work, tiling.TileZone.TOP_HALF)
    bottom = tiling.compute(work, tiling.TileZone.BOTTOM_HALF)
    check(f"halves partition exactly for {work}",
          work.left == left.left
          and left.right == right.left
          and work.right == right.right
          and work.height == left.height == right.height
          and work.width * work.height
          == left.width * left.height + right.width * right.height
          and work.top == top.top
          and top.bottom == bottom.top
          and work.bottom == bottom.bottom
          and work.width == top.width == bottom.width)


# ---- every Quarters_tile_the_work_area_exactly case ---------------------------

quarter_cases = [(0, 0, 1920, 1040), (-1921, -3, 1873, 1041)]
for x, y, width, height in quarter_cases:
    work = tiling.Rect(x, y, width, height)
    tl = tiling.compute(work, tiling.TileZone.TOP_LEFT)
    tr = tiling.compute(work, tiling.TileZone.TOP_RIGHT)
    bl = tiling.compute(work, tiling.TileZone.BOTTOM_LEFT)
    br = tiling.compute(work, tiling.TileZone.BOTTOM_RIGHT)
    total = sum(rect.width * rect.height for rect in (tl, tr, bl, br))
    pairs = ((tl, tr), (tl, bl), (tl, br),
             (tr, bl), (tr, br), (bl, br))
    check(f"quarters tile exactly for {work}",
          (tl.left, tl.top) == (work.left, work.top)
          and (tr.right, tr.top) == (work.right, work.top)
          and (bl.left, bl.bottom) == (work.left, work.bottom)
          and (br.right, br.bottom) == (work.right, work.bottom)
          and tl.right == tr.left
          and bl.right == br.left
          and tl.bottom == bl.top
          and tr.bottom == br.top
          and total == work.width * work.height
          and all(a.intersection_area(b) == 0 for a, b in pairs))


# ---- Two_step_refinement_matches_the_macos_semantics --------------------------

refinement_expectations = {
    (tiling.TileZone.LEFT_HALF, tiling.TileDirection.UP):
        tiling.TileZone.TOP_LEFT,
    (tiling.TileZone.LEFT_HALF, tiling.TileDirection.DOWN):
        tiling.TileZone.BOTTOM_LEFT,
    (tiling.TileZone.RIGHT_HALF, tiling.TileDirection.UP):
        tiling.TileZone.TOP_RIGHT,
    (tiling.TileZone.RIGHT_HALF, tiling.TileDirection.DOWN):
        tiling.TileZone.BOTTOM_RIGHT,
    (tiling.TileZone.TOP_LEFT, tiling.TileDirection.DOWN):
        tiling.TileZone.LEFT_HALF,
    (tiling.TileZone.BOTTOM_LEFT, tiling.TileDirection.UP):
        tiling.TileZone.LEFT_HALF,
    (tiling.TileZone.TOP_RIGHT, tiling.TileDirection.DOWN):
        tiling.TileZone.RIGHT_HALF,
    (tiling.TileZone.BOTTOM_RIGHT, tiling.TileDirection.UP):
        tiling.TileZone.RIGHT_HALF,
}
check("two-step refinement matches macOS semantics",
      all(tiling.refine(zone, direction) == expected
          for (zone, direction), expected in refinement_expectations.items())
      and tiling.refine(tiling.TileZone.LEFT_HALF,
                        tiling.TileDirection.LEFT) is None
      and tiling.refine(tiling.TileZone.TOP_HALF,
                        tiling.TileDirection.UP) is None)

unmapped = [
    (zone, direction)
    for zone in tiling.TileZone
    for direction in tiling.TileDirection
    if (zone, direction) not in refinement_expectations
]
check("non-refining combinations are idempotently absent",
      all(tiling.refine(zone, direction) is None
          and tiling.refine(zone, direction) is None
          for zone, direction in unmapped))
check("repeat presses remain in their directional half",
      all(tiling.zone_for_direction(direction,
                                    tiling.zone_for_direction(direction))
          == tiling.zone_for_direction(direction)
          for direction in tiling.TileDirection))


# ---- Size_constraints_clamp_inside_the_work_area ------------------------------

work = tiling.Rect(0, 0, 1000, 800)
target = tiling.compute(work, tiling.TileZone.BOTTOM_RIGHT)
adjusted = tiling.apply_size_constraints(target, work, 700, 300)
check("minimum size grows inward and stays inside the work area",
      adjusted.width == 700
      and adjusted.right <= work.right
      and adjusted.left >= work.left
      and adjusted.bottom <= work.bottom)

adjusted = tiling.apply_size_constraints(
    target, work, 100, 100, max_width=300, max_height=250)
check("maximum constraints clamp both dimensions",
      adjusted == tiling.Rect(500, 400, 300, 250))

adjusted = tiling.apply_size_constraints(
    target, work, 5000, 5000, max_width=1000, max_height=800)
check("minimum constraints larger than the monitor clamp to the work area",
      adjusted == work)

try:
    tiling.apply_size_constraints(target, work, 700, 300, max_width=600)
except ValueError:
    invalid_constraints_rejected = True
else:
    invalid_constraints_rejected = False
check("inverted size constraints are rejected", invalid_constraints_rejected)


# ---- Restore_tracker_keeps_only_the_first_pre_tile_bounds ---------------------

tracker = tiling.TileRestoreTracker()
original = tiling.Rect(100, 100, 800, 600)
tracker.on_tiled(1, original, tiling.TileZone.LEFT_HALF)
tracker.on_tiled(1, tiling.Rect(0, 0, 960, 1040), tiling.TileZone.TOP_LEFT)
check("restore tracker updates only the current zone",
      tracker.get_current_zone(1) == tiling.TileZone.TOP_LEFT)
check("restore tracker keeps only the first pre-tile bounds",
      tracker.try_restore(1) == original)
check("restore is one-shot", tracker.try_restore(1) is None)


# ---- Manual_move_invalidates_the_restore_record -------------------------------

tracker = tiling.TileRestoreTracker()
tracker.on_tiled(7, tiling.Rect(10, 10, 500, 500),
                 tiling.TileZone.RIGHT_HALF)
tracker.invalidate(7)
check("manual move invalidates the restore record",
      tracker.try_restore(7) is None and tracker.get_current_zone(7) is None)


# ---- driving decisions and multiple monitor work areas ------------------------

work = tiling.Rect(-1921, -3, 1873, 1041)
check("every computed zone is recognized within driver tolerance",
      all(tiling.recognize_zone(tiling.compute(work, zone), work) == zone
          for zone in tiling.TileZone))

zone, rect = tiling.tile_towards(
    tiling.compute(work, tiling.TileZone.LEFT_HALF), work,
    tiling.TileDirection.UP)
check("current geometry drives stateless refinement",
      zone == tiling.TileZone.TOP_LEFT
      and rect == tiling.compute(work, tiling.TileZone.TOP_LEFT))

zone, rect = tiling.tile_towards(
    tiling.Rect(10, 20, 300, 200), work, tiling.TileDirection.RIGHT,
    known_zone=tiling.TileZone.LEFT_HALF)
check("a manual move invalidates tracked refinement",
      zone == tiling.TileZone.RIGHT_HALF
      and rect == tiling.compute(work, tiling.TileZone.RIGHT_HALF))

monitors = (
    tiling.MonitorWorkArea(
        1, tiling.Rect(-1920, 0, 1920, 1080),
        tiling.Rect(-1920, 0, 1920, 1040), False, "LEFT"),
    tiling.MonitorWorkArea(
        2, tiling.Rect(0, 0, 2560, 1440),
        tiling.Rect(48, 0, 2512, 1440), True, "PRIMARY"),
)
check("work-area selection uses the monitor with greatest overlap",
      tiling.select_work_area(tiling.Rect(-100, 100, 400, 400), monitors)
      == monitors[1].work_area)
check("off-screen work-area selection uses the nearest monitor",
      tiling.select_work_area(tiling.Rect(-4000, 100, 100, 100), monitors)
      == monitors[0].work_area)
check("empty monitor lists have no work area",
      tiling.select_work_area(tiling.Rect(0, 0, 1, 1), ()) is None)
