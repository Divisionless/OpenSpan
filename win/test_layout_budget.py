# SPDX-License-Identifier: AGPL-3.0-or-later
"""The page viewport fits the monitor while content remains fully scrollable.

App is never constructed because doing so starts bridge workers. Structural
packing is read from the AST; viewport policy and the arrangement canvas are
driven directly with no visible Tk window and no writes to live state.
"""
import ast
import os
import shutil
import sys
import tempfile
import tkinter as tk

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import openspan as A  # noqa: E402

fails = []


def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name + (
        "" if condition or not detail else "\n      " + detail))
    if not condition:
        fails.append(name)


source = open(os.path.join(HERE, "openspan.py"), encoding="utf-8").read()
module = ast.parse(source, filename="openspan.py")


def _name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name(node.value)
        return None if base is None else base + "." + node.attr
    return None


def _method(class_name, method_name):
    for node in ast.walk(module):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return next((item for item in node.body
                         if isinstance(item, ast.FunctionDef)
                         and item.name == method_name), None)
    return None


def _packs(function):
    result = {}
    for node in ast.walk(function):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "pack"):
            continue
        name = _name(node.func.value)
        if not name:
            continue
        kwargs = {}
        for keyword in node.keywords:
            try:
                kwargs[keyword.arg] = ast.literal_eval(keyword.value)
            except (ValueError, TypeError):
                kwargs[keyword.arg] = "<expression>"
        result[name] = kwargs
    return result


app_init = _method("App", "__init__")
bt_init = _method("BtPanel", "__init__")
app_packs = _packs(app_init)
bt_packs = _packs(bt_init)
init_src = ast.unparse(app_init)


print("\n---- viewport, never document-height geometry ----")
check("page height constants are explicit positive integers",
      isinstance(A.PAGE_PREFERRED_WINDOW_H, int)
      and isinstance(A.PAGE_MIN_WINDOW_H, int)
      and A.PAGE_PREFERRED_WINDOW_H >= A.PAGE_MIN_WINDOW_H > 0)
for available in (400, 500, 680, 800, 930, 1040, 2160, 10000):
    geometry, minimum = A.page_window_plan(available)
    check(f"{available}px: geometry and floor fit the work area",
          1 <= minimum <= geometry <= max(400, available),
          f"geometry={geometry} minimum={minimum}")
check("the preferred height is used when the monitor permits it",
      A.page_window_plan(1040) == (930, 680),
      repr(A.page_window_plan(1040)))
check("a short monitor outranks both preferred and minimum",
      A.page_window_plan(540) == (540, 540),
      repr(A.page_window_plan(540)))
check("the document's measured height is not passed to page_window_plan",
      "page_window_plan(avail_h)" in init_src
      and "page_window_plan(content_h" not in init_src)
check("document height is retained for telemetry and scrollregion",
      "content_h = bridge.winfo_reqheight()" in init_src
      and "self._sync_page_scrollregion()" in init_src)
check("the old clipped-content warning path is absent",
      "_content_clipped" not in source and "panels below the fold" not in source)


print("\n---- geometry reaches Tk from the viewport plan ----")
geometry_calls = [node for node in ast.walk(app_init)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)
                  and node.func.attr == "geometry"]
minsize_calls = [node for node in ast.walk(app_init)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "minsize"]
last_geometry = max(geometry_calls, key=lambda node: node.lineno)
last_minsize = max(minsize_calls, key=lambda node: node.lineno)
check("final geometry height is computed", isinstance(last_geometry.args[0],
                                                       ast.JoinedStr))
check("final minsize height is computed from min_h",
      _name(last_minsize.args[1]) == "min_h")
check("minimum width remains 940, and is now the one named constant that "
      "_render_dock restates when the dock expands",
      _name(last_minsize.args[0]) == "PAGE_MIN_WINDOW_W"
      and A.PAGE_MIN_WINDOW_W == 940)
check("work area uses the selected EsotericOS Desktop monitor",
      "self._desktop_monitor_name()" in init_src
      and "work_area_height" in init_src)


print("\n---- only viewport cavities expand ----")
# `main` is the DASHBOARD SURFACE, and since 2026-08-28 it is not packed in
# __init__ at all: App._render_dock packs exactly one surface into the body,
# which is what makes "one surface showing" a property of the code rather than
# of everyone remembering. So the expanding cavity to assert here is the body,
# and the surface's own pack is read from the method that owns it.
dock_packs = _packs(_method("App", "_render_dock"))
rail_packs = _packs(_method("App", "_build_dock_rail"))
check("outer body cavity expands", app_packs.get("body", {}).get("expand")
      is True and app_packs.get("body", {}).get("fill") == "both",
      repr(app_packs.get("body")))
check("the dock rail takes the right edge and only its own width",
      rail_packs.get("rail", {}).get("side") == "right"
      and rail_packs.get("rail", {}).get("fill") == "y"
      and not rail_packs.get("rail", {}).get("expand"),
      repr(rail_packs.get("rail")))
check("a surface fills whatever the rail left, and nothing in __init__ packs "
      "one",
      dock_packs.get("surface", {}).get("expand") is True
      and dock_packs.get("surface", {}).get("fill") == "both"
      and dock_packs.get("surface", {}).get("side") == "left"
      and "main" not in app_packs,
      repr(dock_packs))
check("page Canvas consumes the viewport",
      app_packs.get("page_canvas", {}).get("expand") is True
      and app_packs.get("page_canvas", {}).get("fill") == "both",
      repr(app_packs.get("page_canvas")))
check("arrangement wrapper does not absorb vertical surplus",
      not app_packs.get("arr_wrap", {}).get("expand"),
      repr(app_packs.get("arr_wrap")))
check("arrangement canvas does not absorb vertical surplus",
      not app_packs.get("self.canvas", {}).get("expand"),
      repr(app_packs.get("self.canvas")))
check("Bluetooth body keeps its declared row height",
      not bt_packs.get("body", {}).get("expand"),
      repr(bt_packs.get("body")))
check("Bluetooth tree still expands to fill its row (law 10: no scrollbar beside it)",
      bt_packs.get("self.tree", {}).get("expand") is True,
      repr(bt_packs.get("self.tree")))
# THE FAULT BANNER COSTS THE PAGE NOTHING AT REST. It replaced a LabelFrame
# holding four combos, three status labels and three buttons -- all of which
# were packed unconditionally, on every launch, whether or not anything was
# wrong. This one is built in __init__ and packed by _show_faults, only while
# an audit has actually found a fault, so a healthy desk spends zero pixels on
# it. Its absence from bt_packs IS the assertion.
check("the fault banner is not packed while the panel is built",
      "self.fault_box" not in bt_packs, repr(sorted(bt_packs)))
show_packs = _packs(_method("BtPanel", "_show_faults"))
check("and when a fault does pack it, it takes width and never surplus height",
      show_packs.get("self.fault_box", {}).get("fill") == "x"
      and not show_packs.get("self.fault_box", {}).get("expand"),
      repr(show_packs.get("self.fault_box")))
# The rows are subscripts (row['row']), which _packs cannot name, so this one
# is read off the source of the method that packs them.
_row_src = ast.unparse(_method("BtPanel", "_set_fault"))
check("each fault row is width-only too",
      "row['row'].pack(fill='x'" in _row_src and "expand" not in _row_src,
      _row_src)


print("\n---- arrangement remains width-fitted and bounded ----")
scratch = tempfile.mkdtemp(prefix="esotericos-layout-")
A.CONFIG = os.path.join(scratch, "config.json")
root = tk.Tk()
root.withdraw()
canvas = A.MultiArrangeCanvas(root, on_change=None, height=270)
canvas.monitors = [{"name": "DESK", "x": 0, "y": 0, "w": 100, "h": 100,
                    "layout_x": 0, "layout_y": 0,
                    "layout_w": 7409, "layout_h": 3039}]
canvas.targets = []
canvas.selected = None
canvas.ipad = None
canvas._world_bounds()
canvas.winfo_width = lambda: 852
canvas._fit_height()
fit = canvas.winfo_reqheight()
check("live-desk width still fits near 447px", abs(fit - 447) <= 2,
      repr(fit))
canvas._fit_height()
check("repeated fitting is stable", canvas.winfo_reqheight() == fit)
for width, expected in ((300, A.MultiArrangeCanvas.FIT_MIN_H),
                        (2000, A.MultiArrangeCanvas.FIT_MAX_H)):
    canvas.winfo_width = lambda width=width: width
    canvas._fit_height()
    check(f"{width}px canvas clamps to {expected}px",
          canvas.winfo_reqheight() == expected,
          repr(canvas.winfo_reqheight()))
for method_name in ("adopt", "_release", "save"):
    method = _method("MultiArrangeCanvas", method_name)
    calls = [node for node in ast.walk(method)
             if isinstance(node, ast.Call)
             and _name(node.func) == "self._fit_height"]
    check(f"{method_name} re-fits after desk shape changes", bool(calls))

root.destroy()
shutil.rmtree(scratch, ignore_errors=True)
print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
