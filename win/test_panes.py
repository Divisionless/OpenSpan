"""One pane at a time: the rail, and the window height that follows it.

Doug, 2 August: *"the app is still showing too much information at once i think
-- how can we get this thing to be a reasonable size on 1080p? it demands too
much -- consider InputDirector interface for ideas"*

Input Director's shape is a narrow labelled rail, ONE pane visible beside it,
and a small persistent header — so the window's height is the TALLEST PANE
rather than the SUM of every section. This window was the sum: two columns,
every panel packed at once, 1136 x 1054 on a 1040px work area.

Four things about that change can break something already shipped, and each of
them is checked here.

  * THE PANES MUST BE BUILT ONCE AND HIDDEN, never destroyed and never built
    lazily. Tk has no reparent operation, and two of these are SERVICE OBJECTS
    as much as they are panels: BtPanel._radios gates the radio check on every
    device card, BtPanel._connected_names feeds the headphones line, _poll calls
    bt_panel.refresh(quiet=True) every fifth tick, and MultiArrangeCanvas owns
    the desk config that six calls a tick read. A pane that existed only while
    you were looking at it would take those with it.

  * EVERY ALWAYS-VISIBLE SURFACE HAS TO LEAVE THE PANES. The readiness banner
    lived in the Audio & status panel, which becomes the Bluetooth pane — so
    under a rail it would vanish whenever any other pane was showing. That is
    the W4 fatal verbatim (the banner was then inside the console frame,
    constructed and never mapped, and the default window could not say whether
    the bridge was up). One banner, in the pinned header, one writer.

    That claim used to be checked STRUCTURALLY — "the master of self.ready_lbl
    is the local named `full`" — which is a check on the source, not on the
    window. It is proved by WALKING THE TREE per pane now, and it is asked of
    every always-visible surface rather than the banner alone, with a negative
    control that puts a probe label inside a pane and requires the same walk to
    catch it.

  * THE HEADER MUST NOT STARVE. The indicator row does not scroll and does not
    wrap, and Tk's packer does not shrink an overflowing child — it clips and
    then drops the LAST-packed one. At the app's minimum width the widest
    honest row overflows, so what the row is willing to lose is decided by pack
    order and by nothing else. Measured here, at 940px, per INDICATOR_ORDER.

  * MINSIZE HAS TO MOVE IN BOTH DIRECTIONS. Left at the tallest pane's height,
    the window could never be shrunk to a short one — which is the same
    "cannot be resized to fit" failure window_height_plan exists to prevent,
    moved up one level.

  * `bridge` MUST HAVE EXACTLY ONE EXPANDING CHILD. Not "the spacer expands" —
    that was always the consequence, not the rule. The console is a log and
    vertical room is the whole point of it, so while the console is showing it
    is the expanding child and the spacer stands down. One, either way.

The AST half runs with no Tk at all. The live half builds a withdrawn root and
drives the SHIPPED select_pane / _rederive_height / _toggle_console against real
pane frames, a real MultiArrangeCanvas and a real BtPanel. App(root) is never
constructed — that starts the VM — so the app under test is App.__new__ with the
widgets those three methods actually touch.

Nothing here reads or writes anything the running app owns.

Exit 0 = all pass.
"""
import ast
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import font as tkfont
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
SCRATCH = tempfile.mkdtemp(prefix="openspan-panes-")
A.CONFIG = os.path.join(SCRATCH, "live.json")
A.PROFILE_DIR = os.path.join(SCRATCH, "profiles")
A.BT_PREFS = os.path.join(SCRATCH, "bt_prefs.json")
A.SETTINGS = os.path.join(SCRATCH, "settings.json")

with open(os.path.join(HERE, "openspan.py"), encoding="utf-8") as handle:
    SOURCE = handle.read()
MODULE = ast.parse(SOURCE, filename="openspan.py")


def _name(node):
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
    """(parent, packs) for one function body — the same reading
    test_layout_budget.py does, so both files agree on what a 'child' is."""
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


INIT = _method("App", "__init__")
SELECT = _method("App", "select_pane")
REDERIVE = _method("App", "_rederive_height")
AUDIO = _method("App", "_build_audio_panel")
check("App.select_pane exists", SELECT is not None)
check("App._rederive_height exists", REDERIVE is not None)
PARENT, PACKS = _layout(INIT)


def children_of(master):
    return sorted(w for w, m in PARENT.items() if m == master)


def expands(widget):
    return bool(PACKS.get(widget, {}).get("expand"))


PANE_LOCALS = ("pane_desk", "pane_devices", "pane_bluetooth", "pane_system",
               "pane_console")


# ===========================================================================
# (a) every pane is CONSTRUCTED at startup, whichever one is showing
# ===========================================================================
print("\n---- (a) built once, hidden — never destroyed, never lazy ----")

for local in PANE_LOCALS:
    check(f"`{local}` is constructed in App.__init__, as a child of `bridge`",
          PARENT.get(local) == "bridge", str(PARENT.get(local)))
check("all five panes are registered in self._panes",
      sorted(A.PANE_KEYS) == sorted(("desk", "devices", "bluetooth", "system",
                                     "console")),
      str(A.PANE_KEYS))

# The two service objects, and the claim that matters about them: they are
# built unconditionally in __init__, INSIDE a pane, at module-statement depth --
# not inside an `if`, not inside select_pane, not inside a builder that only
# runs when a pane is first shown.
check("self.bt_panel is constructed inside the Bluetooth pane",
      PARENT.get("self.bt_panel") == "pane_bluetooth",
      str(PARENT.get("self.bt_panel")))
check("self.canvas is constructed inside the Desk pane (via arr_wrap)",
      PARENT.get("self.canvas") == "arr_wrap"
      and PARENT.get("arr_wrap") == "pane_desk",
      f"canvas -> {PARENT.get('self.canvas')}, "
      f"arr_wrap -> {PARENT.get('arr_wrap')}")

conditional = []
for node in ast.walk(INIT):
    if not isinstance(node, (ast.If, ast.For, ast.While, ast.Try)):
        continue
    for inner in ast.walk(node):
        if (isinstance(inner, ast.Assign) and len(inner.targets) == 1
                and _name(inner.targets[0]) in ("self.bt_panel", "self.canvas")):
            conditional.append(_name(inner.targets[0]))
check("neither service object is built under a condition — they exist for "
      "every start, on every pane",
      not conditional, str(conditional))

# ...and nothing builds or destroys a pane on a switch.
select_src = ast.unparse(SELECT) if SELECT else ""
check("select_pane constructs no widget — the panes it shows already exist",
      not any(word in select_src for word in ("tk.Frame(", "tk.Label(",
                                              "BtPanel(", "ttk.")),
      select_src[:200])
check("select_pane hides with pack_forget and never destroy()",
      "pack_forget" in select_src and ".destroy(" not in select_src)
destroys = [n.lineno for n in ast.walk(MODULE)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "destroy"
            and (_name(n.func.value) or "").startswith(
                ("self.bt_panel", "self.canvas", "self._panes"))]
check("nothing anywhere destroys a pane, the canvas or the Bluetooth panel",
      not destroys, str(destroys))


# ===========================================================================
# (b) exactly ONE pane is packed at a time, and it lands above the spacer
# ===========================================================================
print("\n---- (b) one pane, and it packs BEFORE the spacer ----")

check("select_pane packs the chosen pane before the designated spacer — a "
      "pane selected later in the session must not land UNDER it",
      "before=self._bridge_spacer" in select_src)
# The pane's expand flag is no longer a literal. It is PANE_EXPANDS, and
# select_pane moves the spacer's flag against it so the count stays one; both
# halves are driven live further down.
check("PANE_EXPANDS names the panes whose content grows with the window",
      isinstance(getattr(A, "PANE_EXPANDS", None), tuple)
      and set(A.PANE_EXPANDS) <= set(A.PANE_KEYS),
      str(getattr(A, "PANE_EXPANDS", None)))
check("...and it is the CONSOLE — the one pane in the app that is a log rather "
      "than a stack of controls, so vertical room is the whole point of it",
      A.PANE_EXPANDS == ("console",), str(A.PANE_EXPANDS))
check("select_pane sets the pane's expand flag from PANE_EXPANDS, not from a "
      "literal", "PANE_EXPANDS" in select_src)
check("...and moves the spacer's flag against it, so `bridge` is never left "
      "with two sponges or none",
      "self._bridge_spacer.pack_configure" in select_src, select_src[:600])
for local in PANE_LOCALS:
    check(f"`{local}` is not packed in __init__ at all — select_pane owns that",
          local not in PACKS, str(PACKS.get(local)))


# ===========================================================================
# (c) ONE readiness banner, ONE writer
#
# This section is now only the half a tree walk cannot answer: how many of the
# label there are and how many things paint it. WHERE it ends up is proved by
# walking the live tree, per pane, down in the Tk half — a source read would
# still go green if the banner were moved into a pane by some route this
# reading did not model, and that is exactly the failure that cost W4 a wave.
# ===========================================================================
print("\n---- (c) one readiness banner, one writer ----")

assigns = [n for n in ast.walk(MODULE)
           if isinstance(n, ast.Assign) and len(n.targets) == 1
           and _name(n.targets[0]) == "self.ready_lbl"]
check("self.ready_lbl is assigned exactly once in the whole file",
      len(assigns) == 1, f"{len(assigns)} assignments")
check("...and that assignment is in App.__init__, not in a pane builder",
      bool(assigns) and assigns[0] in set(ast.walk(INIT)))
check("_build_audio_panel — which IS the Bluetooth pane now — no longer "
      "builds it",
      "ready_lbl" not in ast.unparse(AUDIO))
writers = [n for n in ast.walk(MODULE)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
           and n.func.attr in ("config", "configure")
           and _name(n.func.value) == "self.ready_lbl"]
check("exactly one writer paints it", len(writers) == 1,
      f"{len(writers)} writers at lines "
      f"{[n.lineno for n in writers]}")
check("...and that writer is _apply_poll",
      bool(writers) and writers[0] in set(ast.walk(_method("App",
                                                           "_apply_poll"))))

# THE OTHER SURFACES WITH THE SAME EXPOSURE. Anything that must be readable
# whichever pane is showing has to be a child of `full`, above the cavity.
# These are the four, and each is checked live below as well:
#
#   head       the title bar — window controls and the Console button
#   indrow     the seven status tokens
#   ready_lbl  the readiness banner
#   status     the transient call-to-action line
#
# self.c_buds (headphones) and self.sys_status (daemon roll-up) are deliberately
# NOT in that set: both are pane-scoped detail with an always-visible
# counterpart in the row above — `audio ●` for one, `devices N/M` for the other.
for _widget, _why in (("head", "the title bar"),
                      ("indrow", "the status tokens"),
                      ("self.ready_lbl", "the readiness banner"),
                      ("main", "the pane cavity itself")):
    check(f"`{_widget}` ({_why}) is a child of `full`, not of a pane",
          PARENT.get(_widget) == "full", str(PARENT.get(_widget)))
check("the banner is packed into `full`, above the pane cavity",
      PACKS.get("self.ready_lbl", {}).get("fill") == "x"
      and not expands("self.ready_lbl"),
      str(PACKS.get("self.ready_lbl")))
check("no pane frame is ever a master of an always-visible surface — every "
      "child of a pane is pane content",
      not [w for w, m in PARENT.items()
           if m in PANE_LOCALS and w in ("self.ready_lbl", "indrow", "head")],
      str([w for w, m in PARENT.items() if m in PANE_LOCALS]))


# ===========================================================================
# (d) a switch re-derives BOTH numbers — the policy half
# ===========================================================================
print("\n---- (d) height AND minsize are re-derived on every switch ----")

rederive_src = ast.unparse(REDERIVE) if REDERIVE else ""
check("select_pane calls _rederive_height", "_rederive_height" in select_src)
check("_rederive_height measures the built content again",
      "winfo_reqheight" in rederive_src)
check("_rederive_height sets minsize", ".minsize(" in rederive_src)
check("_rederive_height sets geometry", ".geometry(" in rederive_src)
check("minsize is set BEFORE geometry — Tk clamps geometry() to the current "
      "minsize, so lowering the window without lowering the floor first is a "
      "no-op",
      rederive_src.index(".minsize(") < rederive_src.index(".geometry("))
check("minsize width stays the literal 940 there too", "minsize(940" in
      rederive_src)

# The arithmetic, driven over a range rather than at one point.
check("pane_window_plan is a real function",
      callable(getattr(A, "pane_window_plan", None)))
tall = A.pane_window_plan(1000, avail_h=1040)
short = A.pane_window_plan(180, avail_h=1040)
check("a shorter pane gets a SHORTER minsize — the window can be shrunk to it",
      short[1] < tall[1], f"{short[1]} vs {tall[1]}")
check("...and a shorter geometry with it", short[0] < tall[0],
      f"{short[0]} vs {tall[0]}")
worst = None
for content in list(range(1, 1400, 29)) + [1040, 1041, 2120]:
    geom_h, min_h, _over, _clip = A.pane_window_plan(content, avail_h=1040)
    if min_h != geom_h or min_h > 1040:
        worst = (content, geom_h, min_h)
        break
check("minsize and geometry never disagree, and neither outranks the screen",
      worst is None, f"first violation: {worst}")
check("the screen still outranks the content",
      A.pane_window_plan(1400, avail_h=1040)[0] == 1040
      and A.pane_window_plan(1400, avail_h=1040)[3] is True)
check("a short pane is floored, not shrunk to nothing — FrameModal clamps its "
      "card to the window height and the tallest dialog asks for 420px",
      A.pane_window_plan(180, avail_h=1040)[0] == A.PANE_MIN_WINDOW_H
      and A.PANE_MIN_WINDOW_H >= 460, str(A.PANE_MIN_WINDOW_H))
check("...but the floor never exceeds the screen either",
      A.pane_window_plan(180, avail_h=400)[0] <= 400,
      str(A.pane_window_plan(180, avail_h=400)))
check("the clipped warning survives the floor",
      A.pane_window_plan(1263, avail_h=1040)[3] is True
      and A.pane_window_plan(900, avail_h=1040)[3] is False)


# ===========================================================================
# (e) the persisted pane round-trips, and bad values do not raise
# ===========================================================================
print("\n---- (e) the app reopens on the pane he was last using ----")

for key in A.PANE_KEYS:
    A.save_setting("last_pane", key)
    check(f"'{key}' round-trips through openspan_settings.json",
          A.load_last_pane() == key, A.load_last_pane())

for junk in ("nope", "", None, 17, {"a": 1}, ["desk"], True):
    A.save_setting("last_pane", junk)
    got = A.load_last_pane()
    check(f"an unknown value {junk!r} falls back to the default, without "
          f"raising", got == A.DEFAULT_PANE, repr(got))
check("the default is itself a real pane", A.DEFAULT_PANE in A.PANE_KEYS)

os.remove(A.SETTINGS)
check("a MISSING settings file falls back rather than raising",
      A.load_last_pane() == A.DEFAULT_PANE)
with open(A.SETTINGS, "w", encoding="utf-8") as handle:
    handle.write("{ this is not json")
check("a CORRUPT settings file falls back rather than raising",
      A.load_last_pane() == A.DEFAULT_PANE)
os.remove(A.SETTINGS)
check("an explicit default is honoured",
      A.load_last_pane("console") == "console")
A.save_setting("last_pane", "system")
check("...and ignored once a real value is stored",
      A.load_last_pane("console") == "system")

# save_setting must not destroy the rest of the file on the way past.
A.save_setting("scroll_invert", True)
A.save_setting("last_pane", "devices")
with open(A.SETTINGS, encoding="utf-8") as handle:
    stored = json.load(handle)
check("persisting the pane leaves every other setting alone",
      stored.get("scroll_invert") is True and stored.get("last_pane")
      == "devices", str(stored))


# ===========================================================================
# (f) nothing in the height-binding chain expands but the spacer
# ===========================================================================
print("\n---- (f) the sponge is still the one frame that draws nothing ----")

CHAIN = (("full", "main"), ("main", "bridge_col"), ("bridge_col", "bridge"),
         ("bridge", "self._bridge_spacer"))
for parent, expected in CHAIN:
    expanding = [w for w in children_of(parent) if expands(w)]
    check(f"the only expanding child of `{parent}` is `{expected}` as built",
          expanding == [expected],
          f"expanding: {expanding}   of: {children_of(parent)}")
# ...and it stays ONE once select_pane starts moving it around. That half is a
# runtime property, not a source one, so it is driven live below.
check("the rail is in `main` and does NOT expand — it is navigation, not a "
      "sponge",
      PARENT.get("rail") == "main" and not expands("rail"),
      str(PACKS.get("rail")))
check("the rail's height is not propagated away either, so the window can "
      "never be sized shorter than the control that navigates it",
      "rail.pack_propagate" not in SOURCE)


# ===========================================================================
# (g) the rail is a control, and it looks like one
# ===========================================================================
print("\n---- (g) hover, selection and press are three different things ----")

# The bug this closes: inactive items shipped with activebackground=CARD, and
# CARD is what select_pane paints the SELECTED item. Hovering an inactive pane
# therefore painted it the exact colour of the pane you were already on, and
# the only thing left telling them apart was a 3px bar.
check("the rail's four values are all declared",
      all(isinstance(getattr(A, n, None), str)
          for n in ("RAIL_REST", "RAIL_LIVE", "RAIL_HOVER", "RAIL_PRESS")))
check("hover is distinct from resting AND from selected — that is the whole "
      "point of it",
      A.RAIL_HOVER not in (A.RAIL_REST, A.RAIL_LIVE),
      f"rest {A.RAIL_REST} live {A.RAIL_LIVE} hover {A.RAIL_HOVER}")
check("pressed is distinct from hover, so a click moves something",
      A.RAIL_PRESS != A.RAIL_HOVER, f"{A.RAIL_PRESS} vs {A.RAIL_HOVER}")
check("resting and selected stay distinct too",
      A.RAIL_REST != A.RAIL_LIVE)
check("the values reuse the established button ramp rather than inventing one "
      "— hover is TButton's 'active', pressed is PRESS",
      A.RAIL_PRESS == A.PRESS and A.RAIL_LIVE == A.CARD
      and A.RAIL_REST == A.PANEL)
init_src = ast.unparse(INIT)
check("the rail items are built with activebackground=RAIL_HOVER, not CARD",
      "activebackground=RAIL_HOVER" in init_src)
check("...and no rail item is left on activebackground=CARD",
      "activebackground=CARD" not in init_src, "still present in __init__")
check("the rail items carry PRESSED feedback of their own — they are raw "
      "tk.Button, so no ttk style map reaches them and a flat-relief press "
      "moves nothing without this",
      "RAIL_PRESS" in init_src and "'<ButtonPress-1>'" in init_src
      and "'<ButtonRelease-1>'" in init_src)
check("select_pane paints the rail from those names, not from raw palette "
      "entries",
      "RAIL_LIVE" in select_src and "RAIL_REST" in select_src, select_src[:600])


# ===========================================================================
# Tk from here down — the SHIPPED methods, driven
# ===========================================================================
print("\n---- the shipped select_pane, driven on real widgets ----")

A.BtPanel._radio_usb_check = lambda self: None
A.BtPanel.refresh = lambda self, quiet=False: None

root = tk.Tk()
root.withdraw()            # never draw on the desk this is being run from
style = ttk.Style()
try:
    style.theme_use("clam")
except tk.TclError:
    pass

# The header is built the way App.__init__ builds it — title bar, token row,
# readiness banner, transient line — because the walk below asks of all four
# what W4 only ever asked of the banner.
full = tk.Frame(root, bg=A.BG)
full.pack(fill="both", expand=True)
head = tk.Frame(full, bg=A.BG)
head.pack(fill="x")
cons_btn = ttk.Button(head, text="▸  Console")
cons_btn.pack(side="right")
indrow = tk.Frame(full, bg=A.BG)
indrow.pack(fill="x")
ind = {}
for tok in A.INDICATOR_ORDER:
    lbl = tk.Label(indrow, text=tok, bg=A.BG, fg=A.MUTED, font=("Consolas", 10))
    lbl.pack(side="left", padx=(0, 14))
    ind[tok] = lbl
header = tk.Label(full, text="●  READY", bg=A.BG, fg=A.ACCENT)
header.pack(fill="x")
status_lbl = tk.Label(full, text="Checking…", bg=A.BG, fg=A.ACCENT)
status_lbl.pack(fill="x")
main = tk.Frame(full, bg=A.BG)
main.pack(fill="both", expand=True)
rail = tk.Frame(main, bg=A.PANEL)
rail.pack(side="left", fill="y")
bridge = tk.Frame(main, bg=A.BG)
bridge.pack(side="left", fill="both", expand=True)

app = A.App.__new__(A.App)          # never App(root): that starts the VM
app.root = root
app._full = full
app._pane = None
app._prev_pane = None
app._clip_warned = set()
app.ready_lbl = header
app._ind = ind
app._cons_btn = cons_btn

pane_desk = tk.Frame(bridge, bg=A.BG)
arr_wrap = tk.Frame(pane_desk, bg=A.CARD)
arr_wrap.pack(fill="both", expand=False)
app.canvas = A.MultiArrangeCanvas(arr_wrap, on_change=None, height=270)
app.canvas.pack(fill="both", expand=False)
pane_devices = tk.Frame(bridge, bg=A.BG, width=300, height=100)
pane_bluetooth = tk.Frame(bridge, bg=A.BG)
app.bt_panel = A.BtPanel(pane_bluetooth, app=None)
app.bt_panel.pack(fill="both", expand=False)
pane_system = tk.Frame(bridge, bg=A.BG, width=300, height=300)
pane_console = tk.Frame(bridge, bg=A.PANEL, width=300, height=380)
app._panes = {"desk": pane_desk, "devices": pane_devices,
              "bluetooth": pane_bluetooth, "system": pane_system,
              "console": pane_console}
app._rail = {}
for key, label in A.PANE_SPEC:
    row = tk.Frame(rail, bg=A.PANEL)
    row.pack(fill="x")
    bar = tk.Frame(row, bg=A.PANEL, width=3)
    bar.pack(side="left", fill="y")
    btn = tk.Button(row, text=label, bg=A.PANEL, fg=A.MUTED, bd=0,
                    relief="flat", anchor="w", width=13)
    btn.pack(side="left", fill="x", expand=True)
    app._rail[key] = (bar, btn)
app._bridge_spacer = tk.Frame(bridge, bg=A.BG, height=0)
app._bridge_spacer.pack(fill="both", expand=True)
root.update_idletasks()

# ---- (a) live: the hidden panes are still whole -----------------------------
sizes = {}
for key in A.PANE_KEYS:
    app.select_pane(key)
    root.update_idletasks()
    packed = [w for w in bridge.pack_slaves() if w is not app._bridge_spacer]
    check(f"'{key}': exactly one pane is packed",
          packed == [app._panes[key]],
          f"packed: {[str(w) for w in packed]}")
    check(f"'{key}': the spacer is still the LAST slave, so the pane sits "
          f"above it",
          bridge.pack_slaves()[-1] is app._bridge_spacer,
          str([str(w) for w in bridge.pack_slaves()]))
    alive = [k for k, f in app._panes.items() if not f.winfo_exists()]
    check(f"'{key}': every other pane still EXISTS while hidden", not alive,
          str(alive))
    check(f"'{key}': BtPanel survives being hidden — it is a service object, "
          f"not just a panel",
          app.bt_panel.winfo_exists()
          and app.bt_panel.tree.winfo_exists()
          and isinstance(app.bt_panel._radios, list),
          f"exists={bool(app.bt_panel.winfo_exists())}")
    check(f"'{key}': MultiArrangeCanvas survives being hidden — six calls a "
          f"tick go through it",
          app.canvas.winfo_exists() and app.canvas.config is not None
          and isinstance(app.canvas.devices(), list))
    sizes[key] = root.minsize()

# ---- (c) live: WALK THE TREE, per pane --------------------------------------
# The structural version of this check read the source and asked what master
# `self.ready_lbl` was constructed against. That is not the claim. The claim is
# that the banner is ON SCREEN whichever pane is showing, and the only way to
# settle it is to walk what is actually there.
#
# winfo_ismapped() is the obvious observable and is useless here: the root is
# withdrawn so this test never draws on the desk it runs from, and a withdrawn
# toplevel maps nothing at all — every widget in the tree reports 0. Walking the
# geometry managers' own slave lists is the same claim without that dependency.
# A pack_forget'd pane is not among its master's slaves, so it and everything
# under it drop out of this walk exactly as they drop off the window.
def managed_tree(widget):
    """Every widget reachable from `widget` through the geometry managers."""
    seen, out, stack = set(), [], [widget]
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        out.append(node)
        kids = list(node.pack_slaves())
        for kid in list(node.grid_slaves()) + list(node.place_slaves()):
            if kid not in kids:
                kids.append(kid)
        stack.extend(kids)
    return out


# The surfaces that must be readable whichever pane is showing.
ALWAYS_VISIBLE = [("the readiness banner", header),
                  ("the transient status line", status_lbl),
                  ("the title bar", head),
                  ("the title bar's Console button", cons_btn)]
ALWAYS_VISIBLE += [(f"the '{tok}' status token", ind[tok])
                   for tok in A.INDICATOR_ORDER]

# The negative control, and the reason it is here: the structural version of
# this check went green whether or not the banner was in a pane, which is
# exactly the failure mode W4 shipped. A probe inside a pane must make the walk
# fail, or the walk is not measuring anything.
probe = tk.Label(pane_system, text="probe", bg=A.BG)
probe.pack()

for key in A.PANE_KEYS:
    app.select_pane(key)
    root.update_idletasks()
    tree = managed_tree(full)
    missing = [what for what, widget in ALWAYS_VISIBLE if widget not in tree]
    check(f"'{key}': every always-visible surface is still in the tree",
          not missing, str(missing))
    check(f"'{key}': NEGATIVE CONTROL — a widget packed inside a pane is "
          f"visible on that pane and nowhere else, so this walk really can "
          f"tell the difference",
          (probe in tree) == (key == "system"),
          f"probe in tree: {probe in tree}")
probe.destroy()

# ---- (f) live: `bridge` keeps EXACTLY ONE expanding child -------------------
# Not "the spacer expands" — that was the consequence, never the rule. The
# console is a log and vertical room is the entire point of it, so while it is
# showing it takes the surplus and the spacer stands down. Two expanding
# children would split the surplus between them and the console would grow by
# half of what the window grew by.
def expands_live(widget):
    """Is this widget currently packed AND expanding? A pack_forget'd pane is
    not expanding in any sense the surplus can reach, and pack_info() on one
    raises rather than answering."""
    try:
        info = widget.pack_info()
    except tk.TclError:
        return False
    return str(info.get("expand", "0")) in ("1", "True", "true")


for key in A.PANE_KEYS:
    app.select_pane(key)
    root.update_idletasks()
    growing = [w for w in bridge.pack_slaves() if expands_live(w)]
    check(f"'{key}': `bridge` has exactly one expanding child",
          len(growing) == 1, f"{[str(w) for w in growing]}")
    wanted = (app._panes[key] if key in A.PANE_EXPANDS
              else app._bridge_spacer)
    check(f"'{key}': ...and it is "
          f"{'the pane itself' if key in A.PANE_EXPANDS else 'the spacer'}",
          growing == [wanted], f"{[str(w) for w in growing]}")
check("the console pane is the one that grows — every dragged pixel reaches "
      "the log rather than a frame that draws nothing",
      "console" in A.PANE_EXPANDS)

# The tick's real writes, made while a pane is hidden. This is constraint 2:
# _apply_poll and _apply_device_rows repaint on a tick whether or not their
# pane is visible, and a fault there aborts every surface below it.
app.select_pane("devices")
root.update_idletasks()
try:
    app.bt_panel.info.set("hidden write")
    app.bt_panel.btn_scan.state(["disabled"])
    app.bt_panel.btn_scan.state(["!disabled"])
    app.c_buds = tk.Label(pane_bluetooth, text="", bg=A.BG)
    app.c_buds.config(text="🎧  hidden write", fg=A.ACCENT)
    app.canvas.set_ipad_state(True, False, True)
    app.canvas.set_ipad_state(False, False, False)
    hidden_ok, hidden_err = True, ""
except Exception as exc:  # noqa: BLE001
    hidden_ok, hidden_err = False, repr(exc)
check("repainting a HIDDEN pane's widgets raises nothing — including the "
      "canvas state writes, which reach redraw() and winfo_ geometry",
      hidden_ok, hidden_err)

# ---- (d) live: minsize really moves, and downwards ---------------------------
check("the Bluetooth pane (tallest) gets the largest minsize",
      sizes["bluetooth"][1] == max(v[1] for v in sizes.values()),
      str({k: v[1] for k, v in sizes.items()}))
check("switching to the shortest pane brings minsize DOWN",
      sizes["devices"][1] < sizes["bluetooth"][1],
      f"devices {sizes['devices'][1]} vs bluetooth {sizes['bluetooth'][1]}")
check("...and the width half of minsize never moves off 940",
      {v[0] for v in sizes.values()} == {940}, str(sizes))
app.select_pane("bluetooth")
tall_min = root.minsize()[1]
app.select_pane("devices")
short_min = root.minsize()[1]
app.select_pane("bluetooth")
check("the move is repeatable in both directions, not a one-way ratchet",
      short_min < tall_min and root.minsize()[1] == tall_min,
      f"{tall_min} -> {short_min} -> {root.minsize()[1]}")

# ---- the rail says which pane is live ---------------------------------------
app.select_pane("system")
live_bar, live_btn = app._rail["system"]
other_bar, other_btn = app._rail["desk"]
check("the rail marks the active pane, and only it",
      live_bar.cget("bg") == A.ACCENT and other_bar.cget("bg") == A.PANEL
      and live_btn.cget("bg") == A.CARD and other_btn.cget("bg") == A.PANEL,
      f"{live_bar.cget('bg')} / {other_bar.cget('bg')}")
check("every rail item is labelled in WORDS, not a glyph alone",
      all(any(ch.isalpha() for ch in label) for _k, label in A.PANE_SPEC),
      str(A.PANE_SPEC))

# ---- the console button still works, and now selects the pane ---------------
app.select_pane("desk")
check("the Console button reads ▸ while you are somewhere else",
      app._cons_btn.cget("text").startswith("▸"), app._cons_btn.cget("text"))
app._toggle_console()
check("the title-bar Console button selects the console pane",
      app._pane == "console", app._pane)
check("...and the button FLIPS to ◂ — it is a toggle, and with two controls "
      "driving one pane the button has to say which way it goes",
      app._cons_btn.cget("text").startswith("◂"), app._cons_btn.cget("text"))
app._toggle_console()
check("...and a second press comes back to where you were",
      app._pane == "desk", app._pane)
check("...with the caret back to ▸",
      app._cons_btn.cget("text").startswith("▸"), app._cons_btn.cget("text"))
_btn_font = tkfont.Font(family="Segoe UI", size=10)
check("the two states are the same PIXEL width in the button's own font, so "
      "the title bar cannot reflow as panes change",
      _btn_font.measure("▸  Console") == _btn_font.measure("◂  Console"),
      f"{_btn_font.measure('▸  Console')}px vs "
      f"{_btn_font.measure('◂  Console')}px")
# The button's label is written by select_pane, so REACHING the console any
# other way keeps it honest. It used to be frozen at ▸ on every pane.
app.select_pane("console")
check("selecting Console from the RAIL flips the button too — one writer, and "
      "the rail and the button cannot disagree",
      app._cons_btn.cget("text").startswith("◂"), app._cons_btn.cget("text"))

# ---- "back" means the last pane you were actually on ------------------------
# _prev_pane used to be written ONLY by _toggle_console, so arriving at the
# console from the rail left it stale and the button returned you to whichever
# pane you last pressed the BUTTON from — possibly an hour earlier.
app.select_pane("bluetooth")
app.select_pane("console")          # from the RAIL, not the button
app._toggle_console()
check("the Console button returns to the pane you came from even when you "
      "reached the console from the rail",
      app._pane == "bluetooth", app._pane)
app.select_pane("system")
app.select_pane("devices")
app.select_pane("console")
app._toggle_console()
check("...and it tracks the LATEST pane, not the first one of the session",
      app._pane == "devices", app._pane)
check("_prev_pane never records the console itself — 'back' from the console "
      "to the console is not a thing",
      app._prev_pane != "console", str(app._prev_pane))

# ---- a fault in the height re-derivation is REPORTED, not swallowed ---------
# W7 deleted a bare `except Exception: pass` from _apply_poll because the wrap
# made the fault invisible while everything below it kept running. The same
# shape was still sitting at the bottom of _rederive_height: the pane would
# switch, the window would silently keep the PREVIOUS pane's height, and
# nothing anywhere would say why.
app.select_pane("desk")
emitted = []
real_emit, real_stderr = A._emit, sys.stderr
A._emit = lambda kind, text: emitted.append((kind, text))
sys.stderr = io.StringIO()
kept_full = app._full
app._full = None                    # -> AttributeError inside the measurement
try:
    app._rederive_height()
    escaped = False
except Exception:  # noqa: BLE001
    escaped = True
finally:
    stderr_text = sys.stderr.getvalue()
    sys.stderr = real_stderr
    A._emit = real_emit
    app._full = kept_full
check("a fault inside _rederive_height does not escape into the rail's click "
      "handler — it is a Tk command, and raising abandons the switch half done",
      not escaped)
check("...but it is REPORTED: one line to the console, the way _drain_ui "
      "reports what it swallows",
      any(kind == "err" and "re-derived" in text for kind, text in emitted),
      str(emitted))
check("...and the full traceback goes to stderr",
      "Traceback" in stderr_text and "AttributeError" in stderr_text,
      stderr_text[-160:])
check("the reporting is capped, so a fault in the reporting path cannot loop",
      isinstance(A.App.UI_FAULT_REPORTS, int) and A.App.UI_FAULT_REPORTS > 0)
app.select_pane("devices")
check("...and a clean re-derivation RE-ARMS it, so the cap is not a lifetime "
      "silence",
      app._rederive_faults == 0, str(app._rederive_faults))

# ---- an unknown pane key cannot strand the window ---------------------------
app.select_pane("no-such-pane")
check("an unknown pane key falls back to the default rather than leaving the "
      "cavity empty",
      app._pane == A.DEFAULT_PANE
      and [w for w in bridge.pack_slaves()
           if w is not app._bridge_spacer] == [app._panes[A.DEFAULT_PANE]],
      app._pane)


# ===========================================================================
# THE HEADER, MEASURED AT THE APP'S MINIMUM WIDTH
#
# The indicator row does not scroll and does not wrap, and Tk's packer does not
# shrink an overflowing child: it hands each slave the cavity it asks for, in
# pack order, and the tail gets clipped and then dropped. So what the row is
# willing to lose is decided by pack order and by nothing else, and the only
# way to know what it loses is to lay the widest honest row out at 940px and
# look. Measured, not reasoned — the reasoning was what got the admin lamp
# clipped in the first place.
#
# WHY A PLACED FRAME. A withdrawn toplevel never delivers ConfigureNotify to
# its children, so a packed chain root -> full -> indrow propagates no size at
# all and every token reads 1x1. A frame PLACED with an absolute width does get
# arranged, and its own packer then runs for real. The cavity is reproduced
# directly: the app's minsize width, minus indrow's padx=16 on each side.
# ===========================================================================
print("\n---- the header at the 940px minimum width ----")

MIN_W = 940                 # App.minsize width, both call sites
ROW_PADX, TOK_PADX = 16, 14  # indrow.pack(padx=16); each token's trailing padx
CAVITY = MIN_W - 2 * ROW_PADX

# The widest HONEST text each token can carry: a three-device desk, the portal
# up, every daemon answering, and the app NOT elevated (which is precisely when
# the admin lamp is the one thing worth reading).
#
# bcast is the subtle one and the first draft of this dict got it wrong.
# broadcast_names() collapses to a COUNT once more than two devices are
# beaconing, so the three-device form is "3 devices" -- short. The widest the
# token can honestly be is therefore TWO devices with long names, which stays a
# join. Testing the three-device form measures a narrower row than the app can
# actually produce, which is the opposite of what a starvation test is for.
#
# The names come from the REAL config, read-only, because the row's width is a
# fact about this desk and a hardcoded pair would drift the moment a device is
# renamed. If it cannot be read (a fresh checkout), fall back to names at least
# as long as the ones shipped, so the test stays pessimistic rather than
# accidentally lenient.
try:
    with open(os.path.join(os.path.dirname(HERE), "openspan_config.json"),
              encoding="utf-8") as _cfg_handle:
        _LIVE_NAMES = [(d.get("name") or d["id"])
                       for d in json.load(_cfg_handle).get("devices", [])]
except Exception:  # noqa: BLE001
    _LIVE_NAMES = ["Managed Laptop", "Managed Mac", "iPad"]
_TWO_LONGEST = sorted(_LIVE_NAMES, key=len, reverse=True)[:2] or ["A", "B"]
WIDEST = {
    "vm":     "VM ●",
    "ipad":   "Managed Mac ● connected",
    "mac":    "devices 3/3",
    "portal": "portal ○ off",
    "audio":  "audio ●",
    "bcast":  f"📡 {A.broadcast_names(_TWO_LONGEST)} BROADCASTING",
    "admin":  "⚠ NOT ADMIN",
}

# ---- the probe must BIND to the shipped row, or it proves nothing -----------
# The measurement below builds its own row out of INDICATOR_ORDER and then
# asserts properties of INDICATOR_ORDER. That is circular unless something also
# asserts that App.__init__ actually PACKS the row from that constant.
#
# It is not hypothetical: reverting the one line `for _k in INDICATOR_ORDER:`
# back to the historical literal tuple, leaving the constant and its rationale
# comment untouched, made this whole file report ALL PASS while the shipped
# header once again dropped the admin lamp. A test that survives the removal of
# the thing it exists to protect is a rubber stamp.
_ind_loops = [
    node for node in ast.walk(INIT)
    if isinstance(node, ast.For)
    and any(isinstance(sub, ast.Subscript)
            and isinstance(sub.value, ast.Attribute)
            and sub.value.attr == "_ind"
            for sub in ast.walk(node))
]
check("App.__init__ builds the indicator row from exactly one loop",
      len(_ind_loops) == 1, f"found {len(_ind_loops)}")
check("...and that loop iterates INDICATOR_ORDER, not a literal tuple — "
      "this is the check the whole starvation fix rests on",
      bool(_ind_loops)
      and isinstance(_ind_loops[0].iter, ast.Name)
      and _ind_loops[0].iter.id == "INDICATOR_ORDER",
      ast.dump(_ind_loops[0].iter)[:120] if _ind_loops else "no loop")

probe_root = tk.Frame(root, bg=A.BG)
probe_root.place(x=ROW_PADX, y=0, width=CAVITY, height=30)
probe_root.pack_propagate(False)
tokens = {}
for tok in A.INDICATOR_ORDER:
    lbl = tk.Label(probe_root, text=WIDEST[tok], bg=A.BG, fg=A.MUTED,
                   font=("Consolas", 10))
    lbl.pack(side="left", padx=(0, TOK_PADX))
    tokens[tok] = lbl
root.update_idletasks()
check(f"the probe row really got the {CAVITY}px cavity — without this the "
      f"measurement below means nothing",
      probe_root.winfo_width() == CAVITY, str(probe_root.winfo_width()))

want = {k: w.winfo_reqwidth() + TOK_PADX for k, w in tokens.items()}
got = {k: w.winfo_width() for k, w in tokens.items()}
# A token the packer placed in full was allocated its requested width; one it
# clipped got less; one it dropped was never configured and keeps Tk's 1x1.
placed = {k: got[k] > 1 and got[k] + TOK_PADX >= want[k] for k in tokens}
print("      cavity %dpx, honest row %dpx" % (CAVITY, sum(want.values())))
for tok in A.INDICATOR_ORDER:
    print("      %-7s want %4dpx  got %4dpx  %s"
          % (tok, want[tok], got[tok],
             "PLACED" if placed[tok] else
             ("DROPPED" if got[tok] <= 1 else "CLIPPED")))

lost = [k for k in A.INDICATOR_MUST_SURVIVE if not placed[k]]
check("THE INVARIANT: at the app's minimum width every non-negotiable token is "
      "still allocated its full width",
      not lost, f"lost: {lost}")
check("...and `admin` in particular, which is the ONLY surface in this app "
      "that can explain a silently dead mouse under Windows UIPI (see "
      "is_elevated)",
      placed["admin"], f"want {want['admin']}px, got {got['admin']}px")
check("admin is packed FIRST, because pack order IS drop order here",
      A.INDICATOR_ORDER[0] == "admin", str(A.INDICATOR_ORDER))
check("bcast is packed LAST — widest token in the row and the most transient, "
      "so it is the one that should yield",
      A.INDICATOR_ORDER[-1] == "bcast", str(A.INDICATOR_ORDER))
check("the order is a permutation of the row, not a rewrite of it",
      sorted(A.INDICATOR_ORDER) == sorted(
          ("vm", "ipad", "mac", "portal", "audio", "bcast", "admin")),
      str(A.INDICATOR_ORDER))
check("every non-negotiable token is one the row actually has",
      set(A.INDICATOR_MUST_SURVIVE) <= set(A.INDICATOR_ORDER))
# Leading with the admin token costs the row nothing on the normal path: on an
# elevated run that token is empty for the whole session (is_elevated is
# resolved once and cached), so it is packed with no trailing gap and the row
# still lines up with the title above it, which shares indrow's padx=16.
check("the admin token carries no gap when it has nothing to say — otherwise "
      "leading with it would indent the whole row by 14px against the title",
      "if (_k == 'admin' and is_elevated())" in init_src
      or "_k == 'admin' and is_elevated()" in init_src, init_src[:0])

# The shortened broadcast token is what takes the whole row inside the cavity;
# without it `bcast` itself is clipped even though everything ahead of it fits.
check("broadcast_names keeps one and two devices by name",
      A.broadcast_names(["iPad"]) == "iPad"
      and A.broadcast_names(["iPad", "Mac"]) == "iPad + Mac",
      A.broadcast_names(["iPad", "Mac"]))
check("...and counts them beyond that, rather than spending a third of the "
      "row on a state that lasts seconds",
      A.broadcast_names(["a", "b", "c"]) == "3 devices",
      A.broadcast_names(["a", "b", "c"]))
# The row is NOT required to fit whole, and asserting that it does was the wrong
# claim. At the app's minimum width, with two long device names beaconing, the
# honest row wants ~932px against a 908px cavity. Widening minsize to close a
# 24px gap would trade a real constraint (the window must fit a 1080p panel) for
# a cosmetic one.
#
# What must hold is the ORDERING invariant: overflow is permitted, but only a
# YIELDABLE token may be the casualty. That is asserted above, per token, and it
# is the claim that actually protects the UIPI lamp.
#
# So this check says the honest thing instead: if the row does overflow, the
# casualties are drawn exclusively from the yieldable set.
_casualties = [t for t in A.INDICATOR_ORDER if not placed[t]]
_yieldable = [t for t in A.INDICATOR_ORDER if t not in A.INDICATOR_MUST_SURVIVE]
check("if the widest honest row overflows, only a yieldable token is cut",
      all(t in _yieldable for t in _casualties),
      f"row {sum(want.values())}px vs cavity {CAVITY}px; "
      f"casualties {_casualties}; yieldable {_yieldable}")
check("...and the yielder is the transient one, not a standing fact",
      _casualties in ([], ["bcast"]), str(_casualties))
probe_root.destroy()


# ===========================================================================
# THE SECOND PORTAL BUTTON — two surfaces, ONE writer
#
# Doug: *"Duplicate start portal button linked to same backend and place
# floating in field of Desk at bottom"*
#
# The rail put the ctl grid — and with it the Start portal button and the
# full-strength amber alarm that explains why nothing is bridging — on the
# System pane, two clicks from the Desk where the work happens.
#
# A duplicate control surface has already broken this app once: the old global
# device row kept its own paired-state, so acting through one left the other
# still showing the device as paired. The rule that came out of that is ONE
# WRITER and one builder feeding every surface, and everything below is that
# rule asserted rather than asserted-in-a-comment.
# ===========================================================================
print("\n---- the portal control, twice, with one writer behind both ----")

APP_CLASS = next(n for n in ast.walk(MODULE)
                 if isinstance(n, ast.ClassDef) and n.name == "App")


PORTAL_LABELS = {"Start portal", "Stop portal"}
PORTAL_STYLES = {"TButton", "Warn.TButton"}


def _writes_portal_text(fn):
    """Does this method write a portal button's LABEL OR ITS STYLE?

    The first version of this predicate only recognised config(text=...) whose
    value contained the literal "Stop portal", and two mutants walked straight
    past it with the whole suite green:

        self.desk_portal_btn.config(text="Start portal")      # resting label
        self.desk_portal_btn.configure(style="Warn.TButton")  # style only

    Either one leaves the two buttons permanently disagreeing, which is exactly
    the two-surfaces-one-state failure this app has already shipped once. Style
    is half of what _render_portal_button owns, so a predicate that ignores it
    is asserting half the claim while reading like the whole one.

    Both label spellings count too: matching only "Stop portal" missed a writer
    that could pin the pair to "Start portal" forever.
    """
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("config", "configure")):
            continue
        for keyword in node.keywords:
            if keyword.arg == "text":
                if any(isinstance(c, ast.Constant) and c.value in PORTAL_LABELS
                       for c in ast.walk(keyword.value)):
                    return True
            elif keyword.arg == "style":
                if any(isinstance(c, ast.Constant) and c.value in PORTAL_STYLES
                       for c in ast.walk(keyword.value)):
                    return True
    return False


_writers = [f.name for f in APP_CLASS.body
            if isinstance(f, ast.FunctionDef) and _writes_portal_text(f)]
check("EXACTLY ONE method in App writes a portal button's label — a second "
      "writer is the whole failure mode a duplicated surface risks",
      _writers == ["_render_portal_button"], str(_writers))

RENDER = _method("App", "_render_portal_button")
FACTORY = _method("App", "_portal_button")
BUSY = _method("App", "_busy_portal")
check("App._portal_button exists — the ONE builder", FACTORY is not None)
check("App._busy_portal exists — the ONE way to park a wait on the pair",
      BUSY is not None)
render_src = ast.unparse(RENDER) if RENDER else ""
factory_src = ast.unparse(FACTORY) if FACTORY else ""
busy_src = ast.unparse(BUSY) if BUSY else ""

_render_loops = [
    node for node in ast.walk(RENDER or ast.Module(body=[], type_ignores=[]))
    if isinstance(node, ast.For) and _name(node.iter) == "self._portal_btns"
]
check("the writer ITERATES the registry — it does not name a hardcoded pair",
      len(_render_loops) == 1, f"{len(_render_loops)} loops over the registry")
check("...and the loop body is what does the writing, so every registered "
      "button gets the same text and the same style",
      bool(_render_loops)
      and _writes_portal_text(_render_loops[0])
      and any(isinstance(n, ast.Call)
              and isinstance(n.func, ast.Attribute)
              and n.func.attr in ("config", "configure")
              and any(k.arg == "style" for k in n.keywords)
              for n in ast.walk(_render_loops[0])),
      render_src)
check("the writer names NEITHER button — nothing in it survives adding a third",
      "self.portal_btn" not in render_src
      and "desk_portal_btn" not in render_src, render_src)
_guards = []
for _node in ast.walk(RENDER or ast.Module(body=[], type_ignores=[])):
    if not (isinstance(_node, ast.Call) and _name(_node.func) == "any"):
        continue
    if any(isinstance(_g, ast.GeneratorExp)
           and any(_name(_c.iter) == "self._portal_btns"
                   for _c in _g.generators)
           for _g in ast.walk(_node)):
        _guards.append(_node)
check("the busy guard is ANY over the registry, not the first button's — a "
      "pair where one says 'Stopping portal…' and the other still offers "
      "'Stop portal' IS the two-surfaces-one-state bug",
      len(_guards) == 1 and "button_is_busy" in ast.unparse(_guards[0]),
      render_src[:300])

check("both buttons are built by the factory, so neither can skip registration",
      "self.portal_btn = self._portal_button(" in init_src
      and "self.desk_portal_btn = self._portal_button(" in init_src,
      "not built through _portal_button")
check("__init__ never wires a portal button to the backend itself — the "
      "command is bound in ONE place",
      "command=self.toggle_portal" not in init_src)
check("...and that one place is the factory, which registers what it builds",
      "command=self.toggle_portal" in factory_src
      and "self._portal_btns.append(" in factory_src, factory_src)
check("the wait is parked across the whole registry, from the factory's list",
      "self._portal_btns" in busy_src and "self.busy(" in busy_src, busy_src)
TOGGLE = _method("App", "toggle_portal")
toggle_src = ast.unparse(TOGGLE) if TOGGLE else ""
check("toggle_portal parks its ~8s stop through _busy_portal, not on one "
      "button",
      "self._busy_portal(" in toggle_src
      and "self.busy(self.portal_btn" not in toggle_src, toggle_src[:400])
check("the floating copy is PLACED, never packed or gridded — the placer does "
      "not propagate a size, which is how it costs the Desk pane no height",
      "self.desk_portal_btn.place(" in init_src
      and "self.desk_portal_btn.pack(" not in init_src
      and "self.desk_portal_btn.grid(" not in init_src)
check("...and it is a child of the canvas, so redraw()'s delete('all') — which "
      "removes canvas ITEMS — cannot take it, and it travels with the canvas",
      "self._portal_button(self.canvas" in init_src)

# ---- live: build the pair through the SHIPPED factory ----------------------
_recorded = []


def _recorder():
    _recorded.append(id(_recorder))


# THE GEOMETRY MUST COME OUT OF THE SOURCE, NOT OUT OF THIS FILE.
#
# The first version re-typed the placement into this file -- width=17, relx=0.5,
# rely=1.0, anchor="s", y=-8 -- and then measured that. Which measures this
# file, not the app. Proven by mutation: changing the SHIPPED rely to 0.70 puts
# the button over three screen rectangles and turns +63px of clearance into
# -85px, and every geometry check still passed, including the one titled "it
# clears the bottom-left hint line". Same escape for width=64 and anchor="sw".
#
# So the kwargs are lifted out of App.__init__ by AST. Move the button in the
# app and this probe moves with it, and the clearance is re-measured for real.
def _portal_geometry_from_source():
    factory_kw, place_kw = None, None
    for node in ast.walk(INIT):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)):
            continue
        if (node.func.attr == "_portal_button" and node.args
                and getattr(node.args[0], "attr", None) == "canvas"):
            factory_kw = {k.arg: ast.literal_eval(k.value)
                          for k in node.keywords}
        if (node.func.attr == "place"
                and getattr(node.func.value, "attr", None)
                == "desk_portal_btn"):
            place_kw = {k.arg: ast.literal_eval(k.value) for k in node.keywords}
    return factory_kw, place_kw


_factory_kw, _place_kw = _portal_geometry_from_source()
check("the shipped Desk button's geometry was read out of App.__init__ — "
      "re-typing it here would measure this file instead of the app",
      _factory_kw is not None and _place_kw is not None,
      f"factory={_factory_kw} place={_place_kw}")

app.toggle_portal = _recorder        # never the real one: it spawns the portal
app._ui_thread = threading.get_ident()
app._portal_btns = []
desk_btn = A.App._portal_button(app, app.canvas, **(_factory_kw or {}))
desk_btn.place(**(_place_kw or {}))
sys_btn = A.App._portal_button(app, pane_system)
sys_btn.pack()
app.desk_portal_btn, app.portal_btn = desk_btn, sys_btn

# ---- (a) both exist, and both are in the registry the writer drives ---------
check("(a) the factory registered BOTH buttons, in build order",
      app._portal_btns == [desk_btn, sys_btn],
      str([str(b) for b in app._portal_btns]))

# ---- (b) both agree on text AND style, in each state ------------------------
for _on, _text, _style in ((False, "Start portal", "Warn.TButton"),
                           (True, "Stop portal", "TButton")):
    A.App._render_portal_button(app, _on)
    _got = [(b.cget("text"), str(b.cget("style"))) for b in app._portal_btns]
    check(f"(b) portal {'up' if _on else 'down'}: BOTH buttons read "
          f"{_text!r} in {_style}",
          _got == [(_text, _style)] * 2, str(_got))

# ---- (c) a parked wait covers the pair, and the tick cannot paint over it ---
# One busy button must freeze the OTHER one too. Half-busy is the bug.
for _parked, _other, _which in ((desk_btn, sys_btn, "the floating Desk copy"),
                                (sys_btn, desk_btn, "the System pane copy")):
    A.App._render_portal_button(app, False)
    A.set_button_busy(_parked, "Stopping portal…")
    A.App._render_portal_button(app, True)      # the 3-second tick, mid-stop
    check(f"(c) a wait parked on {_which} freezes the WHOLE pair — the tick "
          f"repaints neither",
          _parked.cget("text") == "Stopping portal…"
          and _other.cget("text") == "Start portal",
          f"{_parked.cget('text')!r} / {_other.cget('text')!r}")
    A.clear_button_busy(_parked)

# ...and the shipped path parks it on both at once.
A.App._render_portal_button(app, True)
_done = A.App._busy_portal(app, "Stopping portal…")
check("(c) _busy_portal parks the wait on BOTH buttons and disables both",
      all(b.cget("text") == "Stopping portal…" and "disabled" in b.state()
          for b in app._portal_btns),
      str([(b.cget("text"), str(b.state())) for b in app._portal_btns]))
A.App._render_portal_button(app, False)
check("(c) ...and the tick cannot paint over either of them",
      all(b.cget("text") == "Stopping portal…" for b in app._portal_btns),
      str([b.cget("text") for b in app._portal_btns]))
_done()
check("(c) ...and one restore hands BOTH back to the renderer, re-enabled",
      all(b.cget("text") == "Stop portal" and "disabled" not in b.state()
          for b in app._portal_btns),
      str([(b.cget("text"), str(b.state())) for b in app._portal_btns]))

# ---- (d) one command, one backend -------------------------------------------
desk_btn.invoke()
sys_btn.invoke()
check("(d) both buttons invoke the SAME callable — no second code path to the "
      "backend and no second liveness check",
      _recorded == [id(_recorder), id(_recorder)], str(_recorded))

# ---- the pair survives pane switches ----------------------------------------
# A place()d child of a pack_forget'd master goes with it, so the floating copy
# has to come back when the Desk does — and the tick writes to it while it is
# hidden, which a hidden Tk widget accepts.
for key in A.PANE_KEYS:
    app.select_pane(key)
    root.update_idletasks()
    tree = managed_tree(full)
    check(f"'{key}': the floating Desk button is on screen iff the Desk is",
          (desk_btn in tree) == (key == "desk"),
          f"in tree: {desk_btn in tree}")
    check(f"'{key}': the System pane button is on screen iff System is",
          (sys_btn in tree) == (key == "system"), f"in tree: {sys_btn in tree}")
app.select_pane("console")
try:
    A.App._render_portal_button(app, False)
    _hidden_ok, _hidden_err = True, ""
except Exception as exc:  # noqa: BLE001
    _hidden_ok, _hidden_err = False, repr(exc)
check("writing to BOTH buttons while both panes are hidden raises nothing",
      _hidden_ok, _hidden_err)
app.select_pane("desk")
root.update_idletasks()
check("...and the Desk copy comes back carrying what the tick wrote while it "
      "was away, still placed on the canvas",
      desk_btn.cget("text") == "Start portal"
      and desk_btn.winfo_manager() == "place"
      and desk_btn in app.canvas.place_slaves(),
      f"{desk_btn.cget('text')!r} / {desk_btn.winfo_manager()}")

# ===========================================================================
# (e) THE FLOATING BUTTON COSTS THE DESK PANE NO HEIGHT — and covers nothing
#
# Measured, not reasoned, and at the REAL desk: the whole question is whether a
# button parked at the bottom of the canvas sits in the letterbox strip the
# aspect fit leaves, or on top of a screen rectangle the user drags.
#
# Same trick as the header measurement above: a withdrawn toplevel propagates
# no size through a packed chain, so the chain is rebuilt under a frame PLACED
# at the app's default window width and its own packer then runs for real.
# ===========================================================================
print("\n---- (e) the Desk pane at 1120px, with and without the button ----")

WIN_W = 1120                 # App.__init__: root.geometry("1120x930")
LIVE_CFG = os.path.join(os.path.dirname(HERE), "openspan_config.json")
_kept_cfg = A.CONFIG
if os.path.exists(LIVE_CFG):
    # A COPY, read-only against the original. The canvas persists on
    # construction and nothing here may touch what the running app owns.
    A.CONFIG = os.path.join(SCRATCH, "deskprobe.json")
    shutil.copyfile(LIVE_CFG, A.CONFIG)
    _real_desk = True
else:
    _real_desk = False

p_win = tk.Frame(root, bg=A.BG)
p_win.place(x=0, y=0, width=WIN_W, height=760)
p_win.pack_propagate(False)
p_main = tk.Frame(p_win, bg=A.BG)
p_main.pack(fill="both", expand=True, padx=10, pady=A.PAD_SM)
p_rail = tk.Frame(p_main, bg=A.PANEL)
p_rail.pack(side="left", fill="y", padx=(0, A.PAD_LG))
for _key, _label in A.PANE_SPEC:      # faithful: the rail's width is real
    _row = tk.Frame(p_rail, bg=A.RAIL_REST)
    _row.pack(fill="x")
    tk.Frame(_row, bg=A.RAIL_REST, width=3).pack(side="left", fill="y")
    tk.Button(_row, text=_label, bg=A.RAIL_REST, fg=A.MUTED, bd=0,
              relief="flat", anchor="w", width=13, padx=10, pady=6,
              font=("Segoe UI", 10), highlightthickness=0).pack(
        side="left", fill="x", expand=True)
p_col = tk.Frame(p_main, bg=A.BG)
p_col.pack(side="left", fill="both", expand=True)
p_bridge = tk.Frame(p_col, bg=A.BG)
p_bridge.pack(fill="both", expand=True)
p_desk = tk.Frame(p_bridge, bg=A.BG)
p_desk.pack(fill="both", expand=False)
p_arr = tk.Frame(p_desk, bg=A.CARD, bd=0)
p_arr.pack(fill="both", expand=False, padx=8, pady=A.PAD_MD)
p_canvas = A.MultiArrangeCanvas(p_arr, on_change=None, height=270)
p_canvas.pack(fill="both", expand=False, padx=8, pady=A.PAD_MD)


def _settle(limit=40):
    """Fit to a FIXED POINT before measuring anything. Returns the passes used.

    This is not belt-and-braces, it is the measurement. _fit_height sizes the
    canvas from the width it has been ALLOCATED, and under a withdrawn root a
    freshly built seven-deep chain does not reach its final width in one
    update_idletasks: the canvas reads winfo_width() == 1 for several passes,
    fits to FIT_MIN_H, and a measurement taken there is of a 1px-wide canvas
    rather than of the desk. Measured that way the pane appeared to grow 253px
    when the button was added — an artefact of comparing a settled layout
    against an unsettled one, in a direction that would have looked exactly
    like the bug this check exists to catch.

    The shipped app gets this convergence for free: <Configure> fires as the
    real window is mapped and resized, and _fit_height's own re-entry guard and
    2px tolerance stop it chasing itself. Here it has to be asked for.
    """
    last = None
    for passes in range(1, limit + 1):
        root.update_idletasks()
        p_canvas._fit_height()
        now = (p_canvas.winfo_width(), p_canvas.winfo_reqheight())
        if now == last and now[0] > 1:
            break
        last = now
    root.update_idletasks()
    p_canvas.redraw()
    return passes


# FORCE THE DEEP END OF THE CHAIN TO EXIST BEFORE MEASURING ANYTHING.
#
# Tk creates windows lazily, and under a withdrawn toplevel update_idletasks
# does not finish the job however many times it is called: measured here, the
# arrangement propagated three levels (p_win 1120 -> p_main 1100 -> p_col 972)
# and then stopped dead — p_bridge, p_desk, p_arr and the canvas all sat at 1px
# through forty passes of update_idletasks AND update().
#
# That matters because the FIRST thing to touch the canvas resolves the whole
# chain, and placing the button is such a touch (the placer asks its new master
# for real dimensions). Measure "without" on the lazy chain and "with" on the
# resolved one and the button appears to cost the pane 253px — a fabricated
# failure, in the exact direction of the bug this check exists to catch.
# winfo_id() realises the window on demand, so both measurements are taken on
# the same resolved layout and the only variable left is the button.
p_canvas.winfo_id()
_passes = _settle()
h_without = p_desk.winfo_reqheight()
check(f"the probe layout reached a fixed point ({_passes} passes) — an "
      f"unsettled first measurement is what makes a zero-cost button look "
      f"like a 253px one",
      _passes < 40 and p_canvas.winfo_width() > 1,
      f"{_passes} passes, canvas {p_canvas.winfo_width()}px wide")

_kept_registry = app._portal_btns
app._portal_btns = []
p_btn = A.App._portal_button(app, p_canvas, **(_factory_kw or {}))
p_btn.place(**(_place_kw or {}))
A.App._render_portal_button(app, False)      # the widest resting label + amber
app._portal_btns = _kept_registry
_settle()
h_with = p_desk.winfo_reqheight()

cw, chh = p_canvas.winfo_width(), p_canvas.winfo_height()
check(f"the probe canvas really got a window-sized width ({cw}px) — without "
      f"this every measurement below is meaningless",
      cw > 600 and chh > 200, f"{cw}x{chh}")
print(f"      Desk pane reqheight: without {h_without}px, with {h_with}px")
check("(e) the floating button costs the Desk pane ZERO height — place() never "
      "propagates a size to its master, which is the whole reason it is not "
      "packed",
      h_with == h_without, f"{h_without}px -> {h_with}px")

bx0, by0 = p_btn.winfo_x(), p_btn.winfo_y()
bx1 = bx0 + p_btn.winfo_width()
by1 = by0 + p_btn.winfo_height()
check("the button really is bottom-centred inside the canvas",
      abs(((bx0 + bx1) / 2) - cw / 2) <= 2 and 0 < (chh - by1) <= 10,
      f"button {bx0},{by0}-{bx1},{by1} in a {cw}x{chh} canvas")

_rects = []
for _key, _item in p_canvas._items():
    _x, _y, _w, _h = p_canvas._rect(_key, _item)
    _x0, _y0 = p_canvas.w2c(_x, _y)
    _x1, _y1 = p_canvas.w2c(_x + _w, _y + _h)
    _rects.append((_key, _x0, _y0, _x1, _y1))
_hits = [k for k, x0, y0, x1, y1 in _rects
         if x0 < bx1 and x1 > bx0 and y0 < by1 and y1 > by0]
_lowest = max((y1 for _k, _x0, _y0, _x1, y1 in _rects), default=0.0)
print(f"      {len(_rects)} screen rectangles"
      + ("" if not _real_desk else " (the LIVE desk)")
      + f"; lowest drawn edge y={_lowest:.0f}, button top y={by0}, "
        f"clearance {by0 - _lowest:.0f}px")
check("(5) the button overlaps NO screen rectangle at this desk's geometry — "
      "it sits in the letterbox strip the aspect fit leaves below the drawing",
      not _hits, f"overlaps: {_hits}")

_hint = p_canvas.bbox("hint")
check("...and it clears the bottom-left hint line the canvas draws for itself",
      _hint is None or not (_hint[0] < bx1 and _hint[2] > bx0
                            and _hint[1] < by1 and _hint[3] > by0),
      f"hint {_hint} vs button {bx0},{by0}-{bx1},{by1}")

# ...and taking it away again returns the pane to the same height, which is the
# other half of "costs nothing": a number that matched by luck at one width
# would not survive the round trip.
p_btn.place_forget()
_settle()
h_removed = p_desk.winfo_reqheight()
check("(e) removing it returns the pane to exactly the same height — the "
      "measurement is a round trip, not a coincidence",
      h_removed == h_without, f"{h_without}px -> {h_with}px -> {h_removed}px")

# The canvas's own bindings are untouched: the button intercepts clicks in its
# own footprint and nowhere else, and drag-to-arrange / hover / the W2
# right-click menu all hang off these three.
check("the canvas still answers drag-to-arrange and hover everywhere the "
      "button is not",
      all(p_canvas.bind(seq) for seq in ("<ButtonPress-1>", "<B1-Motion>",
                                         "<ButtonRelease-1>", "<Motion>")),
      str([seq for seq in ("<ButtonPress-1>", "<B1-Motion>",
                           "<ButtonRelease-1>", "<Motion>")
           if not p_canvas.bind(seq)]))
check("...and App binds the W2 right-click menu to the canvas, not to anything "
      "the button now sits on top of",
      "self.canvas.bind('<Button-3>', self._canvas_menu)" in init_src
      or 'self.canvas.bind("<Button-3>", self._canvas_menu)' in init_src)

p_win.destroy()
A.CONFIG = _kept_cfg

root.destroy()
shutil.rmtree(SCRATCH, ignore_errors=True)
print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
