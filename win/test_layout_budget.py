"""The window's height budget: who is allowed to expand, and how tall the
window is permitted to open.

Doug's window was 1921 x 2120 px and had to span two physical monitors. That
was never a taste problem, it was a mechanical one, and there were two separate
faults feeding it.

The first is a packer fault. `arr_wrap` and the arrangement canvas inside it
both packed with expand=True, and they were the ONLY expanding chain in the
left column. So every pixel of surplus window height was handed to a canvas
whose drawing is an aspect-fit of the desk -- and at every width this window has
ever had, that fit is WIDTH-bound. The canvas could not use a single pixel of
the height it was collecting; it just drew the same picture with more and more
dead PANEL above and below it. The Bluetooth Treeview on the right had the
identical disease: it declared height=8 and then expanded, which made the
declaration decorative.

The second is worse, because it is silent. Nothing in openspan.py set a window
HEIGHT. geometry("1120x930") named one at import; _set_win_width parses the
height back out of geometry() and puts it straight back, so it never changed
again. And minsize(940, 680) permitted a window far shorter than the left
column actually needs. At that size Tk's packer simply does not place the last
panels -- "System control" and "Bluetooth radio" are gone. There is no
scrolling anywhere in this app by design, so there is no scrollbar, no clipped
edge, and nothing whatsoever to tell you those panels exist. Both numbers are
now derived from the measured content instead.

WHAT THIS TEST CAN AND CANNOT SEE. App(root) starts the VM and the audio
workers, so it cannot be constructed here -- which means the assembled window's
real reqheight is not observable from a test, and this file does not pretend to
measure it. It splits the problem instead:

    * the packing rules are read out of the source with `ast`. That is a
      structural claim -- "no widget in this chain is allowed to expand" -- and
      structure is exactly what ast can settle.
    * the sizing POLICY is a pure function, window_height_plan, so the
      minsize >= content invariant is checked over a whole range of inputs
      rather than at one measured point.
    * the pieces that CAN be built alone -- MultiArrangeCanvas, BtPanel -- are
      built and measured for real.

The one thing left unobserved is the total: whether the assembled window comes
in under LAYOUT_MAX_CONTENT_H. The app measures that itself at startup and logs
it, and the tripwire is asserted here to exist and to be wired to the real
measurement.

No Tk window is shown (the root is withdrawn), the live config, the profile
directory and the Bluetooth prefs are all redirected to temp files, and nothing
here touches the running app.

Exit 0 = all pass.
"""
import ast
import os
import shutil
import sys
import tempfile
import tkinter as tk
from tkinter import ttk

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import openspan as A  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (
        "" if cond or not detail else "\n      " + detail))
    if not cond:
        fails.append(name)


# Nothing below may read or write anything the running app owns.
SCRATCH = tempfile.mkdtemp(prefix="openspan-layout-")
A.CONFIG = os.path.join(SCRATCH, "live.json")
A.PROFILE_DIR = os.path.join(SCRATCH, "profiles")
A.BT_PREFS = os.path.join(SCRATCH, "bt_prefs.json")

# Declared ceilings. These are the budget: a panel that grows past one of them
# fails this test rather than quietly making the window taller than a monitor.
BT_PANEL_MAX_H = 700        # measured 637 px today
CANVAS_FIT_MAX_H = 560      # must agree with MultiArrangeCanvas.FIT_MAX_H

# The live desk, as measured on Doug's machine: raw bounding box of every
# screen and display, before _world_bounds applies its padding.
LIVE_RAW_W, LIVE_RAW_H = 7409, 3039
LIVE_CANVAS_W = 852         # the canvas width in the live window


# ---- reading the packing rules out of the source ---------------------------
with open(os.path.join(HERE, "openspan.py"), encoding="utf-8") as handle:
    MODULE = ast.parse(handle.read(), filename="openspan.py")


def _name(node):
    """Render `foo` or `self.foo` as text; anything else is not a widget name."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name(node.value)
        return None if base is None else base + "." + node.attr
    return None


def _method(class_name, method_name):
    for node in ast.walk(MODULE):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if (isinstance(item, ast.FunctionDef)
                        and item.name == method_name):
                    return item
    return None


def _layout(fn):
    """(parent, packs) for one function body.

    parent[widget] = the master it was constructed against
    packs[widget]  = the literal keywords of its .pack() call

    A widget built and packed in one expression -- tk.Label(bridge, ...).pack()
    -- has no name, so it is recorded under a line-number key. Those still
    count: an anonymous label that expands is just as much a sponge as a named
    one, and skipping them would make this audit a half-audit.
    """
    parent, packs = {}, {}
    for node in ast.walk(fn):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.value, ast.Call) and node.value.args):
            target = _name(node.targets[0])
            master = _name(node.value.args[0])
            if target and master:
                parent[target] = master
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "pack"):
            continue
        base = node.func.value
        who = _name(base)
        if who is None and isinstance(base, ast.Call) and base.args:
            master = _name(base.args[0])
            if master:
                who = f"<anonymous line {node.lineno}>"
                parent[who] = master
        if who is None:
            continue
        keywords = {}
        for keyword in node.keywords:
            try:
                keywords[keyword.arg] = ast.literal_eval(keyword.value)
            except ValueError:
                keywords[keyword.arg] = "<expression>"
        packs[who] = keywords
    return parent, packs


APP_INIT = _method("App", "__init__")
check("App.__init__ found in the source", APP_INIT is not None)
PARENT, PACKS = _layout(APP_INIT)


def children_of(master):
    return sorted(w for w, m in PARENT.items() if m == master)


def expands(widget):
    return bool(PACKS.get(widget, {}).get("expand"))


# ---- (a) only ONE thing in the left column is allowed to expand -------------
# The chain frames themselves -- main, bridge_col, bridge -- deliberately keep
# expand=True. They are the cavity, not the sponge: the surplus has to be able
# to REACH the spacer at the bottom of `bridge`. Take expand off any of them and
# the surplus strands in `full` instead, and the two-column split collapses.
for frame in ("main", "bridge_col", "bridge"):
    check(f"cavity frame `{frame}` still expands", expands(frame),
          f"pack kwargs: {PACKS.get(frame)}")

bridge_kids = children_of("bridge")
expanding = [w for w in bridge_kids if expands(w)]
check("`bridge` has children at all", len(bridge_kids) >= 5,
      f"found: {bridge_kids}")
check("the only expanding child of `bridge` is the designated spacer",
      expanding == ["self._bridge_spacer"],
      f"expanding: {expanding}   of: {bridge_kids}")
check("`bridge` is never left with zero expanding children",
      len(expanding) == 1, f"expanding: {expanding}")

check("arr_wrap no longer expands -- it was the sponge PARENT",
      not expands("arr_wrap"), f"pack kwargs: {PACKS.get('arr_wrap')}")
check("the arrangement canvas no longer expands",
      not expands("self.canvas"), f"pack kwargs: {PACKS.get('self.canvas')}")
check("the canvas is still a child of arr_wrap",
      PARENT.get("self.canvas") == "arr_wrap", str(PARENT.get("self.canvas")))
check("capping the canvas alone would not have been enough "
      "(both flags are off)",
      not expands("arr_wrap") and not expands("self.canvas"))
check("nothing else inside arr_wrap expands",
      [w for w in children_of("arr_wrap") if expands(w)] == [],
      str([w for w in children_of("arr_wrap") if expands(w)]))

BT_INIT = _method("BtPanel", "__init__")
BT_PARENT, BT_PACKS = _layout(BT_INIT)
check("the BtPanel body no longer expands, so height=8 is real",
      not bool(BT_PACKS.get("body", {}).get("expand")),
      f"pack kwargs: {BT_PACKS.get('body')}")
# The tree itself keeps expand=True on purpose. pack's expand is not
# axis-specific: with side="left" it is what gives the tree the leftover WIDTH
# beside its scrollbar. Measured in an 800px-wide column, expand=False there
# opens a 216px hole. The height cap is already fully delivered by `body`.
check("the tree keeps expand=True (that is its horizontal fill, not height)",
      bool(BT_PACKS.get("self.tree", {}).get("expand")),
      f"pack kwargs: {BT_PACKS.get('self.tree')}")


# ---- (b) the height budget is declared, and the tripwire is wired -----------
check("LAYOUT_MAX_CONTENT_H is declared",
      isinstance(getattr(A, "LAYOUT_MAX_CONTENT_H", None), int))
check("the canvas fit ceiling matches this test's constant",
      A.MultiArrangeCanvas.FIT_MAX_H == CANVAS_FIT_MAX_H,
      f"{A.MultiArrangeCanvas.FIT_MAX_H} vs {CANVAS_FIT_MAX_H}")

init_src = ast.dump(APP_INIT)
measures = [n for n in ast.walk(APP_INIT)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "winfo_reqheight"
            and _name(n.func.value) == "full"]
check("App.__init__ measures the built content (full.winfo_reqheight())",
      len(measures) == 1, f"found {len(measures)}")
check("App.__init__ runs that measurement through window_height_plan",
      any(isinstance(n, ast.Call) and _name(n.func) == "window_height_plan"
          for n in ast.walk(APP_INIT)))
check("the budget ceiling is referenced where it is exceeded",
      "LAYOUT_MAX_CONTENT_H" in init_src)


# ---- (c) minsize can never again be shorter than the content ---------------
# This is the check that permanently closes the silent packer-starvation mode.
# It is asserted as a PROPERTY over a range rather than at one point, because
# the one point -- the assembled window -- is the thing a test cannot build.
worst = None
for content in list(range(1, 400, 37)) + [680, 930, 1200, 1599, 1600, 1601,
                                          2120, 4000]:
    geom_h, min_h, over = A.window_height_plan(content)
    if min_h < content or geom_h < content:
        worst = (content, geom_h, min_h)
        break
check("minsize height >= content height, for every content height",
      worst is None, f"first violation: {worst}")
check("geometry height == content height, so the window opens exactly as tall "
      "as it needs",
      all(A.window_height_plan(c)[0] == c for c in (700, 930, 1450, 2120)))
check("the ceiling is a tripwire, not a clamp -- over-budget content still "
      "gets its full minsize",
      A.window_height_plan(A.LAYOUT_MAX_CONTENT_H + 500)[1]
      == A.LAYOUT_MAX_CONTENT_H + 500)
check("over-budget content is reported",
      A.window_height_plan(A.LAYOUT_MAX_CONTENT_H + 1)[2] is True
      and A.window_height_plan(A.LAYOUT_MAX_CONTENT_H)[2] is False)

# ...and that the derived numbers actually reach Tk. A literal here is the bug:
# minsize(940, 680) is what let the window be sized shorter than its content.
geometry_calls = [n for n in ast.walk(APP_INIT)
                  if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "geometry"]
minsize_calls = [n for n in ast.walk(APP_INIT)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "minsize"]
check("App.__init__ still sets geometry and minsize",
      len(geometry_calls) >= 2 and len(minsize_calls) >= 2,
      f"geometry x{len(geometry_calls)}, minsize x{len(minsize_calls)}")
last_geometry = max(geometry_calls, key=lambda n: n.lineno)
last_minsize = max(minsize_calls, key=lambda n: n.lineno)
check("the LAST geometry() height is computed, not a literal",
      isinstance(last_geometry.args[0], ast.JoinedStr),
      f"line {last_geometry.lineno}")
check("the LAST minsize() height is computed, not a literal",
      len(last_minsize.args) == 2
      and not isinstance(last_minsize.args[1], ast.Constant),
      f"line {last_minsize.lineno}")
check("width behaviour is unchanged -- minsize width is still 940",
      isinstance(last_minsize.args[0], ast.Constant)
      and last_minsize.args[0].value == 940)


# ---- Tk from here down -----------------------------------------------------
root = tk.Tk()
root.withdraw()            # never draw on the desk this is being run from

style = ttk.Style()
try:
    style.theme_use("clam")
except tk.TclError:
    pass

# The button metric, measured the way App._theme configures it. A stacked
# column of button rows pays this difference once per row.
style.configure("TButton", padding=8, font=("Segoe UI", 10))
was = ttk.Button(root, text="Restart keyboard").winfo_reqheight()
style.configure("TButton", padding=(10, 3), font=("Segoe UI", 10))
now = ttk.Button(root, text="Restart keyboard").winfo_reqheight()
check("padding=(10, 3) is shorter than padding=8 by 10px per stacked row",
      was - now == 10, f"{was} -> {now}")
theme = _method("App", "_theme")
check("App._theme actually uses padding=(10, 3)",
      "(10, 3)" in ast.unparse(theme).replace("(10,3)", "(10, 3)"))


# ---- (d) REGRESSION GUARD: the drawing must be pixel-identical -------------
# The whole justification for this wave is that the canvas was collecting height
# it could not use. If that is true, taking the height away cannot change the
# drawing by so much as a pixel. This is where that claim is settled.
canvas = A.MultiArrangeCanvas(root, on_change=None, height=270)
# The live desk, stated directly rather than reconstructed through
# normalize_config, so the world under test is exactly the measured one.
canvas.monitors = [{"name": "DESK", "x": 0, "y": 0, "w": 100, "h": 100,
                    "layout_x": 0, "layout_y": 0,
                    "layout_w": LIVE_RAW_W, "layout_h": LIVE_RAW_H}]
canvas.targets = []
canvas.selected = None
canvas.ipad = None
canvas._world_bounds()

# _world_bounds pads with ONE scalar on all four sides -- 12% of the LONGER
# axis, floor 180 -- not 12% per axis. Getting that wrong gives an aspect of
# 2.438 instead of 1.907 and every number below moves.
pad = max(180, int(max(LIVE_RAW_W, LIVE_RAW_H) * 0.12))
check("the world pad is one scalar on all four sides (889 px here)",
      pad == 889 and (canvas.wx1 - canvas.wx0) == LIVE_RAW_W + 2 * pad
      and (canvas.wy1 - canvas.wy0) == LIVE_RAW_H + 2 * pad,
      f"pad={pad} world={canvas.wx1 - canvas.wx0} x "
      f"{canvas.wy1 - canvas.wy0}")
world_w = canvas.wx1 - canvas.wx0
world_h = canvas.wy1 - canvas.wy0
check("padded world is 9187 x 4817, aspect 1.9072",
      (world_w, world_h) == (9187, 4817)
      and abs(world_w / world_h - 1.9072) < 0.0005,
      f"{world_w} x {world_h}, aspect {world_w / world_h:.4f}")

# winfo_width() is 1 on a withdrawn root -- the widget is never mapped. Stubbing
# it is the seam that lets the real _scale/_fit_height arithmetic run at the
# live window's width without putting a window on Doug's desk.
canvas.winfo_width = lambda: LIVE_CANVAS_W

fit = None
canvas._fit_height()
fit = canvas.winfo_reqheight()
check("_fit_height() is 447 px at the live canvas width",
      abs(fit - 447) <= 2, str(fit))
check("_fit_height() does NOT double-apply the 0.94 inset "
      "(that would give 420)",
      abs(fit - 420) > 5, str(fit))

canvas._fit_height()
again = canvas.winfo_reqheight()
check("_fit_height() twice in a row is stable -- no oscillation",
      again == fit, f"{fit} -> {again}")

canvas.winfo_height = lambda: fit
scale = canvas._scale()[0]
check("_scale() is 0.0872 -- the drawing is pixel-identical to before "
      "this wave",
      abs(scale - 0.0872) <= 0.0005, f"{scale:.6f}")
check("the fit is WIDTH-bound, which is why the surplus height was useless",
      LIVE_CANVAS_W / world_w <= fit / world_h,
      f"{LIVE_CANVAS_W / world_w:.6f} vs {fit / world_h:.6f}")

# The clamps, so a one-screen desk cannot collapse the canvas to nothing and a
# very wide one cannot re-open the hole this wave closed.
for width, expected in ((300, A.MultiArrangeCanvas.FIT_MIN_H),
                        (2000, A.MultiArrangeCanvas.FIT_MAX_H)):
    canvas.winfo_width = lambda width=width: width
    canvas._fit_height()
    check(f"at width {width} the fit clamps to {expected}",
          canvas.winfo_reqheight() == expected,
          str(canvas.winfo_reqheight()))

# A drag and an arrangement switch both change the world's aspect and NEITHER
# fires <Configure>. A Configure-only hook goes stale on the app's primary
# gesture, so the hooks are asserted to exist.
for method in ("adopt", "_release", "save"):
    node = _method("MultiArrangeCanvas", method)
    calls = [n for n in ast.walk(node)
             if isinstance(n, ast.Call) and _name(n.func) == "self._fit_height"]
    check(f"MultiArrangeCanvas.{method}() re-fits the height",
          len(calls) >= 1, f"found {len(calls)}")
node = _method("MultiArrangeCanvas", "redraw")
calls = [n for n in ast.walk(node)
         if isinstance(n, ast.Call) and _name(n.func) == "self._fit_height"]
check("redraw() does NOT re-fit -- _drag calls it every motion tick, and "
      "changing height mid-drag moves the rect under the cursor",
      not calls)

# ...and that it really follows a reshaped desk, not just that the call exists.
canvas.winfo_width = lambda: LIVE_CANVAS_W
canvas._fit_height()
before_reshape = canvas.winfo_reqheight()
canvas.monitors[0]["layout_h"] = LIVE_RAW_H * 2
canvas.save()
check("re-shaping the desk changes the fitted height",
      canvas.winfo_reqheight() != before_reshape,
      f"{before_reshape} -> {canvas.winfo_reqheight()}")


# ---- the pieces that can be built alone, against their own budgets ---------
panel = A.BtPanel(root, app=None)
panel.update_idletasks()
tree_h = panel.tree.winfo_reqheight()
body = next(w for w in panel.winfo_children()
            if w.winfo_class() == "Frame" and w.winfo_reqheight() == tree_h)
check("the Bluetooth tree's declared height=8 is now the height it takes",
      body.winfo_reqheight() == tree_h and tree_h > 150,
      f"body {body.winfo_reqheight()} px, tree {tree_h} px")
check("8 rows still exceeds the devices present, so nothing is hidden",
      len(panel.tree.get_children()) <= 8,
      f"{len(panel.tree.get_children())} rows")
check(f"BtPanel is within its {BT_PANEL_MAX_H}px budget",
      panel.winfo_reqheight() <= BT_PANEL_MAX_H,
      f"{panel.winfo_reqheight()} px")
check(f"the arrangement canvas is within its {CANVAS_FIT_MAX_H}px budget",
      fit <= CANVAS_FIT_MAX_H, f"{fit} px")

root.destroy()
shutil.rmtree(SCRATCH, ignore_errors=True)
print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
