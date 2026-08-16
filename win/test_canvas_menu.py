"""Right-clicking the arrangement: what each menu is allowed to offer, and
what the write path behind it must do.

Doug asked one question -- "why can't i right click a screen to change its res,
size, hz?" -- and the answer was that nothing had ever been bound to Button-3 on
the arrangement canvas, even though every ingredient already existed: _hit_key
resolves a click to (key, live config dict), _detail_lines already formats
"res x res @ Hz", physical_size already derives the desk rectangle, and
MacDisplayEditor already edits a whole device. This file is the guard rail
around wiring them together, and there are four separate ways that wiring can be
wrong in a way that nothing on screen would show you.

ONE: it can edit the wrong screen. Two shipped controls did exactly that. The
global "Rotate" button called canvas.rotate(), which only ever turned
targets[0]["displays"][0]; "Configure Mac displays..." passed no device_id, so
it resolved to the FIRST device whichever one you meant. Both are deleted rather
than relocated, and this file asserts they are gone -- a per-screen menu cannot
address the wrong rectangle, because it is opened on the right one.

TWO: it can offer a resolution that is not a resolution. The iPad's res_w/res_h
hold POINTS, not pixels -- the live config's ipad-main is 1080x810, byte
identical to IPAD_PRESETS["iPad 10.2\""] -- and every pointer distance on that
HID lane is computed from those two numbers. A generic 3840x2160 offered on that
rectangle would not change any screen; it would silently rescale the whole iPad
lane. So the resolution list is per display KIND, and that is checked here.

THREE: it can claim to change something it cannot. Windows owns a local
monitor's position, resolution, refresh rate and primary flag; this app cannot
set a Windows display mode. What Windows does NOT know is physical size, which
is why diagonal_in is typed in by hand at all. So the local menu shows the first
four as read-only facts, offers a re-read, and lets you type only the diagonal.
The re-read MERGES: a refresh that reset diagonal_in would destroy the only
field the user can supply.

FOUR: it can deadlock the window. FrameModal.grab_set records grab_current() as
_prev_grab and hands the grab back when it closes. Opened inline from a posted
menu it captures the MENU, and then returns the grab to a widget that is no
longer posted -- the whole window goes mouse-dead. Every command in these menus
is therefore deferred through App._deferred, and that is asserted both
structurally (in the source) and by actually invoking every entry with after()
recording instead of running.

App(root) starts the VM and the audio workers, so it is never constructed here.
The methods under test are taken off the real class and bound to an App-shaped
stub, so the code being exercised is the shipping code and not a copy of it. The
root is withdrawn, and the live config, profiles and Bluetooth prefs are all
redirected to a temp directory before anything is built -- MultiArrangeCanvas
persists on construction, so that redirect happens first or not at all.

Exit 0 = all pass.
"""
import ast
import json
import os
import shutil
import sys
import tempfile
import tkinter as tk
import types

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import openspan as A  # noqa: E402
import openspan_targets as T  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (
        "" if cond or not detail else "\n      " + detail))
    if not cond:
        fails.append(name)


# The real desk, captured BEFORE the redirect below, and only ever read.
LIVE_CONFIG_PATH = A.CONFIG

SCRATCH = tempfile.mkdtemp(prefix="openspan-menu-")
A.CONFIG = os.path.join(SCRATCH, "live.json")
A.PROFILE_DIR = os.path.join(SCRATCH, "profiles")
A.BT_PREFS = os.path.join(SCRATCH, "bt_prefs.json")


# ---- (b) the golden round-trip, against the real desk ----------------------
# Every rectangle on the arrangement is DERIVED: w/h is physical_size of the
# diagonal and the aspect the resolution and rotation imply. Every edit in the
# new menu re-derives it the same way. If that identity does not already hold
# for the config on this machine, then either the stored geometry or the
# derivation is wrong, and every menu edit would visibly move a screen.
with open(LIVE_CONFIG_PATH, encoding="utf-8") as handle:
    LIVE = json.load(handle)

checked = 0
mismatch = []
for monitor in LIVE.get("monitors", []):
    diagonal = monitor.get("diagonal_in")
    if not diagonal:
        continue
    checked += 1
    want = A.physical_size(diagonal, monitor["w"], monitor["h"], 0)
    got = (monitor.get("layout_w"), monitor.get("layout_h"))
    if want != got:
        mismatch.append(f"{monitor['name']}: stored {got} vs derived {want}")
for device in LIVE.get("devices", []):
    for display in device.get("displays", []):
        diagonal = display.get("diagonal_in")
        if not diagonal:
            continue
        checked += 1
        want = A.physical_size(
            diagonal, display["res_w"], display["res_h"],
            display.get("rotation", 0))
        got = (display.get("w"), display.get("h"))
        if want != got:
            mismatch.append(
                f"{device.get('name')}/{display.get('name')}: "
                f"stored {got} vs derived {want}")
check("the live config has surfaces with a diagonal to check", checked >= 4,
      f"{checked} surfaces carry diagonal_in")
check("golden round-trip: every stored rectangle IS physical_size(diagonal, "
      "raw resolution, rotation)", not mismatch, "; ".join(mismatch))

# ...and that the raw resolution is what goes in. physical_size swaps for a
# quarter turn itself, so pre-swapping would square the turn.
portrait = A.physical_size(32, 3840, 2160, 90)
check("physical_size does the rotation swap itself (32\" 4K at 90 deg is tall)",
      portrait == (1569, 2789), str(portrait))
check("passing a PRE-swapped resolution would give a different rectangle",
      A.physical_size(32, 2160, 3840, 90) != portrait,
      f"{A.physical_size(32, 2160, 3840, 90)} vs {portrait}")


# ---- the source-level claims ----------------------------------------------
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
                if (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and item.name == method_name):
                    return item
    return None


# ---- (a) a <Button-3> binding exists on the canvas -------------------------
APP_INIT = _method("App", "__init__")
check("App.__init__ found", APP_INIT is not None)
binds = [n for n in ast.walk(APP_INIT)
         if isinstance(n, ast.Call)
         and isinstance(n.func, ast.Attribute) and n.func.attr == "bind"
         and _name(n.func.value) == "self.canvas"
         and n.args and isinstance(n.args[0], ast.Constant)]
sequences = [n.args[0].value for n in binds]
check("(a) App binds <Button-3> on the arrangement canvas",
      "<Button-3>" in sequences, f"canvas binds found: {sequences}")
check("(a) it is bound to the App handler, not to something inside the canvas",
      any(_name(n.args[1]) == "self._canvas_menu"
          for n in binds if len(n.args) > 1 and n.args[0].value == "<Button-3>"))
canvas_init = _method("MultiArrangeCanvas", "__init__")
inner = [n.args[0].value for n in ast.walk(canvas_init)
         if isinstance(n, ast.Call)
         and isinstance(n.func, ast.Attribute) and n.func.attr == "bind"
         and n.args and isinstance(n.args[0], ast.Constant)]
check("the canvas itself does NOT bind Button-3 -- it keeps only its "
      "on_change contract",
      "<Button-3>" not in inner, f"canvas self-binds: {inner}")

# ---- the two wrong-screen controls are deleted, not relocated --------------
# An AST attribute access, not a substring. `"self.canvas.rotate" not in
# SOURCE` passed only because a surviving comment happens to write it as
# `canvas.rotate()` without the `self.` -- one reworded comment away from
# asserting nothing, in a file that parses the AST everywhere else precisely to
# avoid that.
_rotate_calls = [n.lineno for n in ast.walk(MODULE)
                 if isinstance(n, ast.Attribute)
                 and _name(n) == "self.canvas.rotate"]
check("the global Rotate button is gone (it only ever turned "
      "targets[0].displays[0])",
      not _rotate_calls, f"self.canvas.rotate at line(s) {_rotate_calls}")
check("MultiArrangeCanvas.rotate() itself is gone",
      not hasattr(A.MultiArrangeCanvas, "rotate"))
check("MultiArrangeCanvas.set_ipad_size() is gone with the model combobox",
      not hasattr(A.MultiArrangeCanvas, "set_ipad_size"))
check("App._pick_model is gone with it -- nothing is orphaned",
      not hasattr(A.App, "_pick_model"))
check("no MacDisplayEditor call is left without a device_id",
      all(any(k.arg == "device_id" for k in node.keywords)
          for node in ast.walk(MODULE)
          if isinstance(node, ast.Call)
          and _name(node.func) == "MacDisplayEditor"),
      "a device_id-less call resolves to the FIRST device")


def _packed_labels_in(container, scope):
    """Line numbers of every tk/ttk Label PACKED into `container` in `scope`.

    Asserting the absence of one particular sentence let any re-added hint
    Label through as long as it was reworded -- and the label being gone is a
    claim about the packed COLUMN (it spends window height, which is the whole
    reason it was deleted), not about a string. Both shapes are caught: the
    inline chain tk.Label(container, ...).pack(...), and the two-step
    `x = tk.Label(container, ...)` followed by `x.pack(...)`.
    """
    built = [n for n in ast.walk(scope)
             if isinstance(n, ast.Call)
             and _name(n.func) in ("tk.Label", "ttk.Label")
             and n.args and _name(n.args[0]) == container]
    if not built:
        return []
    built_ids = {id(n) for n in built}
    bound = set()
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and id(node.value) in built_ids:
            bound.update(filter(None, (_name(t) for t in node.targets)))
    hits = []
    for node in ast.walk(scope):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("pack", "grid", "place")):
            continue
        if id(node.func.value) in built_ids or _name(node.func.value) in bound:
            hits.append(node.lineno)
    return sorted(hits)


_hint_labels = _packed_labels_in("arr_wrap", APP_INIT)
check("no Label is packed into the arrangement column at all -- the hint that "
      "pointed at “Screen sizes…” is gone and cannot come back reworded",
      not _hint_labels, f"packed Label(s) at line(s) {_hint_labels}")
check("the canvas draws its own tell instead, inside itself (costs no window "
      "height)",
      hasattr(A.MultiArrangeCanvas, "_draw_hint")
      and "right-click a screen to edit it" in SOURCE)
redraw = _method("MultiArrangeCanvas", "redraw")
check("redraw() draws that hint",
      any(_name(n.func) == "self._draw_hint" for n in ast.walk(redraw)
          if isinstance(n, ast.Call)))


# ---- (e) structural half: every menu command is deferred -------------------
MENU_BUILDERS = ("_fill_surface_menu", "_fill_local_entries", "_fill_desk_menu",
                 "_fill_device_verb_entries")
inline = []
commands = 0
for builder in MENU_BUILDERS:
    node = _method("App", builder)
    if node is None:
        inline.append(f"{builder}: MISSING")
        continue
    for call in ast.walk(node):
        if not (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr in ("add_command", "add_cascade")):
            continue
        for keyword in call.keywords:
            if keyword.arg != "command":
                continue
            commands += 1
            if not (isinstance(keyword.value, ast.Call)
                    and _name(keyword.value.func) == "self._deferred"):
                inline.append(f"{builder}:{call.lineno}")
check("(e) every menu command is built through App._deferred", not inline,
      ", ".join(inline))
check("(e) there are commands to defer at all", commands >= 8,
      f"{commands} commands")
deferred = _method("App", "_deferred")
check("(e) _deferred really goes through root.after",
      any(isinstance(n, ast.Call) and _name(n.func) == "self.root.after"
          for n in ast.walk(deferred)))


# ---- (d) the merge, as a pure function -------------------------------------
# Driven with a synthetic enum_monitors result, so it is the merge rule under
# test and not this machine's monitors.
SAVED = [
    {"name": "\\\\.\\DISPLAY1", "x": 0, "y": 0, "w": 1920, "h": 1080,
     "primary": True, "layout_x": -400, "layout_y": -77,
     "layout_w": 1482, "layout_h": 833, "refresh_hz": 60.0,
     "diagonal_in": 17.0},
    {"name": "\\\\.\\DISPLAY2", "x": 1920, "y": 0, "w": 1920, "h": 1080,
     "primary": False, "layout_x": 1100, "layout_y": 40,
     "layout_w": 1482, "layout_h": 833, "refresh_hz": 60.0,
     "diagonal_in": 24.0},
]
# DISPLAY1 changed resolution AND refresh, DISPLAY2 is unplugged, DISPLAY9 is
# new, and Windows has moved the primary flag.
LIVE_NOW = [
    {"name": "\\\\.\\DISPLAY1", "x": 0, "y": 0, "w": 2560, "h": 1440,
     "primary": False, "refresh_hz": 144.0},
    {"name": "\\\\.\\DISPLAY9", "x": 2560, "y": 0, "w": 1920, "h": 1080,
     "primary": True, "refresh_hz": 60.0},
]
merged, report = T.merge_live_monitors(SAVED, LIVE_NOW)
by_name = {row["name"]: row for row in merged}
one = by_name.get("\\\\.\\DISPLAY1", {})
check("(d) the merge preserves diagonal_in for a monitor that still exists",
      one.get("diagonal_in") == 17.0, str(one.get("diagonal_in")))
check("(d) the merge preserves the hand-placed layout position",
      (one.get("layout_x"), one.get("layout_y")) == (-400, -77),
      f"{one.get('layout_x')}, {one.get('layout_y')}")
check("(d) Windows' resolution wins",
      (one.get("w"), one.get("h")) == (2560, 1440),
      f"{one.get('w')}x{one.get('h')}")
check("(d) the rectangle is re-derived from the kept diagonal and the NEW "
      "resolution",
      (one.get("layout_w"), one.get("layout_h"))
      == A.physical_size(17.0, 2560, 1440, 0),
      f"{one.get('layout_w')}x{one.get('layout_h')}")
check("(d) the real refresh rate replaces the stale saved one",
      one.get("refresh_hz") == 144.0, str(one.get("refresh_hz")))
check("(d) an unplugged monitor is dropped and REPORTED, not silently lost",
      "\\\\.\\DISPLAY2" not in by_name
      and report["removed"] == ["\\\\.\\DISPLAY2"], str(report["removed"]))
check("(d) a new monitor is added and reported",
      "\\\\.\\DISPLAY9" in by_name
      and report["added"] == ["\\\\.\\DISPLAY9"], str(report["added"]))
check("(d) the resolution change is reported",
      report["resolution"] == [("\\\\.\\DISPLAY1", "1920x1080", "2560x1440")],
      str(report["resolution"]))
check("(d) the refresh change is reported",
      [row[0] for row in report["refresh"]] == ["\\\\.\\DISPLAY1"],
      str(report["refresh"]))
check("(d) the moved primary flag is reported (for the monitor that was "
      "already known -- a brand new one is reported as added, not as changed)",
      report["primary"] == ["\\\\.\\DISPLAY1"], str(report["primary"]))
summary = A.describe_monitor_refresh(report)
check("(d) the report renders as something a human can read",
      "DISPLAY9" in summary and "2560x1440" in summary, summary)
check("(d) an unchanged desk says so rather than claiming an edit",
      A.describe_monitor_refresh(
          T.merge_live_monitors(SAVED, [
              {"name": "\\\\.\\DISPLAY1", "x": 0, "y": 0, "w": 1920, "h": 1080,
               "primary": True, "refresh_hz": 60.0},
              {"name": "\\\\.\\DISPLAY2", "x": 1920, "y": 0, "w": 1920,
               "h": 1080, "primary": False, "refresh_hz": 60.0}])[1])
      == "nothing changed",
      A.describe_monitor_refresh(T.merge_live_monitors(SAVED, LIVE_NOW)[1]))

# The invented 60 Hz is gone: a monitor nobody has a reading for carries no
# refresh_hz at all, rather than a number the app made up.
blind = T._normalize_monitor(
    {"name": "X", "x": 0, "y": 0, "w": 1920, "h": 1080, "primary": True}, None)
check("_normalize_monitor no longer invents 60 Hz",
      "refresh_hz" not in blind, str(blind.get("refresh_hz")))
check("openspan_setup exposes a real refresh reader for local monitors",
      callable(getattr(__import__("openspan_setup"), "display_refresh_hz",
                       None)))


# ---- Tk from here down ------------------------------------------------------
root = tk.Tk()
root.withdraw()          # never draw on the desk this is being run from

SYNTHETIC_MONITORS = [
    {"name": "\\\\.\\DISPLAY1", "x": 0, "y": 0, "w": 1920, "h": 1080,
     "primary": True, "refresh_hz": 120.0},
]
A.enum_monitors = lambda: [dict(row) for row in SYNTHETIC_MONITORS]
# The synthetic screen borrows a REAL name (\\.\DISPLAY1), so without this
# stub the EDID reader sizes it from this machine's registry -- 17.1" here,
# 27" or 32" on another desk -- and every coordinate below moves. A synthetic
# desk reads no registry.
A.monitor_sizes = lambda: {}

DESK = {
    "version": 3,
    # "links" present on purpose: normalize_config runs a ONE-TIME migration
    # snap of each device's first display when it is absent, which would move
    # the rectangles out from under the coordinates asserted below.
    "links": [],
    "monitors": [dict(SYNTHETIC_MONITORS[0], layout_x=0, layout_y=0,
                      layout_w=1482, layout_h=833, diagonal_in=17.0)],
    "devices": [
        {"id": "ipad", "name": "iPad", "port": 9955, "enabled": True,
         "displays": [{"id": "ipad-main", "name": "iPad", "x": -900, "y": 0,
                       "w": 816, "h": 612, "res_w": 1080, "res_h": 810,
                       "rotation": 0, "refresh_hz": 60.0,
                       "diagonal_in": 10.2}]},
        {"id": "mac", "name": "Managed Mac", "port": 9956, "enabled": True,
         "displays": [{"id": "mac-1", "name": "Mac Display 1", "x": 1900,
                       "y": 0, "w": 2789, "h": 1569, "res_w": 2560,
                       "res_h": 1440, "rotation": 0, "refresh_hz": 144.0,
                       "diagonal_in": 32.0},
                      {"id": "mac-2", "name": "Mac Display 2", "x": 4800,
                       "y": 0, "res_w": 1920, "res_h": 1080, "rotation": 0,
                       "refresh_hz": 60.0}]},
    ],
}
with open(A.CONFIG, "w", encoding="utf-8") as handle:
    json.dump(DESK, handle)

canvas = A.MultiArrangeCanvas(root, on_change=None, height=270)
canvas.winfo_width = lambda: 852
canvas.winfo_height = lambda: 447

IPAD_KEY = ("target", "ipad", "ipad-main")
MAC_KEY = ("target", "mac", "mac-1")
BARE_KEY = ("target", "mac", "mac-2")     # the one with NO diagonal_in
LOCAL_KEY = ("local", "windows", "\\\\.\\DISPLAY1")

check("the synthetic desk resolves through _lookup",
      all(canvas._lookup(k) is not None
          for k in (IPAD_KEY, MAC_KEY, BARE_KEY, LOCAL_KEY)))
check("mac-2 really has no diagonal (the case that must never be defaulted)",
      not canvas._lookup(BARE_KEY).get("diagonal_in"))


class StubApp:
    """An App-shaped object built WITHOUT App.__init__.

    Constructing the real App starts the VM and the audio workers, so every
    method under test is lifted off the real class and bound here instead. The
    code being exercised is therefore the shipping code, not a copy of it."""

    def __init__(self, root_widget, arrange):
        self.root = root_widget
        self.canvas = arrange
        self._surface_menu = tk.Menu(root_widget, tearoff=0, **A.MENU_STYLE)
        self._desk_menu = tk.Menu(root_widget, tearoff=0, **A.MENU_STYLE)
        # Built ONCE and repopulated in place, exactly as App.__init__ does.
        # A fresh tk.Menu per popup leaks: Menu.delete releases the entries' Tcl
        # commands but never the cascaded submenu widget, and the submenu stays
        # in its master's children dict for the life of the process.
        self._res_menu = tk.Menu(self._surface_menu, tearoff=0, **A.MENU_STYLE)
        self._hz_menu = tk.Menu(self._surface_menu, tearoff=0, **A.MENU_STYLE)
        self._device_menu = tk.Menu(self._desk_menu, tearoff=0, **A.MENU_STYLE)
        self.status = tk.StringVar(value="")
        # A managed display's menu now ends with that DEVICE's four connection
        # verbs, so filling one reaches the per-device state the card reads.
        # Empty _radios means "no radio list yet", which is the honest reading
        # for a stub -- _device_verb_facts only calls a radio missing when it
        # has a list to miss it from.
        self._dev_states = {}
        self._dev_status = {}
        self._vm_reachable = True
        self.bt_panel = types.SimpleNamespace(_radios=[])
        self.clicked = []


for _name_ in ("_deferred", "_canvas_menu", "_fill_surface_menu",
               "_fill_local_entries", "_fill_desk_menu",
               "_resolution_presets", "_refresh_presets", "_ask_diagonal",
               "_edit_display", "_menu_set_resolution", "_menu_rotate",
               "_menu_set_refresh", "_menu_diagonal", "_menu_device_editor",
               "_menu_refresh_monitors", "_menu_display_settings",
               "_screen_sizes_dialog", "_dev_state", "_device_verb_facts",
               "_device_verb_entries", "_verb_menu_label",
               "_fill_device_verb_entries", "device_record"):
    setattr(StubApp, _name_, A.App.__dict__[_name_])


# The four verb handlers ssh to the guest, so they are recorders here. Bound
# off DEVICE_VERB_HANDLERS rather than by name, so a rename cannot leave a
# stale stub that never fires.
def _verb_recorder(verb):
    def handler(self, device_id):
        self.clicked.append((verb, device_id))
    return handler


for _verb_, _handler_ in A.DEVICE_VERB_HANDLERS.items():
    setattr(StubApp, _handler_, _verb_recorder(_verb_))

app = StubApp(root, canvas)


def entries(menu):
    """(index, type, label, state) for every entry on a posted-shape menu."""
    out = []
    end = menu.index("end")
    if end is None:
        return out
    for index in range(end + 1):
        kind = menu.type(index)
        if kind == "separator":
            out.append((index, kind, "", ""))
            continue
        out.append((index, kind, str(menu.entrycget(index, "label")),
                    str(menu.entrycget(index, "state"))))
    return out


def labels(menu):
    return [row[2] for row in entries(menu)]


# ---- (c) the local monitor menu is read-only about Windows' own fields ------
app._fill_surface_menu(app._surface_menu, LOCAL_KEY, canvas._lookup(LOCAL_KEY))
local_rows = entries(app._surface_menu)
editable_res = [row for row in local_rows
                if row[2].startswith("Resolution") and row[3] != "disabled"]
editable_hz = [row for row in local_rows
               if row[2].startswith("Refresh ") and "now" not in row[2]
               and row[3] != "disabled"]
check("(c) the local menu offers NO editable resolution entry",
      not editable_res, str(editable_res))
check("(c) the local menu offers NO editable refresh entry",
      not editable_hz, str(editable_hz))
check("(c) it has no cascade at all -- a value list would imply an edit",
      not [row for row in local_rows if row[1] == "cascade"],
      str([row[2] for row in local_rows if row[1] == "cascade"]))
check("(c) it still SHOWS the resolution Windows reports",
      any("1920 × 1080" in row[2] for row in local_rows), str(labels(
          app._surface_menu)))
check("(c) it shows the REAL refresh rate, not an invented 60",
      any("120 Hz" in row[2] for row in local_rows), str(labels(
          app._surface_menu)))
check("(c) Diagonal is editable -- the one field Windows cannot supply",
      any(row[2].startswith("Diagonal") and row[3] == "normal"
          for row in local_rows))
# ...and it costs exactly what its managed-display twin costs. _menu_diagonal
# writes layout_w/layout_h for a local key, portal_signature lists
# layout_w/layout_h per monitor, so this entry taskkills and respawns the
# portal across all three lanes. It was the ONLY disruptive entry in either
# menu with no cost on its label.
check("(c) the local Diagonal entry names its cost, like the managed twin",
      all("restarts input" in row[2] for row in local_rows
          if row[2].startswith("Diagonal")),
      str([row[2] for row in local_rows if row[2].startswith("Diagonal")]))
check("(c) Refresh now is offered",
      any(row[2].startswith("Refresh now") and row[3] == "normal"
          for row in local_rows))
check("(c) Windows display settings is offered",
      any("Windows display settings" in row[2] for row in local_rows))

# ...and the same code path on a MANAGED display is deliberately different.
app._fill_surface_menu(app._surface_menu, MAC_KEY, canvas._lookup(MAC_KEY))
mac_rows = entries(app._surface_menu)
cascades = [row[2] for row in mac_rows if row[1] == "cascade"]
check("a managed display DOES get a Resolution cascade",
      any(row.startswith("Resolution") for row in cascades), str(cascades))
check("a managed display DOES get a Refresh rate cascade",
      any(row.startswith("Refresh rate") for row in cascades), str(cascades))
check("the disruptive entries are labelled with their cost",
      all(any("restarts input" in row[2] for row in mac_rows
              if row[2].startswith(prefix))
          for prefix in ("Resolution", "Rotate", "Diagonal")),
      str([row[2] for row in mac_rows]))
check("refresh rate is labelled free, because it really is "
      "(portal_signature excludes it)",
      any("free" in row for row in cascades), str(cascades))
check("the device editor is reachable from the screen you clicked",
      any(row[2].startswith("Edit all screens on Managed Mac")
          for row in mac_rows), str([row[2] for row in mac_rows]))
check("the menu's title line names the surface it is about",
      mac_rows[0][2].startswith("Managed Mac")
      and mac_rows[0][3] == "disabled", str(mac_rows[0]))

# ---- the connection verbs ride on the managed menu, and only there ---------
# The behaviour of that section -- which verbs are live in which state, and
# that the menu and the card can never disagree about it -- is
# test_device_verbs.py. What belongs HERE is which menu carries it at all: the
# verbs act on a DEVICE, and a Windows monitor does not have one.
check("a managed display's menu carries the device's connection section, "
      "headed by the DEVICE's name -- the menu is opened on one screen but "
      "Unpair unpairs the whole machine",
      any(row[2] == "Managed Mac — connection" and row[3] == "disabled"
          for row in mac_rows), str([row[2] for row in mac_rows]))
check("...and it is the LAST section, after the display entries",
      [row[2] for row in mac_rows].index("Managed Mac — connection")
      > [row[2] for row in mac_rows].index(
          "Edit all screens on Managed Mac…"))
check("a Windows monitor's menu carries NO connection section -- there is no "
      "device behind it to pair",
      not any("connection" in row[2] for row in local_rows),
      str([row[2] for row in local_rows]))
app._fill_desk_menu(app._desk_menu)
_desk_rows = entries(app._desk_menu)
check("the empty-canvas menu carries none either -- a verb needs a device to "
      "act on, and right-clicking nothing names none",
      not any("connection" in row[2] for row in _desk_rows),
      str([row[2] for row in _desk_rows]))


# ---- resolution lists are per display KIND ---------------------------------
ipad_options = app._resolution_presets(IPAD_KEY, canvas._lookup(IPAD_KEY))
ipad_sizes = [size for _label, size in ipad_options]
check("an iPad screen is offered the NAMED iPad geometries",
      set(ipad_sizes) >= {tuple(v) for v in A.IPAD_PRESETS.values()},
      str(ipad_sizes))
check("an iPad screen is NOT offered desktop pixel resolutions "
      "(that would rescale the whole HID lane, not any screen)",
      (3840, 2160) not in ipad_sizes and (1920, 1080) not in ipad_sizes,
      str(ipad_sizes))
check("the iPad list is the one the setup module already ships",
      [label for label, _size in ipad_options] == list(A.IPAD_PRESETS),
      str([label for label, _size in ipad_options]))
mac_options = app._resolution_presets(MAC_KEY, canvas._lookup(MAC_KEY))
mac_sizes = [size for _label, size in mac_options]
check("a desktop screen IS offered desktop resolutions",
      (3840, 2160) in mac_sizes and (2560, 1440) in mac_sizes, str(mac_sizes))
check("the current resolution always has a home in the list, so the check-mark "
      "is never orphaned",
      (int(canvas._lookup(MAC_KEY)["res_w"]),
       int(canvas._lookup(MAC_KEY)["res_h"])) in mac_sizes)
odd = dict(canvas._lookup(MAC_KEY), res_w=3000, res_h=2000)
check("...including a resolution that is on no preset list",
      (3000, 2000) in [size for _l, size in
                       app._resolution_presets(MAC_KEY, odd)])
check("display_kind reads the iPad from its geometry alone, not only its name",
      A.display_kind({"id": "device-7", "name": "Tablet"},
                     {"res_w": 1194, "res_h": 834}) == "ipad")
check("display_kind calls a 4K panel a desktop",
      A.display_kind({"id": "mac", "name": "Managed Mac"},
                     {"res_w": 3840, "res_h": 2160}) == "desktop")


# ---- (e) runtime half: nothing runs inline ---------------------------------
# root.after is replaced with a recorder, so invoking a menu entry must produce
# a QUEUED callable and must not execute anything.
recorded = []
real_after = root.after
root.after = lambda delay, fn=None, *rest: recorded.append((delay, fn))


def invoke_everything(menu, seen=None):
    """Invoke every enabled command on a menu and its cascades."""
    seen = seen if seen is not None else set()
    count = 0
    for index, kind, _label, state in entries(menu):
        if kind == "cascade":
            path = menu.entrycget(index, "menu")
            if path and path not in seen:
                seen.add(path)
                count += invoke_everything(root.nametowidget(path), seen)
        elif kind == "command" and state == "normal":
            menu.invoke(index)
            count += 1
    return count


app._fill_surface_menu(app._surface_menu, MAC_KEY, canvas._lookup(MAC_KEY))
invoked = invoke_everything(app._surface_menu)
check("(e) every enabled entry on a display menu queued instead of running",
      invoked > 0 and len(recorded) == invoked,
      f"invoked {invoked}, queued {len(recorded)}")
check("(e) everything is queued at delay 0",
      all(delay == 0 for delay, _fn in recorded),
      str({delay for delay, _fn in recorded}))
before = len(recorded)
app._fill_desk_menu(app._desk_menu)
desk_invoked = invoke_everything(app._desk_menu)
check("(e) the same holds for the empty-canvas menu",
      desk_invoked > 0 and len(recorded) - before == desk_invoked,
      f"invoked {desk_invoked}, queued {len(recorded) - before}")

ran = []
app._deferred(lambda *a: ran.append(a), 7, 8)()
check("(e) _deferred does not call through on its own",
      not ran and recorded[-1][0] == 0)
recorded[-1][1]()
check("(e) ...and the queued callable carries the arguments",
      ran == [(7, 8)], str(ran))
root.after = real_after
recorded.clear()


# ---- (7) the empty-canvas menu keeps the all-surfaces escape hatch ----------
app._fill_desk_menu(app._desk_menu)
desk_labels = labels(app._desk_menu)
check("(7) “Screen sizes…” survives on the empty-canvas menu",
      any(row.startswith("Screen sizes") for row in desk_labels),
      str(desk_labels))
check("(7) the empty-canvas menu can reach a device the hit test cannot",
      any("Edit a device" in row for row in desk_labels), str(desk_labels))
check("(7) ...and it lists every device by name",
      app._device_menu is not None
      and labels(app._device_menu) == ["iPad…", "Managed Mac…"],
      str(labels(app._device_menu)))
check("(7) Refresh now is here too",
      any(row.startswith("Refresh Windows screens") for row in desk_labels))


# ---- (8) the write path ----------------------------------------------------
signature_before = A.portal_signature(canvas.config)
app._menu_set_refresh(MAC_KEY, 60)
check("(8) a refresh-rate edit is written",
      canvas._lookup(MAC_KEY)["refresh_hz"] == 60.0)
check("(8) a refresh-rate edit does NOT restart the portal -- it is the one "
      "genuinely free edit",
      A.portal_signature(canvas.config) == signature_before)
check("(8) a refresh-rate edit leaves the rectangle alone",
      (canvas._lookup(MAC_KEY)["w"], canvas._lookup(MAC_KEY)["h"])
      == (2789, 1569))

app._menu_set_resolution(MAC_KEY, 3840, 2160)
after_res = canvas._lookup(MAC_KEY)
check("(8) a resolution edit is written",
      (after_res["res_w"], after_res["res_h"]) == (3840, 2160))
check("(8) the rectangle is recomputed from the diagonal, from the RAW "
      "resolution",
      (after_res["w"], after_res["h"])
      == A.physical_size(32.0, 3840, 2160, 0),
      f"{after_res['w']}x{after_res['h']}")
check("(8) a resolution edit DOES restart the portal",
      A.portal_signature(canvas.config) != signature_before)

before_rot = dict(canvas._lookup(MAC_KEY))
app._menu_rotate(MAC_KEY)
after_rot = canvas._lookup(MAC_KEY)
check("(8) rotate turns the screen that was clicked",
      after_rot["rotation"] == 90 and before_rot["rotation"] == 0)
check("(8) rotation re-derives the rectangle rather than only swapping it",
      (after_rot["w"], after_rot["h"])
      == A.physical_size(32.0, 3840, 2160, 90),
      f"{after_rot['w']}x{after_rot['h']}")
check("(8) the iPad next door was not touched",
      (canvas._lookup(IPAD_KEY)["w"], canvas._lookup(IPAD_KEY)["h"])
      == (816, 612))

# The prompt path, with dark_prompt answered by a stub. The screen under test
# is the one that has no diagonal at all -- physical_size clamps a missing
# diagonal to 1", which would collapse it to MIN_LAYOUT_SIZE and move every
# crossing band on it.
asked = []
real_prompt = A.dark_prompt
A.dark_prompt = lambda parent, title, message, default="": (
    asked.append(title) or "27")
app._menu_set_resolution(BARE_KEY, 2560, 1440)
bare = canvas._lookup(BARE_KEY)
check("(8) a screen with no diagonal is ASKED, never defaulted",
      asked and bare.get("diagonal_in") == 27.0, str(asked))
check("(8) ...and its rectangle comes out of the answer, not of the clamp",
      (bare["w"], bare["h"]) == A.physical_size(27.0, 2560, 1440, 0)
      and bare["w"] > T.MIN_LAYOUT_SIZE,
      f"{bare['w']}x{bare['h']}")

# Cancelling the prompt must change nothing at all.
A.dark_prompt = lambda parent, title, message, default="": None
snapshot = dict(canvas._lookup(BARE_KEY))
del snapshot["diagonal_in"]
canvas._lookup(BARE_KEY).pop("diagonal_in")
app._menu_set_resolution(BARE_KEY, 1280, 720)
check("(8) cancelling the diagonal prompt abandons the whole edit",
      canvas._lookup(BARE_KEY)["res_w"] == snapshot["res_w"],
      str(canvas._lookup(BARE_KEY)))
A.dark_prompt = real_prompt

# A stale key -- the exact hazard adopt()'s docstring describes -- must bail.
GONE = ("target", "mac", "mac-99")
check("(8) an edit aimed at a display that no longer exists bails out",
      app._edit_display(GONE, lambda item: item.update(res_w=1)) is False)
# Observed by EFFECT, not by return value. All three of these return None on
# every path they have, so `(...) == (None, None, None)` held whether or not a
# single one of them bailed -- it was three method calls dressed as an
# assertion. A bail writes nothing, so save() must never be reached and the
# config must come out byte identical. dark_prompt is stubbed to ANSWER, so a
# _menu_diagonal that failed to bail would really write.
_saves = []
_real_save = canvas.save
_real_prompt = A.dark_prompt
canvas.save = lambda *a, **k: _saves.append(1)
A.dark_prompt = lambda parent, title, message, default="": "31"
_before_gone = json.dumps(canvas.config, sort_keys=True, default=str)
app._menu_rotate(GONE)
app._menu_set_refresh(GONE, 60)
app._menu_diagonal(GONE)
canvas.save = _real_save
A.dark_prompt = _real_prompt
check("(8) ...and a rotate, a refresh and a diagonal on it never reach save()",
      _saves == [], f"{len(_saves)} save() call(s)")
check("(8) ...and leave the desk byte identical",
      json.dumps(canvas.config, sort_keys=True, default=str) == _before_gone)
# The same guard, on the other axis: _menu_set_refresh must refuse a LOCAL key
# the way _edit_display does. Windows owns a monitor's refresh rate, and the
# invented number is exactly what this wave existed to kill.
_local_hz_before = canvas._lookup(LOCAL_KEY).get("refresh_hz")
app._menu_set_refresh(LOCAL_KEY, 30)
check("(8) a refresh edit refuses a Windows monitor -- key[0] is guarded",
      canvas._lookup(LOCAL_KEY).get("refresh_hz") == _local_hz_before,
      str(canvas._lookup(LOCAL_KEY).get("refresh_hz")))

# The local diagonal takes the local derivation: a monitor's w/h ARE pixels and
# its desk rectangle is layout_w/layout_h.
A.dark_prompt = lambda parent, title, message, default="": "21.5"
app._menu_diagonal(LOCAL_KEY)
monitor = canvas._lookup(LOCAL_KEY)
check("(8) a local monitor's diagonal writes layout_w/layout_h, not w/h",
      (monitor["layout_w"], monitor["layout_h"])
      == A.physical_size(21.5, 1920, 1080, 0)
      and (monitor["w"], monitor["h"]) == (1920, 1080),
      f"{monitor['layout_w']}x{monitor['layout_h']}")
A.dark_prompt = real_prompt


# ---- (d) runtime half: Refresh now, through the real handler ---------------
# The hand-placed position is parked CLEAR of every device on this desk. It
# used to be (-613, 41), which -- at the 21.5" this fixture types -- lay
# across the iPad at (-900,0); the PC block is now kept off the devices on
# refresh (as macOS keeps screens off each other), so an overlapping "hand
# placement" is no longer a position a refresh can honour. ABOVE every
# device (all of them start at y=0, and an earlier check in this file
# rotated Mac Display 1 to 2789 tall), so the second monitor Windows adds
# to the right of the primary is clear too. What this checks is unchanged:
# the position the user chose survives a re-read of Windows.
canvas._lookup(LOCAL_KEY)["layout_x"] = -613
canvas._lookup(LOCAL_KEY)["layout_y"] = -1200
kept_diagonal = canvas._lookup(LOCAL_KEY)["diagonal_in"]
A.enum_monitors = lambda: [
    {"name": "\\\\.\\DISPLAY1", "x": 0, "y": 0, "w": 2560, "h": 1440,
     "primary": True, "refresh_hz": 60.0},
    {"name": "\\\\.\\DISPLAY7", "x": 2560, "y": 0, "w": 1920, "h": 1080,
     "primary": False, "refresh_hz": 75.0},
]
app._menu_refresh_monitors()
refreshed = canvas._lookup(LOCAL_KEY)
check("(d) Refresh now keeps the diagonal the user typed",
      refreshed["diagonal_in"] == kept_diagonal, str(refreshed))
check("(d) Refresh now keeps the hand-placed position",
      (refreshed["layout_x"], refreshed["layout_y"]) == (-613, -1200),
      f"{refreshed['layout_x']}, {refreshed['layout_y']}")
check("(d) Refresh now takes Windows' new resolution",
      (refreshed["w"], refreshed["h"]) == (2560, 1440))
check("(d) Refresh now adds the monitor Windows just reported",
      canvas._lookup(("local", "windows", "\\\\.\\DISPLAY7")) is not None)
check("(d) the canvas and the config point at the SAME monitor list",
      canvas.monitors is canvas.config["monitors"])
check("(d) it says what changed instead of rewriting the desk silently",
      "DISPLAY7" in app.status.get() and "2560x1440" in app.status.get(),
      app.status.get())
A.enum_monitors = lambda: [dict(row) for row in SYNTHETIC_MONITORS]


# ---- (12) the hover card is anchored at the pointer ------------------------
class FakeEvent:
    def __init__(self, x, y):
        self.x, self.y = x, y
        self.x_root, self.y_root = x, y


canvas.action = None
# Aimed at the CENTRE of the rectangle as it stands right now, rather than at
# the coordinates it was declared with: the edits above have legitimately
# resized and rotated it.
_rx, _ry, _rw, _rh = canvas._rect(MAC_KEY, canvas._lookup(MAC_KEY))
mac_x, mac_y = canvas.w2c(_rx + _rw // 2, _ry + _rh // 2)
canvas._on_hover(FakeEvent(int(mac_x), int(mac_y)))
check("(12) _on_hover records the pointer, because _draw_hover's other two "
      "callers (redraw and <Leave>) carry no event",
      canvas._hover_xy == (int(mac_x), int(mac_y)), str(canvas._hover_xy))
check("(12) hovering a surface draws a card at all",
      canvas.bbox("hovercard") is not None and canvas._hover == MAC_KEY,
      str(canvas._hover))

# Placed deterministically, so this measures the anchoring rule and not
# whatever world coordinate the desk happens to have after the edits above.
canvas._hover_xy = (200, 150)
canvas._draw_hover()
card = canvas.bbox("hovercard")
check("(12) the card is drawn AT the pointer, offset by HOVER_OFFSET -- not "
      "pinned to the bottom-left corner 350-600px away",
      card is not None
      and abs(card[0] - (200 + canvas.HOVER_OFFSET)) <= 2
      and abs(card[1] - (150 + canvas.HOVER_OFFSET)) <= 2,
      f"card {card}, pointer (200, 150)")
canvas._hover_xy = (500, 300)
canvas._draw_hover()
moved_card = canvas.bbox("hovercard")
check("(12) ...and it follows the pointer rather than staying put",
      moved_card is not None and moved_card[0] != card[0],
      f"{card} -> {moved_card}")

canvas._hover_xy = (846, 442)
canvas._draw_hover()
edge_card = canvas.bbox("hovercard")
check("(12) at the far corner it flips to the other side of the pointer "
      "instead of being clipped",
      edge_card is not None
      and edge_card[2] <= 852 and edge_card[3] <= 447
      and edge_card[0] >= 0 and edge_card[1] >= 0,
      str(edge_card))

# The invented 60 Hz is gone from the detail card too.
canvas._lookup(LOCAL_KEY).pop("refresh_hz", None)
_title, lines = canvas._detail_lines(LOCAL_KEY, canvas._lookup(LOCAL_KEY))
check("(12) with no refresh reading the card says nothing about Hz",
      "Hz" not in lines[0], lines[0])
canvas._lookup(LOCAL_KEY)["refresh_hz"] = 120.0
_title, lines = canvas._detail_lines(LOCAL_KEY, canvas._lookup(LOCAL_KEY))
check("(12) with a real reading it states it",
      "120 Hz" in lines[0], lines[0])


# ---- the popup itself, end to end ------------------------------------------
posted = []
for menu in (app._surface_menu, app._desk_menu):
    menu.tk_popup = lambda x, y, m=menu: posted.append((m, x, y))
canvas.action = "move"
app._canvas_menu(FakeEvent(int(mac_x), int(mac_y)))
check("mid-drag, right-click is ignored (the guard _on_hover uses)", not posted)
canvas.action = None
app._canvas_menu(FakeEvent(int(mac_x), int(mac_y)))
check("right-clicking a screen posts the surface menu",
      len(posted) == 1 and posted[0][0] is app._surface_menu)
check("the clicked screen is SELECTED before the menu is posted, so the "
      "outline and the menu agree",
      canvas.selected == MAC_KEY, str(canvas.selected))
empty_x, empty_y = canvas.w2c(canvas.wx0 + 20, canvas.wy0 + 20)
app._canvas_menu(FakeEvent(int(empty_x), int(empty_y)))
check("right-clicking empty canvas posts the arrangement menu",
      len(posted) == 2 and posted[1][0] is app._desk_menu)
check("...and clears the selection with it",
      canvas.selected is None, str(canvas.selected))

check("the menus are held on the app, so Tk cannot collect a posted cascade",
      isinstance(app._surface_menu, tk.Menu)
      and isinstance(app._desk_menu, tk.Menu)
      and isinstance(app._res_menu, tk.Menu)
      and isinstance(app._hz_menu, tk.Menu)
      and isinstance(app._device_menu, tk.Menu))

# ...and held is not enough: they must be the SAME widgets every popup.
# Menu.delete releases an entry's Tcl command object but never a cascaded
# submenu widget, and the submenu stays in its master's children dict for the
# life of the process -- so a fresh tk.Menu per right-click stranded two Menu
# widgets and about twenty Tcl commands each time.
_res_path, _hz_path = str(app._res_menu), str(app._hz_menu)
_dev_path = str(app._device_menu)
for _ in range(5):
    app._fill_surface_menu(app._surface_menu, MAC_KEY, canvas._lookup(MAC_KEY))
    app._fill_desk_menu(app._desk_menu)
check("the cascades are repopulated in place, not rebuilt per popup",
      (str(app._res_menu), str(app._hz_menu), str(app._device_menu))
      == (_res_path, _hz_path, _dev_path),
      f"{_res_path} -> {app._res_menu}")
check("...so six popups strand no submenu widgets at all",
      len(app._surface_menu.children) == 2
      and len(app._desk_menu.children) == 1,
      f"{len(app._surface_menu.children)} under the surface menu, "
      f"{len(app._desk_menu.children)} under the desk menu")

check("nothing here touched the live config",
      A.CONFIG != LIVE_CONFIG_PATH and os.path.dirname(A.CONFIG) == SCRATCH)

root.destroy()
shutil.rmtree(SCRATCH, ignore_errors=True)
print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
