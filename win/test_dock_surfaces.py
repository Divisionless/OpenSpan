# SPDX-License-Identifier: AGPL-3.0-or-later
"""The right-edge dock, and the three surfaces it invokes.

Doug, 2026-08-28: *"split this into Dashboard and Console -- invokable and
collapsible from the right side dock (that you create now) -- add Scripts as a
third surface here"*, and the distinction that governs the whole shape: *"no
pop out but we can replace surfaces and invoke entire new ones in the side."*

DIALOGS ARE BANNED; SURFACES ARE INVOKED FROM THE DOCK. A dialog is an OS
window Windows places wherever it likes, on a screen he was not looking at (28
July). A surface is a region of the app's own window, asked for by name from
the rail, replacing whatever was showing there. Adding a fourth surface must
never need a window, and the two that exist are exempt by name in
test_frame_modal, with the reason written where the rule is enforced.

THE RAIL LEFT THE WINDOW on 2026-08-29. Doug: *"The column doesn't need to be
that high for the sidebar, it needs to be just to Scripts and then below it our
new vertical dock."* It is a strip of its own now -- a borderless window on the
Desktop-role screen's right edge, holding the wordmark and one row per surface
and nothing else, exactly as tall as those rows -- with the shell's app-icons
dock below it and ``on_desktop.STRIP_GAP`` guaranteed between the two. The app
becomes an ordinary window that the strip shows and hides.

COLLAPSED THEREFORE MEANS HIDDEN. It meant "as wide as the rail" for one day.
Doug, on a screenshot of a full-width window holding nothing but the rail:
*"This should be fully collapsed, this is not a valid state."* That was fixed
by making the pack and a resize one act; the rail leaving retires the resize
entirely, because there is nothing left in the window worth showing and the
honest form of "put it away" is to put it away. The invariant below survives
the change with its two halves swapped: nothing packed IF AND ONLY IF the app
window is off screen, over every reachable click sequence.

WHAT THAT DELETED is asserted too. ``_dock_floor``, ``_dock_claim_width``,
``_window_width`` and ``_dock_expanded_w`` existed only because a collapse had
to be a resize. ``_rail_width`` survived and changed meaning -- it was the
collapsed window's floor and is now the strip's width -- and
``on_desktop.set_min_width`` / ``COLLAPSED_MIN_WIDTH`` survived untouched,
because they were built so a window with no pane in it could be placed narrower
than MIN_WIDTH and the strip is exactly that window.

LAW 10 -- *"nested scrolling is a scrolling surface inside another surface that
scrolls"* -- is a claim about CONTAINMENT on the same axis, so it is asserted
here as containment: neither the rail nor the strip scrolls at all, the three
surfaces are siblings of one another, exactly one is packed at a time, and each
owns at most one vertical scroller. Three scrollers across two windows are
lawful precisely because none can ever be inside another.

THE SCRIPTS SURFACE STOPPED BEING EMPTY on 2026-08-28. It is the face of
``script_engine`` now: what is on disk, what would not parse, and where to
write. So this file also holds the engine's LIFETIME contract -- constructed in
one place, started only from the switch or the startup arm, stopped with the
other hook owner on the way out -- because the lifetime and the surface are the
same piece of work and a surface that outlives its engine is the bug.

NO TK ROOT IS CONSTRUCTED IN THIS FILE, deliberately. The app is running on
Doug's desk while these run. The layout contract is read from the AST and the
switching behaviour is driven against fakes.
"""
import ast
import collections
import os
import shutil
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import on_desktop as OD  # noqa: E402  -- constants only; no window is touched
import openspan as A  # noqa: E402
import script_engine as SE  # noqa: E402
from settings_service import FeatureRegistry  # noqa: E402

fails = []


def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name + (
        "" if condition or not detail else "\n      " + detail))
    if not condition:
        fails.append(name)


SOURCE = open(os.path.join(HERE, "openspan.py"), encoding="utf-8").read()
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
            return next((item for item in node.body
                         if isinstance(item, ast.FunctionDef)
                         and item.name == method_name), None)
    return None


def _body_src(function):
    """Unparsed source of a method WITHOUT its docstring.

    Prose must never be able to pass or fail a contract check: a comment naming
    a Toplevel reads exactly like a Toplevel to a substring test, and every
    method here has a docstring that names one.
    """
    if function is None:
        return ""
    clone = ast.parse(ast.unparse(function)).body[0]
    if (clone.body and isinstance(clone.body[0], ast.Expr)
            and isinstance(clone.body[0].value, ast.Constant)
            and isinstance(clone.body[0].value.value, str)):
        clone.body = clone.body[1:] or [ast.Pass()]
    return ast.unparse(clone)


def _parents(function):
    """local widget name -> the local name of the master it was built on."""
    result = {}
    for node in ast.walk(function):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.value, ast.Call) and node.value.args):
            child, master = _name(node.targets[0]), _name(node.value.args[0])
            if child and master:
                result[child] = master
    return result


def _calls(function, dotted):
    return [node for node in ast.walk(function)
            if isinstance(node, ast.Call) and _name(node.func) == dotted]


init = _method("App", "__init__")
build_rail = _method("App", "_build_dock_rail")
build_strip = _method("App", "_build_dock_strip")
strip_place = _method("App", "_strip_place")
strip_geometry = _method("App", "_strip_geometry")
strip_controller = _method("App", "_strip_controller")
render = _method("App", "_render_dock")
state = _method("App", "_dock_state")
click = _method("App", "_dock_click")
show = _method("App", "_dock_show")
restore = _method("App", "_restore_dock")
paint_entry = _method("App", "_paint_dock_entry")
scripts_fn = _method("App", "_build_scripts_surface")
console_mount = _method("App", "_console_mount")
scripts_refresh = _method("App", "_scripts_refresh")
scripts_report = _method("App", "_scripts_report")
scripts_reload = _method("App", "_scripts_reload")
scripts_seed = _method("App", "_scripts_seed")
script_host = _method("App", "_script_host")
toggle_scripts = _method("App", "_toggle_scripts")
start_scripts = _method("App", "_start_scripts")
autostart = _method("App", "_autostart_window_features")
full_stop = _method("App", "_full_stop")
init_parents = _parents(init)
init_src = ast.unparse(init)
render_src = _body_src(render)


print("\n---- three surfaces, registered once ----")
check("every dock method exists",
      all((init, build_rail, build_strip, strip_place, strip_geometry,
           strip_controller, render, state, click, show, restore, paint_entry,
           scripts_fn, console_mount)),
      repr({"rail": bool(build_rail), "strip": bool(build_strip),
            "place": bool(strip_place), "geometry": bool(strip_geometry),
            "controller": bool(strip_controller), "render": bool(render),
            "state": bool(state), "click": bool(click), "show": bool(show),
            "restore": bool(restore), "paint": bool(paint_entry),
            "scripts": bool(scripts_fn), "console": bool(console_mount)}))
check("DOCK_SPEC names exactly three surfaces, in rail order",
      A.DOCK_SPEC == (("dashboard", "Dashboard"), ("console", "Console"),
                      ("scripts", "Scripts")), repr(A.DOCK_SPEC))
check("DOCK_KEYS is derived from it, so the two cannot drift",
      A.DOCK_KEYS == tuple(key for key, _label in A.DOCK_SPEC)
      and len(set(A.DOCK_KEYS)) == 3, repr(A.DOCK_KEYS))
check("the Dashboard is first, so a fresh install opens on it",
      A.DOCK_KEYS[0] == "dashboard")
registry = [node for node in ast.walk(init)
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            and _name(node.targets[0]) == "self._dock_surfaces"
            and isinstance(node.value, ast.Dict) and node.value.keys]
check("the registry is filled in exactly one place", len(registry) == 1,
      f"{len(registry)} populated assignments")
check("...and it registers exactly the keys the spec names, in spec order",
      len(registry) == 1
      and [key.value for key in registry[0].value.keys] == list(A.DOCK_KEYS),
      repr([] if not registry
           else [key.value for key in registry[0].value.keys]))


print("\n---- law 10: siblings, never nesting ----")
# The whole of law 10 in this layout is the parent map. `body` holds the rail
# and the three surfaces, side by side; no surface is built on another surface,
# so no scroller can ever be inside another scroller on the same axis.
surface_locals = {"dashboard": "main", "console": "cons", "scripts": "scripts"}
for key, local in surface_locals.items():
    check(f"the {key} surface is a direct child of the body",
          init_parents.get(local) == "body",
          f"{local} was built on {init_parents.get(local)!r}")
check("no surface is built inside another surface",
      not any(init_parents.get(a) == b
              for a in surface_locals.values()
              for b in surface_locals.values() if a != b),
      repr({local: init_parents.get(local)
            for local in surface_locals.values()}))
body_ctor = [node for node in ast.walk(init)
             if isinstance(node, ast.Assign) and len(node.targets) == 1
             and _name(node.targets[0]) == "body"
             and isinstance(node.value, ast.Call)]
check("the body that holds them all is a plain Frame on the app's own frame "
      "-- not a Canvas, so the container of the surfaces cannot scroll either",
      len(body_ctor) == 1
      and _name(body_ctor[0].value.func) == "tk.Frame"
      and init_parents.get("body") == "full",
      repr([_name(node.value.func) for node in body_ctor]))
check("the rail is built on whatever it is handed, and what it is handed is "
      "the STRIP -- not the body, which now holds surfaces and nothing else",
      _parents(build_rail).get("rail") == "parent"
      and _parents(build_strip).get("strip") is not None
      and "self._build_dock_rail(strip)" in _body_src(build_strip),
      repr(_parents(build_rail).get("rail")))
check("...so nothing in __init__ builds a rail into the body any more",
      "self._build_dock_rail(body)" not in init_src
      and "self._build_dock_strip()" in init_src)

rail_src = _body_src(build_rail)
strip_src = _body_src(build_strip)
check("THE RAIL DOES NOT SCROLL: no scroller, no canvas, no scroll wiring",
      not _calls(build_rail, "ttk.Scrollbar")
      and not _calls(build_rail, "tk.Canvas")
      and not _calls(build_rail, "tk.Text")
      and "yscrollcommand" not in rail_src
      and "yview" not in rail_src, rail_src)
# LAW 10 IN THE STRIP, which is where it could most easily have been broken: a
# dock window with a scroller in it would be a scrolling surface holding the
# only controls that reach the other surfaces.
check("THE STRIP DOES NOT SCROLL EITHER, and holds nothing but the rail -- so "
      "no surface is nested inside it and no wheel can be hijacked in it",
      not _calls(build_strip, "ttk.Scrollbar")
      and not _calls(build_strip, "tk.Canvas")
      and not _calls(build_strip, "tk.Text")
      and strip_src.count("self._build_") == 1, strip_src)
check("no surface is built into the strip: the three are on the body, in the "
      "app's own window, and containment is what law 10 is about",
      not any(_name(node.args[0]) == "strip"
              for node in _calls(init, "tk.Frame") if node.args))
check("the rail is always visible: it is packed unconditionally, and nothing "
      "ever forgets it",
      "rail.pack(" in rail_src and "rail.pack_forget" not in SOURCE
      and "self._dock_rail.pack_forget" not in SOURCE, rail_src)

# THE RAIL IS SIZED TO ITS CONTENT, NOT TO A WINDOW. This is the pack keyword
# Doug's sentence comes down to: it was side="right", fill="y" while the rail
# lived in the app's window, where full height was the only height available.
# The strip is placed from winfo_reqheight, so a rail that filled vertically
# would report the height it was last placed at back as its own content height
# and grow on every pass.
rail_packs = {}
for _node in ast.walk(build_rail):
    if (isinstance(_node, ast.Call) and isinstance(_node.func, ast.Attribute)
            and _node.func.attr == "pack"):
        _kw = {}
        for _word in _node.keywords:
            try:
                _kw[_word.arg] = ast.literal_eval(_word.value)
            except (ValueError, TypeError):
                _kw[_word.arg] = "<expression>"
        rail_packs[_name(_node.func.value)] = _kw
check("THE RAIL IS CONTENT-HEIGHT: it takes the top of the strip and its own "
      "natural height -- fill='x', never fill='y', and never expand",
      rail_packs.get("rail", {}).get("side") == "top"
      and rail_packs.get("rail", {}).get("fill") == "x"
      and not rail_packs.get("rail", {}).get("expand"),
      repr(rail_packs.get("rail")))
# ...and nothing inside it CLAIMS SURPLUS HEIGHT, which is a narrower rule than
# "nothing fills y" and is the one that matters. The accent stripe is packed
# fill="y" on purpose: it fills the height its row was already given, and a row
# is as tall as its button. What would stretch the block is expand=True with a
# vertical fill -- a widget asking the packer for cavity it has not earned.
check("...and nothing inside it claims surplus HEIGHT: fill='y' on a stripe "
      "fills a row that is already content-tall, but expand with a vertical "
      "fill would stretch the whole block from the inside",
      not any(row.get("expand") is True and row.get("fill") in ("y", "both")
              for name, row in rail_packs.items()),
      repr(rail_packs))
check("the strip's height comes from the rail's REQUESTED height, never from "
      "the height it was last placed at -- that second reading is a loop that "
      "only grows",
      "winfo_reqheight()" in _body_src(_method("App", "_rail_height"))
      and "winfo_height()" not in _body_src(_method("App", "_rail_height")),
      _body_src(_method("App", "_rail_height")))
check("the rail carries the wordmark above the entries, which is what makes it "
      "the top of a dock rather than a floating list of three words",
      any(keyword.arg == "text"
          and isinstance(keyword.value, ast.Constant)
          and keyword.value.value in ("Esoteric", "OS")
          for node in _calls(build_rail, "tk.Label")
          for keyword in node.keywords), rail_src)
check("...and it is one row per surface under it, from DOCK_SPEC and not from "
      "a second list", "for key, label in DOCK_SPEC:" in rail_src, rail_src)

scroll_owners = {}
for _cls in [n for n in ast.walk(MODULE) if isinstance(n, ast.ClassDef)]:
    for _item in _cls.body:
        if not isinstance(_item, ast.FunctionDef):
            continue
        count = len(_calls(_item, "ttk.Scrollbar"))
        if count:
            scroll_owners[f"{_cls.name}.{_item.name}"] = count
check("exactly three vertical scrollers in the whole app, one per surface, and "
      "no surface owns two",
      scroll_owners == {"App.__init__": 1, "App._console_mount": 1,
                        "App._build_scripts_surface": 1},
      repr(scroll_owners))
check("the Dashboard's scroller is built on the Dashboard's own cavity",
      all(_name(node.args[0]) == "main"
          for node in _calls(init, "ttk.Scrollbar")))
check("the Console's scroller is built inside the Console surface",
      _parents(console_mount).get("wrap") == "parent"
      and "self._dock_surfaces.get('console')"
      in _body_src(console_mount)
      and all(_name(node.args[0]) == "wrap"
              for node in _calls(console_mount, "ttk.Scrollbar")))
# LAW 10 FOR THE THIRD SURFACE. It has content now, so it has a scroller, and
# the whole of its compliance is that it has exactly ONE and that one lives on
# a frame the surface owns -- never on another surface, never inside the page.
scripts_parents = _parents(scripts_fn)
check("the Scripts surface owns exactly ONE vertical scroller",
      len(_calls(scripts_fn, "ttk.Scrollbar")) == 1,
      repr(len(_calls(scripts_fn, "ttk.Scrollbar"))))
check("...and it is built on a frame inside the surface it was handed, so it "
      "is a sibling of the other two scrollers and inside neither",
      scripts_parents.get("wrap") == "parent"
      and all(_name(node.args[0]) == "wrap"
              for node in _calls(scripts_fn, "ttk.Scrollbar"))
      and scripts_parents.get("text") == "wrap", repr(scripts_parents))
check("...and there is exactly one scrollable widget under it: one Text, no "
      "Canvas, so nothing inside the scroller can scroll on the same axis",
      len(_calls(scripts_fn, "tk.Text")) == 1
      and not _calls(scripts_fn, "tk.Canvas"), _body_src(scripts_fn))
check("the head row is a plain Frame on the surface and scrolls nothing -- "
      "Reload and the switch stay put while the report moves",
      scripts_parents.get("head") == "parent"
      and not any(_name(node.args[0]) == "head"
                  for node in _calls(scripts_fn, "ttk.Scrollbar")),
      repr(scripts_parents.get("head")))
check("the painter and the decider build no widget at all, so neither can "
      "smuggle in a second scroller as the report grows",
      not any(_calls(fn, ctor)
              for fn in (scripts_refresh, scripts_report)
              for ctor in ("ttk.Scrollbar", "tk.Canvas", "tk.Text",
                           "tk.Frame", "tk.Label", "ttk.Button")))
check("the wheel handler still claims only the page canvas -- the Console's "
      "own Text keeps its class binding, and needs no exemption",
      "self._inside(widget, self._page_canvas)"
      in _body_src(_method("App", "_on_page_mousewheel")))


print("\n---- no pop-outs anywhere ----")
# TWO WINDOWS, AND THE TEST FOR BOTH IS WHO PLACES IT. Windows places a dialog,
# at a moment of its choosing, on a screen he was not looking at (28 July);
# this process places these, at rects it computed, and refuses every move it
# did not make. The strip is the second and it is a dock, not a dialog: it
# cannot be inside the app's window because its job is to show and hide that
# window, and a dock drawn inside the thing it hides is gone at exactly the
# moment it is needed. The reason is written where the ban is enforced --
# test_frame_modal.EXEMPT_FUNCTIONS -- and this is the same list from here.
toplevel_owners = {node.name for node in ast.walk(MODULE)
                   if isinstance(node, ast.FunctionDef)
                   and any(isinstance(call, ast.Call)
                           and _name(call.func) == "tk.Toplevel"
                           for call in ast.walk(node))}
check("exactly two methods in the app open a window: the identify card and "
      "the dock strip",
      toplevel_owners == {"_identify_card", "_build_dock_strip"},
      repr(sorted(toplevel_owners)))
check("...one Toplevel each, so an exemption is a window and not a licence",
      len([node for node in ast.walk(MODULE) if isinstance(node, ast.Call)
           and _name(node.func) == "tk.Toplevel"]) == len(toplevel_owners))
check("no SURFACE method opens one -- a fourth surface must never need a "
      "window, and the rail that switches them does not have one of its own",
      not (toplevel_owners & {"_build_dock_rail", "_render_dock",
                              "_dock_click", "_dock_show", "_dock_state",
                              "_restore_dock", "_console_mount",
                              "_build_scripts_surface", "_scripts_refresh",
                              "_scripts_report", "_scripts_reload",
                              "_toggle_scripts", "_start_scripts",
                              "_script_host"}))
check("the strip is built ONCE, at startup, and never opened again -- a dock "
      "built on first use is a dock that does not exist in the one state that "
      "needs it, which is a hidden app window",
      SOURCE.count("self._build_dock_strip()") == 1
      and "self._build_dock_strip()" in init_src, init_src[:0])
check("the builder places nothing itself: no geometry, no overrideredirect, "
      "no topmost attribute -- on_desktop owns where both windows go",
      not any(token in strip_src for token in
              ("geometry(", "overrideredirect", "-topmost", "attributes(")),
      strip_src)
check("...and it steals nothing: no focus_force, no grab, no lift, because a "
      "dock that takes the keyboard is a dialog wearing a dock's shape",
      not any(token in strip_src for token in
              ("focus_force", "grab_set", "lift(", "wait_window")), strip_src)
check("nothing closes the dock: WM_DELETE_WINDOW is answered rather than left "
      "to Tk's destroy, because it is the only way back to a hidden window",
      "protocol('WM_DELETE_WINDOW'" in strip_src, strip_src)
check("it comes up withdrawn, so it never flashes at Tk's default position on "
      "some other screen before _strip_place puts it on the edge",
      "strip.withdraw()" in strip_src, strip_src)
check("the console's window era is gone by name, not merely unreachable",
      "_open_console_window" not in SOURCE
      and "_close_console_window" not in SOURCE
      and "_console_win" not in SOURCE and "_console_geom" not in SOURCE)


print("\n---- the strip is placed by the machinery that already places the app ----")
# EXTENDED, NOT DUPLICATED. Everything the strip needs -- an edge, a work area,
# a refusal of foreign moves, a re-dock when the desk changes underneath it --
# on_desktop already did for the app window. A second placement path would be a
# second thing to keep in step with the first, and the failure would be silent:
# a dock that is merely somewhere else.
strip_ctl_src = _body_src(strip_controller)
strip_geom_src = _body_src(strip_geometry)
strip_place_src = _body_src(strip_place)
check("the strip is placed by on_desktop.OnDesktop -- the same controller the "
      "app window uses", "on_desktop.OnDesktop(" in strip_ctl_src,
      strip_ctl_src)
check("...pinned TOPMOST rather than to the bottom, because a dock behind "
      "Chrome is a dock nobody can click and it is the way back to a hidden "
      "window", "pin=on_desktop.HWND_TOPMOST" in strip_ctl_src, strip_ctl_src)
check("...and told its height is its content's, so the wndproc's re-dock keeps "
      "the rail's height instead of stretching to a new work area",
      "fixed_height=True" in strip_ctl_src, strip_ctl_src)
check("THE DESKTOP-ROLE SCREEN, resolved by the one resolver the app window "
      "uses, so the two cannot end up on different panels",
      "self._desktop_monitor_name()" in strip_ctl_src
      and "self._desktop_monitor_name()" in strip_geom_src)
check("the strip's rect is on_desktop.dock_rect with a height -- the gap under "
      "it is the module's clamp, not a number retyped here",
      "on_desktop.dock_rect(" in strip_geom_src
      and "height=self._rail_height()" in strip_geom_src, strip_geom_src)
check("the app window is placed against the band the strip stands in, so a "
      "full-width window stops at the dock instead of sliding under it",
      "reserve=self._strip_reserve()"
      in _body_src(_method("App", "_desktop_geometry")))
check("...and that band is the rail's width plus on_desktop's own inset, so "
      "the two docks are not measured against different air",
      "self._rail_width() + on_desktop.INSET"
      in _body_src(_method("App", "_strip_reserve")))
apply_src = _body_src(_method("App", "_apply_window_placement"))
check("the strip is placed on every placement pass, BEFORE the app window "
      "whose rect is computed against it",
      apply_src.index("self._strip_place()") < apply_src.index("ctl.set_reserve"),
      apply_src)
check("float_window frees the APP window and never the dock: nothing releases "
      "the strip, so there is no state in which the dock is somewhere else",
      "release" not in strip_place_src, strip_place_src)
check("the strip states its floor before every placement, for the reason "
      "on_desktop.set_min_width exists: the wndproc re-docks from cached "
      "numbers and a stale floor widens the dock on the next shell bar",
      "set_min_width" in strip_place_src, strip_place_src)
strip_users = {node.name for node in ast.walk(MODULE)
               if isinstance(node, ast.FunctionDef)
               and "self._strip_controller()" in _body_src(node)}
check("one place reaches the strip's controller, so there is one place the "
      "dock is put on screen", strip_users == {"_strip_place"},
      repr(sorted(strip_users)))
sync_src = _body_src(_method("App", "_sync_desktop_monitor"))
check("DESKTOP ROLE CHANGED: both windows are re-pointed in the one funnel "
      "every role change already passes through -- a second watcher is a "
      "second thing that can disagree with the first",
      "self._desktop.set_monitor(name)" in sync_src
      and "self._strip.set_monitor(name)" in sync_src, sync_src)
check("...and that funnel is the only place either is re-pointed",
      {node.name for node in ast.walk(MODULE)
       if isinstance(node, ast.FunctionDef)
       and "set_monitor(" in _body_src(node)} == {"_sync_desktop_monitor"})


print("\n---- one writer, one surface showing ----")
check("_render_dock forgets before it packs, so two are never in the cavity",
      render_src.index("pack_forget") < render_src.index("surface.pack("),
      render_src)
touchers = {node.name for node in ast.walk(MODULE)
            if isinstance(node, ast.FunctionDef)
            and "self._dock_surfaces" in _body_src(node)}
check("only these methods know the surface registry exists at all",
      touchers == {"__init__", "_render_dock", "_dock_show", "_dock_click",
                   "_restore_dock", "_console_mount"},
      repr(sorted(touchers)))
packers = {name for name in touchers
           if "pack_forget()" in _body_src(_method("App", name))
           or "surface.pack(" in _body_src(_method("App", name))}
check("...and of those, only _render_dock packs or unpacks one",
      packers == {"_render_dock"}, repr(sorted(packers)))
check("the console is mounted from the renderer, so a restored console is "
      "built exactly like a clicked one",
      "self._console_mount()" in render_src
      and render_src.count("self._console_mount()") == 1, render_src)
check("Scripts is repainted from the renderer for the same reason: what it "
      "shows changed while it was hidden",
      "self._scripts_refresh()" in render_src
      and render_src.count("self._scripts_refresh()") == 1, render_src)


print("\n---- the state persists where every other preference does ----")
state_src = _body_src(state)
check("_dock_state writes both keys and is the only writer",
      "save_setting('dock_surface', self._dock_active)" in state_src
      and "save_setting('dock_collapsed', self._dock_collapsed)" in state_src
      and SOURCE.count("save_setting(\"dock_surface\"") == 1
      and SOURCE.count("save_setting(\"dock_collapsed\"") == 1, state_src)
check("_restore_dock reads and never writes, so coming up is not a change",
      "save_setting" not in _body_src(restore)
      and "load_setting('dock_surface'" in _body_src(restore)
      and "load_setting('dock_collapsed'" in _body_src(restore),
      _body_src(restore))
check("it persists through openspan_settings.json, the app's one settings "
      "file, and invents no store of its own",
      "load_setting" in _body_src(restore)
      and not [node for node in ast.walk(MODULE)
               if isinstance(node, ast.Constant)
               and isinstance(node.value, str)
               and node.value.endswith(".json") and "dock" in node.value])
check("the restore happens at the END of __init__, after the page has been "
      "measured in the state it has always been measured in",
      init_src.index("self._restore_dock()")
      > init_src.index("page_window_plan(avail_h)"))


print("\n---- Scripts: two controls, one report, still no editor ----")
scripts_src = _body_src(scripts_fn)
check("the empty state is one module-level constant, so it can be read here",
      isinstance(A.SCRIPTS_EMPTY_STATE, tuple)
      and all(isinstance(line, str) and line for line in
              A.SCRIPTS_EMPTY_STATE), repr(A.SCRIPTS_EMPTY_STATE))
check("it says plainly that there is nothing, rather than opening a tour",
      "No scripts found" in A.SCRIPTS_EMPTY_STATE[0]
      and "nothing runs" in A.SCRIPTS_EMPTY_STATE[0],
      repr(A.SCRIPTS_EMPTY_STATE[0]))
check("it names the location he has to write in",
      any("scripts directory" in line for line in A.SCRIPTS_EMPTY_STATE),
      repr(A.SCRIPTS_EMPTY_STATE))
check("...and the file extension and the load order, so the directory is "
      "actionable rather than merely named",
      all(token in " ".join(A.SCRIPTS_EMPTY_STATE)
          for token in (".eos", "filename order")))
check("it points at the format document by name",
      A.SCRIPTS_FORMAT_DOC.endswith("SCRIPTS-FORMAT.md")
      and any(A.SCRIPTS_FORMAT_DOC in line
              for line in A.SCRIPTS_EMPTY_STATE), A.SCRIPTS_FORMAT_DOC)
check("the format document it points at actually exists on disk",
      os.path.isfile(os.path.join(
          os.path.dirname(HERE), *A.SCRIPTS_FORMAT_DOC.replace("\\", "/")
          .split("/"))), A.SCRIPTS_FORMAT_DOC)
check("it still promises no editor, because there still is not one",
      "no editor here" in " ".join(A.SCRIPTS_EMPTY_STATE))
check("the switched-off state is its own constant and says the feature is off "
      "rather than showing a list that cannot fire",
      isinstance(A.SCRIPTS_DISABLED_STATE, tuple)
      and "switched off" in A.SCRIPTS_DISABLED_STATE[0]
      and "No chord is intercepted" in A.SCRIPTS_DISABLED_STATE[0],
      repr(A.SCRIPTS_DISABLED_STATE))
check("...and it names the one action that changes that",
      any("Turn on scripts" in line for line in A.SCRIPTS_DISABLED_STATE))

# THE EXAMPLE SCRIPT IS RUN THROUGH THE REAL PARSER. It is written to Doug's
# disk on first run, so a typo in it would greet him as an error report on a
# file he never wrote. This is the check that stops that.
example = SE.parse_script(A.SCRIPTS_EXAMPLE, A.SCRIPTS_EXAMPLE_NAME)
check("the seeded example parses with no problems at all",
      not example.problems,
      repr([problem.describe() for problem in example.problems]))
check("...and it binds something, so first run is not another empty file",
      len(example.bindings) >= 1,
      repr([binding.describe() for binding in example.bindings]))
check("...and what it binds is harmless and reversible: no send, no catalog",
      all(action.verb in (SE.Verb.WINDOW, SE.Verb.PASS)
          for binding in example.bindings for action in binding.actions),
      repr([binding.describe() for binding in example.bindings]))
check("the example is named with a number, so filename order is visible from "
      "the first file he ever sees",
      A.SCRIPTS_EXAMPLE_NAME.endswith(SE.EXTENSION)
      and A.SCRIPTS_EXAMPLE_NAME[0].isdigit(), A.SCRIPTS_EXAMPLE_NAME)
check("it is mostly comment: the format is taught in the file, not guessed",
      sum(1 for line in A.SCRIPTS_EXAMPLE.splitlines()
          if line.lstrip().startswith("#")) > len(example.bindings))

# TWO CONTROLS, AND EXACTLY TWO. An editor, a run button or a file picker here
# would each be a second way to do something the format already does better.
scripts_buttons = _calls(scripts_fn, "ttk.Button")
check("the surface builds exactly two buttons", len(scripts_buttons) == 2,
      repr(len(scripts_buttons)))
check("...and they are Reload and the switch, nothing else",
      sorted(keyword.value.value for node in scripts_buttons
             for keyword in node.keywords if keyword.arg == "text")
      == ["Reload", "Turn on scripts"],
      repr([keyword.value.value for node in scripts_buttons
            for keyword in node.keywords if keyword.arg == "text"]))
check("Reload is wired to the app's reload path and the switch to the toggle",
      "command=self._scripts_reload" in scripts_src
      and "command=self._toggle_scripts" in scripts_src, scripts_src)
check("there is still no editor: no entry, no list, no tree, no menu",
      not any(_calls(scripts_fn, ctor) for ctor in
              ("ttk.Entry", "tk.Entry", "tk.Listbox", "ttk.Treeview",
               "ttk.Combobox", "tk.Checkbutton", "ttk.Checkbutton",
               "tk.Menu")), scripts_src)
check("the report Text is read-only, so the surface cannot be typed into",
      "state='disabled'" in scripts_src, scripts_src)
check("nothing in the app pretends a script exists: the rows come from the "
      "engine or there are no rows",
      "script_rows" not in SOURCE and "run_script" not in SOURCE)


print("\n---- the engine's lifetime: one owner, one start, one stop ----")
# The hotkey host is the worked example and this is the same shape: built on
# first use, started only from a named place, stopped with the other hook owner
# on the way out. The whole point is that CONSTRUCTION is free -- no hook, no
# thread, no file -- so the object may exist all session while the keyboard is
# untouched.
host_src = _body_src(script_host)
builders = {node.name for node in ast.walk(MODULE)
            if isinstance(node, ast.FunctionDef)
            and _calls(node, "script_engine.ScriptEngine")}
check("the engine is constructed in exactly one method, and it is the lazy "
      "accessor", builders == {"_script_host"}, repr(sorted(builders)))
assigners = {node.name for node in ast.walk(MODULE)
             if isinstance(node, ast.FunctionDef)
             and "self._script_engine =" in _body_src(node)}
check("...and only __init__ (to None) and that accessor ever fill the slot",
      assigners == {"__init__", "_script_host"}, repr(sorted(assigners)))
check("the slot is created before _restore_dock can repaint onto Scripts, so "
      "a session that ended on Scripts does not come back to an "
      "AttributeError",
      init_src.index("self._script_engine = None")
      < init_src.index("self._restore_dock()"))
check("CONSTRUCTION IS SIDE-EFFECT-FREE: the accessor starts nothing, reloads "
      "nothing and seeds nothing",
      not _calls(script_host, "engine.start")
      and not _calls(script_host, "engine.reload")
      and "self._scripts_seed" not in host_src, host_src)
check("the accessor imports the engine module inside itself, so importing the "
      "app installs no hook and costs no import",
      any(isinstance(node, ast.Import)
          and any(alias.name == "script_engine" for alias in node.names)
          for node in ast.walk(script_host))
      and not any(isinstance(node, (ast.Import, ast.ImportFrom))
                  and "script_engine" in ast.unparse(node)
                  for node in MODULE.body), host_src)
check("THE DECLARATION IS REGISTERED on the way in, before anything can ask "
      "whether the feature is on",
      bool(_calls(script_host, "script_engine.ScriptEngine.ensure_declaration")),
      host_src)
starters = {node.name for node in ast.walk(MODULE)
            if isinstance(node, ast.FunctionDef)
            and _calls(node, "engine.start")}
check("only the switch and the startup arm ever start the engine",
      starters == {"_toggle_scripts", "_start_scripts"}, repr(sorted(starters)))
check("both of them go through the one accessor rather than building their own",
      all("self._script_host()" in _body_src(_method("App", name))
          for name in starters))
check("the startup arm is armed exactly once, beside the other hook features",
      [_name(node) for node in ast.walk(autostart)
       if isinstance(node, ast.Attribute)
       and _name(node) == "self._start_scripts"] == ["self._start_scripts"],
      _body_src(autostart))
check("scripts are OPT-IN: the arm consults the feature flag and starts "
      "nothing when it is off",
      "self._scripts_enabled(engine)" in _body_src(start_scripts)
      and SE.FEATURE_DECLARATION.default_enabled is False,
      _body_src(start_scripts))
check("every path that changes whether the engine is running repaints the "
      "surface afterwards, so live state is live",
      all("self._scripts_refresh()" in _body_src(fn)
          for fn in (start_scripts, toggle_scripts, scripts_reload)))
stop_src = _body_src(full_stop)
check("the engine is STOPPED on the way out, beside the other hook owner",
      "_scripts.stop()" in stop_src and "_script_engine" in stop_src, stop_src)
check("...and it comes out before the VM, the tray and the child processes, "
      "for the same reason the hotkey host does: a low-level hook outliving "
      "its process is how a desk ends up with dead chords",
      stop_src.index("_scripts.stop()") < stop_src.index("self._tray"),
      stop_src)
stoppers = {node.name for node in ast.walk(MODULE)
            if isinstance(node, ast.FunctionDef)
            and ("_scripts.stop()" in _body_src(node)
                 or _calls(node, "engine.stop"))}
check("stopping happens in exactly two places: the switch and the full stop",
      stoppers == {"_toggle_scripts", "_full_stop"}, repr(sorted(stoppers)))
check("RELOAD IS WIRED TO reload(), in one place, and it does not start or "
      "stop anything",
      bool(_calls(scripts_reload, "engine.reload"))
      and not _calls(scripts_reload, "engine.start")
      and not _calls(scripts_reload, "engine.stop"),
      _body_src(scripts_reload))
reloaders = {node.name for node in ast.walk(MODULE)
             if isinstance(node, ast.FunctionDef)
             and _calls(node, "engine.reload")}
check("...and it is the only caller of reload() in the app",
      reloaders == {"_scripts_reload"}, repr(sorted(reloaders)))

# The declaration itself, against the real registry rather than a description
# of one. ensure_declaration is what the app calls, so this is what the app
# does.
registry_probe = FeatureRegistry()
SE.ScriptEngine.ensure_declaration(registry_probe)
check("ensure_declaration registers native-scripts in an empty registry",
      registry_probe.get(SE.FEATURE_ID) is SE.FEATURE_DECLARATION,
      SE.FEATURE_ID)
SE.ScriptEngine.ensure_declaration(registry_probe)
check("...and calling it a second time is a no-op rather than a ValueError",
      len(registry_probe.features) == 1,
      repr([item.id for item in registry_probe.features]))
check("the declaration discloses the hook it joins, so the settings service "
      "can say so without asking the engine",
      any("WH_KEYBOARD_LL" in item
          for item in SE.FEATURE_DECLARATION.input_hooks),
      repr(SE.FEATURE_DECLARATION.input_hooks))
check("the engine sits below the tiling host, so a hand-written script can "
      "take a chord back from a shipped default",
      SE.DEFAULT_PRIORITY < 50, repr(SE.DEFAULT_PRIORITY))


# =========================================================================
# BEHAVIOUR, WITH FAKES. Nothing below builds a widget: the shipped methods
# are driven against recorders, so switching, collapsing and persistence are
# proved rather than described.
# =========================================================================

class FakeSurface:
    def __init__(self):
        self.packed = False
        self.packs = 0

    def pack(self, **_kwargs):
        self.packed = True
        self.packs += 1

    def pack_forget(self):
        self.packed = False


class FakeChrome:
    """A rail mark or a rail button: remembers only its last colours."""

    def __init__(self):
        self.bg = None
        self.fg = None

    def config(self, **kwargs):
        self.bg = kwargs.get("bg", self.bg)
        self.fg = kwargs.get("fg", self.fg)


class FakeRail:
    """A rail wider than DOCK_STRIP_MIN_W and taller than DOCK_STRIP_MIN_H, so
    both max() calls are real choices -- and far shorter than a work area,
    because being shorter than the window is the whole point of it."""

    def winfo_reqwidth(self):
        return 130

    def winfo_reqheight(self):
        return 186


RAIL_W = FakeRail().winfo_reqwidth()
RAIL_H = FakeRail().winfo_reqheight()


class FakeRoot:
    """A top level that really does go away when it is told to.

    shown is written by withdraw/deiconify, because the whole of "collapsed"
    is now whether this window is on screen: a fake that recorded the call and
    stayed visible would pass a _dock_place that did nothing. geometry() is
    still here and still writes the width back, so that a dock which started
    resizing the window again would be caught rather than ignored.
    """

    def __init__(self, width=A.PAGE_PREFERRED_WINDOW_W, height=930):
        self.minimums = [(A.PAGE_MIN_WINDOW_W, 680)]
        self.width = width
        self.height = height
        self.shown = True
        self.calls = []       # every call that matters, in the order it came

    def minsize(self, width=None, height=None):
        if width is None:
            return self.minimums[-1]
        self.minimums.append((width, height))
        self.calls.append(("minsize", width))
        return None

    def geometry(self, spec):
        self.calls.append(("geometry", spec))
        wide, _sep, high = str(spec).partition("x")
        # Tk clamps a geometry request to the current minimum, and so does this.
        self.width = max(int(wide), self.minimums[-1][0])
        self.height = int(high)

    def withdraw(self):
        self.shown = False
        self.calls.append(("withdraw", None))

    def deiconify(self):
        self.shown = True
        self.calls.append(("deiconify", None))

    def winfo_width(self):
        return self.width

    def winfo_height(self):
        return self.height


class FakeDesktop:
    """OnDesktop as _dock_place uses it.

    show_at_dock is the whole of the desktop half now: a window pinned to
    HWND_BOTTOM cannot be raised by deiconify alone, because the app's own
    wndproc sinks every lift, so a dock that only deiconified would put the
    window up UNDERNEATH whatever was in front of it and look like nothing
    happened. set_min_width and set_width are kept, clamping exactly as the
    shipped controller does, so a dock that started resizing the app window
    again is caught here rather than on the desk.
    """

    def __init__(self, active=True):
        self.active = active
        self.min_width = OD.MIN_WIDTH
        self.width = None
        self.floors = []
        self.widths = []
        self.shows = 0

    def set_min_width(self, width):
        self.min_width = max(int(width), OD.COLLAPSED_MIN_WIDTH)
        self.floors.append(self.min_width)
        return self.min_width

    def set_width(self, width):
        self.width = max(int(width), self.min_width)
        self.widths.append(self.width)
        return True

    def show_at_dock(self):
        self.shows += 1
        return True


def new_app(active="dashboard", collapsed=False, desktop=None,
            width=A.PAGE_PREFERRED_WINDOW_W):
    app = A.App.__new__(A.App)
    app.root = FakeRoot(width=width)
    app._desktop = desktop
    app._dock_rail = FakeRail()
    app._dock_strip = None     # the strip's own placement is asserted apart
    app._strip = None
    app._dock_surfaces = {key: FakeSurface() for key in A.DOCK_KEYS}
    app._dock_entries = {key: (FakeChrome(), FakeChrome())
                         for key in A.DOCK_KEYS}
    app._dock_active = active
    app._dock_collapsed = collapsed
    app._console_mount = lambda: mounted.append(app._dock_active)
    app._scripts_refresh = lambda: repainted.append(app._dock_active)
    return app


def packed(app):
    return {key for key, frame in app._dock_surfaces.items() if frame.packed}


saved = []
mounted = []
repainted = []
A.save_setting = lambda key, value: saved.append((key, value))


print("\n---- exactly one surface at a time ----")
app = new_app()
app._render_dock()
check("the Dashboard comes up showing, alone", packed(app) == {"dashboard"},
      repr(packed(app)))
app._dock_click("console")
check("clicking Console replaces it -- the Dashboard is not beside it, it is "
      "gone", packed(app) == {"console"}, repr(packed(app)))
check("and the console's view is mounted on the way in", mounted == ["console"])
app._dock_click("scripts")
check("clicking Scripts replaces THAT", packed(app) == {"scripts"},
      repr(packed(app)))
check("and Scripts is repainted from the engine on the way in, so it is never "
      "a view of a state the engine has left",
      repainted == ["scripts"], repr(repainted))
app._dock_click("console")
check("coming back to the console does not build a second view",
      packed(app) == {"console"} and mounted == ["console", "console"])
check("no surface is ever packed twice over without being forgotten first",
      all(frame.packs <= 3 for frame in app._dock_surfaces.values()))
check("an unknown key changes nothing at all",
      app._dock_click("nope") is False and packed(app) == {"console"}
      and app._dock_active == "console")


print("\n---- collapse, and the strip that survives it ----")
app = new_app()
app._render_dock()
check("clicking the ACTIVE entry collapses", app._dock_click("dashboard")
      and app._dock_collapsed is True and packed(app) == set(),
      repr(packed(app)))
check("the active surface is REMEMBERED across the collapse, not cleared",
      app._dock_active == "dashboard")
check("the rail is untouched: nothing in the collapse path forgets it, and it "
      "is in a window of its own that a collapse does not reach",
      isinstance(app._dock_rail, FakeRail))
check("THE WINDOW IS HIDDEN, which is what collapsed means since 2026-08-29 -- "
      "the rail is not in here any more, so there is nothing left to shrink to",
      app.root.shown is False, repr(app.root.calls))
check("...and nothing was resized on the way: the window keeps the size Doug "
      "left it at, and gets it back by never having been touched",
      [call for call in app.root.calls if call[0] == "geometry"] == []
      and app.root.width == A.PAGE_PREFERRED_WINDOW_W, repr(app.root.calls))
check("...and no minimum moved either, because no state needs a lower one",
      app.root.minimums == [(A.PAGE_MIN_WINDOW_W, 680)],
      repr(app.root.minimums))
check("clicking it again brings the same surface back, in the same window",
      app._dock_click("dashboard") and packed(app) == {"dashboard"}
      and app._dock_collapsed is False and app.root.shown is True)
check("...at exactly the width it had before the collapse",
      app.root.width == A.PAGE_PREFERRED_WINDOW_W, repr(app.root.width))
app._dock_click("dashboard")
check("clicking a DIFFERENT entry while collapsed shows the window on THAT "
      "surface, rather than staying hidden",
      app._dock_click("scripts") and packed(app) == {"scripts"}
      and app._dock_collapsed is False and app.root.shown is True)

app = new_app(width=1503)
app._dock_click("dashboard")
app._dock_click("dashboard")
check("whatever width he had dragged it to is the width he gets back, and it "
      "is the same width because nothing ever took it away",
      app.root.width == 1503 and app.root.shown is True, repr(app.root.width))
app.root.calls.clear()
app._dock_click("console")
check("switching surfaces without collapsing neither hides nor resizes: the "
      "window was already up and stays exactly as it was",
      app.root.calls == [("deiconify", None)] and app.root.shown is True,
      repr(app.root.calls))

# THE STRIP IS NOT TOUCHED BY ANY OF THIS. It is a separate window with its own
# controller, and a collapse that reached it would put the dock away with the
# app -- which is the one state from which nothing can be clicked back.
strip_touchers = {node.name for node in ast.walk(MODULE)
                  if isinstance(node, ast.FunctionDef)
                  and ("self._dock_strip" in _body_src(node)
                       or "self._strip" in _body_src(node))}
check("nothing in the dock's click, state, render or placement path touches "
      "the strip: it is placed once and left alone",
      not (strip_touchers & {"_dock_click", "_dock_show", "_dock_state",
                             "_render_dock", "_dock_place", "_restore_dock"}),
      repr(sorted(strip_touchers)))


# =========================================================================
# COLLAPSED MEANS THE WINDOW IS GONE. Doug, 2026-08-29, on a full-width window
# holding nothing but the rail: *"This should be fully collapsed, this is not
# a valid state."* That was closed by making the pack and a resize one act.
# The rail leaving the window closed it harder: there is no width to get wrong
# any more, only a window that is up or is not.
# =========================================================================

print("\n---- floating: collapse hides, invoking shows ----")
app = new_app()
app._render_dock()
check("a first render of an expanded dock resizes nothing and leaves the "
      "window up",
      [call for call in app.root.calls if call[0] == "geometry"] == []
      and app.root.shown is True
      and app.root.width == A.PAGE_PREFERRED_WINDOW_W, repr(app.root.calls))
app.root.calls.clear()
app._dock_click("dashboard")
check("COLLAPSING HIDES THE WINDOW, and does exactly that one thing",
      app.root.calls == [("withdraw", None)] and app.root.shown is False,
      repr(app.root.calls))
check("...and the height is untouched too: putting the app away is not a "
      "layout gesture at all", app.root.height == 930, repr(app.root.height))
app.root.calls.clear()
check("SHOWING PUTS IT BACK", app._dock_click("dashboard")
      and app.root.calls == [("deiconify", None)]
      and app.root.shown is True, repr(app.root.calls))

# The restore reads settings, and the app is RUNNING on Doug's desk: the real
# load_setting would read his live openspan_settings.json, so this suite would
# pass or fail depending on which surface he happened to leave showing. Stub it
# for the one call, exactly as the section further down does.
_restore_stored = {"dock_surface": "dashboard", "dock_collapsed": True}
A.load_setting = lambda key, default=None: _restore_stored.get(key, default)
app = new_app(collapsed=True)
app._restore_dock()
check("a session that ended collapsed comes back collapsed AND hidden -- the "
      "strip alone on the edge, which is what he left",
      app._dock_collapsed is True and packed(app) == set()
      and app.root.shown is False, repr(app.root.calls))
check("...and showing it opens at the width the app opens at, because no "
      "collapse ever spent it", app._dock_click("dashboard")
      and app.root.shown is True
      and app.root.width == A.PAGE_PREFERRED_WINDOW_W, repr(app.root.width))


print("\n---- on the desktop, where a hidden window cannot simply be raised ----")
desk = FakeDesktop()
app = new_app(desktop=desk)
app._render_dock()
check("showing on the desktop goes through show_at_dock, not deiconify alone: "
      "the app is pinned to HWND_BOTTOM and its own wndproc sinks every lift, "
      "so a bare deiconify would put it up UNDER whatever was in front",
      desk.shows == 1 and app.root.shown is True, repr(desk.shows))
check("...and the dock resizes nothing on the desktop either: no floor is "
      "lowered and no width is asked for, because neither exists any more",
      desk.floors == [] and desk.widths == [], repr((desk.floors, desk.widths)))
check("...and nothing reaches geometry(), which OnDesktop would refuse anyway",
      [call for call in app.root.calls if call[0] == "geometry"] == [],
      repr(app.root.calls))
app._dock_click("dashboard")
check("COLLAPSING ON THE DESKTOP HIDES IT, and does not ask the controller for "
      "anything at all",
      app.root.shown is False and desk.shows == 1
      and desk.widths == [], repr((app.root.calls, desk.widths)))
app._dock_click("dashboard")
check("...and showing it again is one more show_at_dock",
      app.root.shown is True and desk.shows == 2)

dormant = FakeDesktop(active=False)
app = new_app(desktop=dormant, collapsed=True)
app._dock_click("dashboard")
check("a controller that is NOT on the desktop -- float_window is on, or "
      "placement failed -- is not asked; deiconify is the whole of it",
      dormant.shows == 0 and app.root.shown is True, repr(app.root.calls))
app = new_app(collapsed=True)
app._desktop = None
app._dock_click("dashboard")
check("...and so does one with no controller built yet, which is every render "
      "before the 300ms placement", app.root.shown is True,
      repr(app.root.calls))


print("\n---- the invalid state is unreachable, not merely unlikely ----")
# THE INVARIANT, over every sequence the rail can produce: nothing packed IF
# AND ONLY IF the app window is off screen. The screenshot Doug sent was the
# left half of that biconditional without the right, and no ordering of clicks
# below can reproduce it, because one method writes both halves. The right half
# used to be "at the rail's width"; it is "not on screen" now, and the shape of
# the assertion is deliberately unchanged.
SEQUENCES = (("dashboard",), ("dashboard", "dashboard"),
             ("console", "console", "console"),
             ("dashboard", "scripts", "scripts", "dashboard"),
             ("scripts", "scripts", "console", "console", "scripts"),
             ("console", "scripts", "scripts", "scripts", "dashboard"))


def walk_states(probe, keys):
    """Every state one click sequence passes through, as (empty, hidden)."""
    probe._render_dock()
    seen = [(packed(probe) == set(), probe.root.shown is False)]
    for key in keys:
        probe._dock_click(key)
        seen.append((packed(probe) == set(), probe.root.shown is False))
    return seen


for start in (False, True):
    for keys in SEQUENCES:
        probe = new_app(collapsed=start)
        states = walk_states(probe, keys)
        check(f"floating, {'collapsed' if start else 'showing'} then "
              f"{' > '.join(keys)}: empty exactly when hidden, at every step",
              all(empty == hidden for empty, hidden in states), repr(states))
        check(f"...and every one of those {len(states)} states is one of the "
              f"two that exist: a surface in a window on screen, or the strip "
              f"alone", set(states) <= {(False, False), (True, True)},
              repr(states))
for start in (False, True):
    for keys in SEQUENCES:
        desk = FakeDesktop()
        probe = new_app(collapsed=start, desktop=desk)
        states = walk_states(probe, keys)
        check(f"on the desktop, {'collapsed' if start else 'showing'} then "
              f"{' > '.join(keys)}: the same, and the window is never resized "
              f"on the way",
              all(empty == hidden for empty, hidden in states)
              and desk.widths == [], repr(states))
        check(f"...and every show on that path went through show_at_dock, so "
              f"none of them raised a window that stayed behind another",
              desk.shows == len([s for s in states if not s[1]]),
              f"{desk.shows} shows for {states}")

# ...and the reason it is unreachable, read off the source rather than trusted:
# ONE method writes visibility, and it writes it in the same call as the pack.
place_src = _body_src(_method("App", "_dock_place"))
resizers = {node.name for node in ast.walk(MODULE)
            if isinstance(node, ast.FunctionDef)
            and "self.root.geometry(" in _body_src(node)}
check("exactly two methods resize or move the app's own window, and NEITHER is "
      "about the dock -- a collapse spends no width, so none can be lost",
      resizers == {"__init__", "_drag_move"}, repr(sorted(resizers)))
check("...and the drag is a MOVE, with no size in the spec at all",
      "self.root.geometry(f'+{x}+{y}')" in _body_src(_method("App",
                                                             "_drag_move")))
hiders = {node.name for node in ast.walk(MODULE)
          if isinstance(node, ast.FunctionDef)
          and ("self.root.withdraw()" in _body_src(node)
               or "self.root.deiconify()" in _body_src(node))}
check("ONE METHOD decides whether the app window is on screen, and it is the "
      "dock's placer -- so 'collapsed' cannot mean two things",
      hiders == {"_dock_place"}, repr(sorted(hiders)))
check("...and it hides on collapsed and shows otherwise, in that one branch",
      "self.root.withdraw()" in place_src
      and "self.root.deiconify()" in place_src
      and place_src.index("withdraw") < place_src.index("deiconify"),
      place_src)
check("...and the desktop's show goes through show_at_dock, the same call the "
      "tray's Open uses",
      "desktop.show_at_dock()" in place_src, place_src)
check("_render_dock places in the same call it packs in, so there is no "
      "ordering in which the two disagree",
      "self._dock_place()" in render_src, render_src)
check("...and the placement path restates it too, so crossing between the "
      "desktop and a floating window cannot land showing while the rail says "
      "collapsed",
      "self._dock_place()" in _body_src(_method("App",
                                                "_apply_window_placement")))
callers = {node.name for node in ast.walk(MODULE)
           if isinstance(node, ast.FunctionDef)
           and "self._dock_place()" in _body_src(node)}
check("and those two are its only callers",
      callers == {"_render_dock", "_apply_window_placement"},
      repr(sorted(callers)))
check("the TRAY's Open goes through the dock rather than around it: a bare "
      "deiconify there would put up a window the rail still said was away",
      "self._dock_show(self._dock_active)"
      in _body_src(_method("App", "_from_tray")),
      _body_src(_method("App", "_from_tray")))
check("__init__ is the only writer of the window's minimum now: nothing lowers "
      "it at runtime because no state needs it lowered",
      {node.name for node in ast.walk(MODULE)
       if isinstance(node, ast.FunctionDef)
       and "self.root.minsize(" in _body_src(node)} == {"__init__"})

print("\n---- what the move deleted, and what it kept ----")
# HONEST BOOKKEEPING, asserted rather than described. These four existed only
# because the rail was inside the window and a collapse therefore had to be a
# resize of it. Asked of the CODE and not of the file: openspan.py keeps a
# paragraph naming them and why they went, and deleting that record to make a
# test pass is how a removal comes back as a "missing feature".
_defined = {node.name for node in ast.walk(MODULE)
            if isinstance(node, ast.FunctionDef)}
_attrs = {node.attr for node in ast.walk(MODULE)
          if isinstance(node, ast.Attribute)}
for _gone in ("_dock_floor", "_dock_claim_width", "_window_width"):
    check(f"{_gone} is gone: it answered a question a collapse no longer asks",
          _gone not in _defined)
check("_dock_expanded_w is gone with them -- a remembered width would be a "
      "second, quieter idea of how wide the window should be",
      "_dock_expanded_w" not in _attrs)
check("DOCK_COLLAPSED_MIN_W is gone by name: there is no collapsed WIDTH to "
      "floor, and the constant that replaced it says what it floors",
      not hasattr(A, "DOCK_COLLAPSED_MIN_W")
      and A.DOCK_STRIP_MIN_W > 0 and A.DOCK_STRIP_MIN_H > 0)
check("_rail_width SURVIVED and changed meaning: it was the collapsed window's "
      "floor, and it is the strip's width",
      "_rail_width" in _defined
      and "self._rail_width()" in _body_src(_method("App", "_strip_geometry")))
check("...and it is floored at the constant, never at 0, for a rail that "
      "cannot be measured yet",
      "DOCK_STRIP_MIN_W" in _body_src(_method("App", "_rail_width"))
      and "DOCK_STRIP_MIN_H" in _body_src(_method("App", "_rail_height")))
# Driven, not read: an unmeasurable rail is the state at startup and after a
# teardown, and the strip must still be placeable then.
blind_app = new_app()
blind_app._dock_rail = None
check("a rail that cannot be measured at all falls back to the constants, "
      "never to 0 -- a 0px strip is a dock nobody can click",
      blind_app._rail_width() == A.DOCK_STRIP_MIN_W
      and blind_app._rail_height() == A.DOCK_STRIP_MIN_H,
      repr((blind_app._rail_width(), blind_app._rail_height())))
check("...and the band it reserves for itself is still positive, so the app "
      "window is never placed over a dock that could not be measured",
      blind_app._strip_reserve() > A.DOCK_STRIP_MIN_W)
measured = new_app()
check("a measurable rail reports its own size, and the reserve is that plus "
      "on_desktop's inset",
      measured._rail_width() == RAIL_W and measured._rail_height() == RAIL_H
      and measured._strip_reserve() == RAIL_W + OD.INSET,
      repr(measured._strip_reserve()))


print("\n---- the two floors on_desktop keeps, and who needs them now ----")
check("the ordinary floor is unchanged: a window with panes in it still may "
      "not go below 560", OD.MIN_WIDTH == 560)
check("...and the absolute floor beneath it is smaller, positive, and narrower "
      "than the rail, so it constrains nothing the STRIP wants -- it was built "
      "for a collapsed dock and the strip is that window now",
      0 < OD.COLLAPSED_MIN_WIDTH < RAIL_W < OD.MIN_WIDTH,
      f"{OD.COLLAPSED_MIN_WIDTH} < {RAIL_W} < {OD.MIN_WIDTH}")
check("a controller starts at the ordinary floor, so a caller that says "
      "nothing gets the old behaviour exactly",
      OD.OnDesktop(lambda: 0, lambda: (0, 0, 0, 0),
                   bindings=object()).min_width == OD.MIN_WIDTH)
check("...and the app's controller is one of those callers again: the window "
      "it places always has a pane in it",
      "min_width=PAGE_MIN_WINDOW_W"
      in _body_src(_method("App", "_desktop_geometry")))

print("\n---- the gap the app-icons dock is left ----")
# THE OTHER DOCK ON THIS EDGE is the shell's, and it is not this process's to
# place. The contract between them is a band: a strip may never be placed so
# tall that less than STRIP_GAP is left underneath it. Asserted against the
# rect function the strip actually uses, at the rail's real size.
WORK = (0, 0, 1920, 1040)
strip_rect = OD.dock_rect(WORK, RAIL_W, min_width=A.DOCK_STRIP_MIN_W,
                          height=RAIL_H)
check("the strip is as tall as the rail and no taller",
      strip_rect[3] == RAIL_H, repr(strip_rect))
check("...and it leaves the whole rest of the edge to the app-icons dock",
      WORK[3] - OD.INSET - (strip_rect[1] + strip_rect[3])
      >= OD.STRIP_GAP, f"{strip_rect} on {WORK}")
check("the gap is a named number and not a fraction of something",
      OD.STRIP_GAP > 0 and isinstance(OD.STRIP_GAP, int))
runaway = OD.dock_rect(WORK, RAIL_W, min_width=A.DOCK_STRIP_MIN_W,
                       height=99999)
check("A RAIL THAT GREW COULD NOT EAT THE LOWER DOCK: an absurd content height "
      "is clamped to leave exactly the gap, rather than covering the edge",
      runaway[1] + runaway[3] + OD.STRIP_GAP == WORK[3] - OD.INSET,
      repr(runaway))


print("\n---- the rail says which surface is showing ----")
app = new_app()
app._render_dock()
marks = {key: app._dock_entries[key][0].bg for key in A.DOCK_KEYS}
buttons = {key: app._dock_entries[key][1].bg for key in A.DOCK_KEYS}
check("exactly one entry wears the accent mark",
      [key for key, bg in marks.items() if bg == A.ACCENT] == ["dashboard"],
      repr(marks))
check("the showing entry is lit and the others are not",
      buttons["dashboard"] == A.CARD
      and buttons["console"] == A.PANEL and buttons["scripts"] == A.PANEL,
      repr(buttons))
app._paint_dock_entry("console", hover=True)
check("hovering an idle entry lights it without claiming it is showing",
      app._dock_entries["console"][1].bg == A.DOCK_HOVER
      and app._dock_entries["console"][0].bg == A.PANEL)
app._paint_dock_entry("dashboard", hover=True)
check("hovering the SHOWING entry cannot dim it",
      app._dock_entries["dashboard"][1].bg == A.CARD
      and app._dock_entries["dashboard"][0].bg == A.ACCENT)
app._dock_click("dashboard")
check("collapsed, no entry is marked -- the rail does not claim a surface is "
      "showing when none is",
      not [key for key in A.DOCK_KEYS
           if app._dock_entries[key][0].bg == A.ACCENT],
      repr({k: app._dock_entries[k][0].bg for k in A.DOCK_KEYS}))
check("painting an unknown entry is harmless",
      app._paint_dock_entry("nope") is None)


print("\n---- what was showing is what comes back ----")
saved.clear()
app = new_app()
app._dock_click("scripts")
check("both keys are written on every change",
      saved == [("dock_surface", "scripts"), ("dock_collapsed", False)],
      repr(saved))
saved.clear()
app._dock_click("scripts")
check("collapsing writes them too, so a restart is not an expansion",
      saved == [("dock_surface", "scripts"), ("dock_collapsed", True)],
      repr(saved))

stored = {"dock_surface": "console", "dock_collapsed": True}
A.load_setting = lambda key, default=None: stored.get(key, default)
saved.clear()
mounted.clear()
app = new_app()
app._restore_dock()
check("a session that ended collapsed on the Console comes back that way",
      app._dock_active == "console" and app._dock_collapsed is True
      and packed(app) == set(), repr(packed(app)))
check("restoring writes nothing", saved == [], repr(saved))
check("a collapsed console is not built until it is asked for",
      mounted == [])
app._dock_click("console")
check("...and asking for it builds it then", packed(app) == {"console"}
      and mounted == ["console"])

stored = {"dock_surface": "a surface that no longer exists"}
saved.clear()
app = new_app(active="scripts")
app._restore_dock()
check("an unknown surface falls back to the first entry rather than to a "
      "blank window",
      app._dock_active == "dashboard" and packed(app) == {"dashboard"},
      repr(packed(app)))
stored = {}
app = new_app(active="scripts", collapsed=True)
app._restore_dock()
check("with nothing stored, the app opens on the Dashboard, uncollapsed",
      app._dock_active == "dashboard" and app._dock_collapsed is False
      and packed(app) == {"dashboard"})


# =========================================================================
# THE SCRIPTS SURFACE, AGAINST A HALF-REAL ENGINE. The binding set, the
# problems, the files and the resolver are the SHIPPED ones -- the fake owns
# only the three things a test must not do for real: touch the config store,
# read the desk, and install a hook. So every state below is the state the
# surface will actually be in, not a description of one.
# =========================================================================

class FakeText:
    """A tk.Text, for BOTH surfaces that own one -- Scripts and the Console.

    One fake because the two are the same widget doing the same job: rebuilt
    from a buffer that lives somewhere else, appended to, trimmed, and asked
    where its view is before anything is written into it.
    """

    def __init__(self, tail=1.0):
        self.rows = []
        self.state = "disabled"
        self.tail = tail
        self.saw_end = 0
        self.order = []       # every call that matters, in the order it came

    def config(self, **kwargs):
        self.state = kwargs.get("state", self.state)

    def insert(self, _where, text, _tag=None):
        self.order.append("insert")
        if not self.rows or self.rows[-1].endswith("\n"):
            self.rows.append(text)
        else:
            self.rows[-1] += text

    def delete(self, start, end=None):
        self.order.append("delete")
        if end is None or end == "end":
            self.rows = []
            return
        first = int(str(start).split(".")[0])
        last = int(str(end).split(".")[0])
        del self.rows[first - 1:last - 1]

    def index(self, _spec):
        return f"{max(1, len(self.rows))}.0"

    def yview(self):
        self.order.append("yview")
        return (0.0, self.tail)

    def see(self, _where):
        self.order.append("see")
        self.saw_end += 1


class FakeVar:
    def __init__(self, value=""):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class FakeButton:
    def __init__(self):
        self.text = None
        self.states = []

    def config(self, **kwargs):
        self.text = kwargs.get("text", self.text)

    def state(self, spec):
        self.states.append(tuple(spec))


class FakeSettings:
    """The engine's settings service, as far as the surface is concerned."""

    def __init__(self, enabled=False):
        self.enabled = bool(enabled)
        self.asked = []
        self.writes = []

    def is_enabled(self, feature_id):
        self.asked.append(feature_id)
        return self.enabled

    def set_enabled(self, feature_id, enabled):
        self.asked.append(feature_id)
        self.enabled = bool(enabled)
        self.writes.append(bool(enabled))


class FakeEngine:
    """A ScriptEngine with a real binding set and no hook, store or desk."""

    def __init__(self, directory, files=(), bindings=(), problems=(),
                 enabled=False, running=False):
        self.directory = directory
        self.settings_service = FakeSettings(enabled)
        self.is_running = bool(running)
        self._files = tuple(files)
        self._problems = tuple(problems)
        self.bindings = SE.BindingSet(bindings, problems)
        self.reloads = self.starts = self.stops = 0
        self.explained = []

    def scripts(self):
        return self._files

    def problems(self):
        return self._problems

    def explain(self, chord, facts, screen):
        self.explained.append(chord)
        return self.bindings.resolve(chord, facts, screen)

    def reload(self):
        self.reloads += 1
        return SE.ReloadResult(str(self.directory), self._files,
                               len(self.bindings.bindings), self._problems)

    def start(self):
        self.starts += 1
        if self.is_running:
            return SE.EngineResult("start", False, "already running")
        self.is_running = True
        return SE.EngineResult("start", True,
                               "joined the running keyboard hook")

    def stop(self):
        self.stops += 1
        self.is_running = False
        return SE.EngineResult("stop", True, "stopped")


FACTS = SE.WindowFacts("notepad", "Untitled - Notepad", "Notepad")
SCREEN = SE.ScreenFacts("9f2c4a1b7e0d3355", True)
GOOD_TEXT = ("scope os\n"
             "  Ctrl+Alt+Shift+C -> window center\n"
             "scope window process=notepad\n"
             "  Ctrl+Alt+Shift+C -> pass\n")
BROKEN_TEXT = ("scope os\n"
               "  Ctrl+Alt+Y -> fly away\n")
GOOD = SE.parse_script(GOOD_TEXT, "10-good.eos")
BROKEN = SE.parse_script(BROKEN_TEXT, "20-broken.eos", start_order=2)
GOOD_FILE = SE.ScriptFile("10-good.eos", "C:\\scripts\\10-good.eos", True,
                          len(GOOD.bindings), GOOD.problems)
BROKEN_FILE = SE.ScriptFile("20-broken.eos", "C:\\scripts\\20-broken.eos",
                            True, len(BROKEN.bindings), BROKEN.problems)
WHERE = "C:\\EsotericOS\\data\\scripts"


def scripts_app(engine, text=None):
    """An App with the Scripts surface's four slots and nothing else."""
    app = A.App.__new__(A.App)
    app._script_engine = engine
    app._scripts_text = text
    app.scripts_state = FakeVar()
    app.scripts_where = FakeVar()
    app.scripts_btn = FakeButton()
    app._scripts_facts = lambda: (FACTS, SCREEN)
    return app


def body_text(body):
    return "\n".join(line for _tag, line in body)


print("\n---- the Scripts surface in every state it has ----")
state, where, body = scripts_app(None)._scripts_report(None)
check("with no engine at all the surface says so and shows no list",
      state == "unavailable on this build" and where == ""
      and len(body) == 1 and body[0][0] == "err",
      repr((state, where, body)))

off = FakeEngine(WHERE, files=(GOOD_FILE,), bindings=GOOD.bindings)
state, where, body = scripts_app(off)._scripts_report(off)
check("SWITCHED OFF: the surface says the feature is off rather than showing "
      "a list of bindings that cannot fire",
      "switched off" in state
      and [line for _tag, line in body] == list(A.SCRIPTS_DISABLED_STATE),
      repr((state, body_text(body))))
check("...and no file name leaks into that state",
      "10-good.eos" not in body_text(body), body_text(body))
check("...but the location is still shown, so he can write while it is off",
      WHERE in where, repr(where))
check("the flag is read from the feature the engine declares, by id",
      off.settings_service.asked and set(off.settings_service.asked)
      == {SE.FEATURE_ID}, repr(off.settings_service.asked))

empty = FakeEngine(WHERE, enabled=True)
state, where, body = scripts_app(empty)._scripts_report(empty)
check("EMPTY: with nothing on disk the surface prints the empty state whole",
      [line for _tag, line in body] == list(A.SCRIPTS_EMPTY_STATE),
      body_text(body))
check("...and the empty state is shown UNDER the location, so 'the scripts "
      "directory named above' names something",
      WHERE in where and "scripts directory" in body_text(body).lower(),
      repr(where))
check("...and it does not claim to be intercepting anything yet",
      "not intercepting" in state, repr(state))

loaded = FakeEngine(WHERE, files=(GOOD_FILE, BROKEN_FILE),
                    bindings=GOOD.bindings + BROKEN.bindings,
                    problems=GOOD.problems + BROKEN.problems,
                    enabled=True, running=True)
state, where, body = scripts_app(loaded)._scripts_report(loaded)
rendered = body_text(body)
check("LOADED: the state line says the chords are actually intercepted",
      state == "ON — scripts are intercepted", repr(state))
check("every file is listed with its binding count",
      "10-good.eos: 2 binding(s)" in rendered
      and "20-broken.eos: 0 binding(s)" in rendered, rendered)
check("...and with the scopes it declares, in declaration order",
      "scope os" in rendered
      and "scope window process=notepad" in rendered
      and rendered.index("scope os") < rendered.index(
          "scope window process=notepad"), rendered)
flaw = BROKEN.problems[0]
check("the broken script really is located to a line and a column, which is "
      "what makes 'first-class' worth anything",
      flaw.source == "20-broken.eos" and flaw.line == 2 and flaw.column > 0
      and "fly" in flaw.message, flaw.describe())
check("PROBLEMS ARE FIRST-CLASS CONTENT: file, line, column and reason, "
      "verbatim from the engine",
      all(problem.describe() in rendered for problem in loaded.problems())
      and flaw.describe() in rendered, rendered)
check("...and they are named as errors, not as prose",
      [tag for tag, line in body
       if any(problem.describe() in line
              for problem in loaded.problems())] == ["err"], repr(body[:4]))
check("...and they come BEFORE the file list, so a broken script cannot be "
      "scrolled past",
      rendered.index(flaw.describe()) < rendered.index("10-good.eos: 2"),
      rendered)
check("...and the count is stated, with what a problem actually costs",
      "1 problem(s)" in body[0][1] and "ONE binding" in body[0][1],
      repr(body[0]))
check("the resolution for each claimed chord is shown, resolved against the "
      "window that has the focus",
      "notepad" in rendered and "window center" in rendered
      and loaded.explained == list(loaded.bindings.chords),
      repr(loaded.explained))
check("...and the heading says which window and which screen it resolved "
      "against, so 'here' is not a mystery",
      "9f2c4a1b7e0d3355" in rendered and "(primary)" in rendered, rendered)
check("the narrower `pass` really did stand aside for the os binding, which "
      "is the resolver's answer and not the surface's",
      any(tag == "ok" and "-> window center" in line for tag, line in body),
      rendered)

blind = scripts_app(loaded)
blind._scripts_facts = lambda: (None, None)
check("a desk that cannot be read costs the resolution and nothing else: the "
      "files and the problems are still there",
      "could not be read" in body_text(blind._scripts_report(loaded)[2])
      and "10-good.eos: 2 binding(s)"
      in body_text(blind._scripts_report(loaded)[2]))


print("\n---- painting it, and the two controls ----")
view = FakeText()
app = scripts_app(loaded, view)
body = app._scripts_refresh()
check("the repaint rebuilds from empty rather than appending",
      view.order[0] == "delete" and view.order.count("delete") == 1,
      repr(view.order))
check("every reported line reaches the widget, in order",
      [row.rstrip("\n") for row in view.rows] == [line for _tag, line in body],
      repr(view.rows[:3]))
check("...and the view is placed LAST, at the top, because the problems are "
      "at the top -- this surface does not follow a tail",
      view.order[-1] == "see" and view.saw_end == 1, repr(view.order[-3:]))
check("the state line and the location are written to their own labels, not "
      "into the scroller",
      "intercepted" in app.scripts_state.get()
      and WHERE in app.scripts_where.get(),
      repr((app.scripts_state.get(), app.scripts_where.get())))
check("a running engine's switch offers to turn it OFF",
      app.scripts_btn.text == "Turn off scripts", repr(app.scripts_btn.text))
stopped = FakeEngine(WHERE, enabled=True)
app = scripts_app(stopped, FakeText())
app._scripts_refresh()
check("a stopped engine's switch offers to turn it ON",
      app.scripts_btn.text == "Turn on scripts", repr(app.scripts_btn.text))
app = scripts_app(None, FakeText())
app._script_host = lambda: None
app._scripts_refresh()
check("with no engine the switch is disabled rather than lying about what it "
      "would do", app.scripts_btn.states == [("disabled",)],
      repr(app.scripts_btn.states))

engine = FakeEngine(WHERE, files=(GOOD_FILE,), bindings=GOOD.bindings,
                    enabled=True)
app = scripts_app(engine, FakeText())
app._scripts_seed = lambda _engine: False
app._scripts_reload()
check("RELOAD calls the engine's reload exactly once and starts nothing",
      (engine.reloads, engine.starts, engine.stops) == (1, 0, 0),
      repr((engine.reloads, engine.starts, engine.stops)))
check("...and repaints afterwards, so the surface shows what was just read",
      "10-good.eos: 2 binding(s)" in "".join(app._scripts_text.rows))
app._scripts_reload()
check("...and reloading twice reloads twice; it is not a one-shot",
      engine.reloads == 2)

engine = FakeEngine(WHERE, files=(GOOD_FILE,), bindings=GOOD.bindings)
app = scripts_app(engine, FakeText())
app._scripts_seed = lambda _engine: False
app._toggle_scripts()
check("THE SWITCH starts the engine and writes the feature flag on",
      engine.starts == 1 and engine.is_running
      and engine.settings_service.writes == [True],
      repr((engine.starts, engine.settings_service.writes)))
check("...and the surface comes back showing the loaded list",
      "10-good.eos: 2 binding(s)" in "".join(app._scripts_text.rows))
app._toggle_scripts()
check("pressing it again stops the engine and writes the flag off",
      engine.stops == 1 and not engine.is_running
      and engine.settings_service.writes == [True, False],
      repr(engine.settings_service.writes))
check("...and it never started twice on the way", engine.starts == 1)

refused = FakeEngine(WHERE, enabled=False)
refused.start = lambda: SE.EngineResult("start", False, "hook refused")
app = scripts_app(refused, FakeText())
app._scripts_seed = lambda _engine: False
app._toggle_scripts()
check("a refused start does NOT write the flag on, so the app does not spend "
      "every launch retrying something that cannot work",
      refused.settings_service.writes == [] and not refused.is_running,
      repr(refused.settings_service.writes))


print("\n---- the scripts directory, created once ----")
seed_root = tempfile.mkdtemp(prefix="esotericos-scripts-")
try:
    target = os.path.join(seed_root, "data", "scripts")
    engine = FakeEngine(target)
    app = scripts_app(engine)
    created = app._scripts_seed(engine)
    example = os.path.join(target, A.SCRIPTS_EXAMPLE_NAME)
    check("first run creates the directory and writes one example into it",
          created is True and os.path.isdir(target)
          and os.path.isfile(example), repr(os.listdir(seed_root)))
    written = open(example, encoding="utf-8").read()
    check("...and what it wrote parses with the shipped parser",
          not SE.parse_script(written, A.SCRIPTS_EXAMPLE_NAME).problems)
    check("...and the engine would find it: it ends in the engine's extension",
          example.endswith(SE.EXTENSION))
    os.remove(example)
    check("a SECOND run writes nothing: an existing directory is his, and a "
          "deleted example stays deleted",
          app._scripts_seed(engine) is False
          and not os.path.exists(example))
    engine.directory = os.path.join(seed_root, "nested", "a", "b", "scripts")
    check("a directory several levels deep is created whole",
          app._scripts_seed(engine) is True
          and os.path.isdir(engine.directory))
    engine.directory = ""
    check("an engine with no directory at all is refused, not crashed into",
          app._scripts_seed(engine) is False)
finally:
    shutil.rmtree(seed_root, ignore_errors=True)


# =========================================================================
# THE CONSOLE'S BUFFER SURVIVED THE MOVE. It was written for a closable
# window and it is unchanged for a hideable surface, because what it was
# always really protecting is that THE LOG IS THE BUFFER, not the widget.
# FakeText is defined with the Scripts fakes above; both surfaces own one
# Text and neither owns the history inside it.
# =========================================================================

print("\n---- the log is the buffer, and it outlived the window ----")
console = A.App.__new__(A.App)
console._console_lines = collections.deque(maxlen=4)
console._console_text = None            # the surface has never been invoked
for n in range(6):
    console.log("event", f"line {n}")
check("output with no view at all still lands in the buffer",
      [row[2] for row in console._console_lines]
      == ["line 2", "line 3", "line 4", "line 5"],
      repr([row[2] for row in console._console_lines]))

console._console_text = FakeText()
console._console_replay()
check("the first invoke replays the whole buffer, from empty",
      console._console_text.order[0] == "delete"
      and [row.split(" ", 1)[1].rstrip("\n")
           for row in console._console_text.rows]
      == ["line 2", "line 3", "line 4", "line 5"],
      repr(console._console_text.rows))
check("and it lands at the tail", console._console_text.saw_end >= 1)

# THE TAIL DECISION IS MADE BEFORE THE INSERT. If it were read afterwards the
# view would always look like it was at the bottom -- because the insert just
# put it there -- and Doug would be yanked out of whatever he was reading.
view = FakeText(tail=1.0)
console._console_text = view
view.order.clear()
console.log("event", "arrived at the bottom")
check("the at-tail decision precedes the insert, every time",
      view.order.index("yview") < view.order.index("insert"),
      repr(view.order))
check("at the bottom, new output follows", view.order[-1] == "see")

view = FakeText(tail=0.42)               # scrolled up, reading
console._console_text = view
console.log("event", "arrived while he was reading")
check("scrolled up, new output does NOT yank the view",
      view.saw_end == 0 and "see" not in view.order, repr(view.order))
check("...but the line is there waiting, in the widget and in the buffer",
      any("arrived while he was reading" in row for row in view.rows)
      and console._console_lines[-1][2] == "arrived while he was reading")

# THE TRIM, driven with a small cap rather than 20000 lines. _console_paint
# reads the module constant at call time, so it is patched and put back.
_real_cap = A.CONSOLE_BUFFER_LINES
try:
    A.CONSOLE_BUFFER_LINES = 3
    view = FakeText()
    console._console_text = view
    console._console_lines = collections.deque(maxlen=3)
    for n in range(8):
        console.log("event", f"trimmed {n}")
    check("the widget is trimmed from the TOP to the same length as the "
          "buffer, so the newest output is what survives",
          len(view.rows) == 3
          and [row.split(" ", 1)[1].rstrip("\n") for row in view.rows]
          == ["trimmed 5", "trimmed 6", "trimmed 7"], repr(view.rows))
    check("and the buffer agrees with it, line for line",
          [row[2] for row in console._console_lines]
          == [row.split(" ", 1)[1].rstrip("\n") for row in view.rows])
finally:
    A.CONSOLE_BUFFER_LINES = _real_cap
check("the shipped cap is untouched and still generous",
      A.CONSOLE_BUFFER_LINES == _real_cap and A.CONSOLE_BUFFER_LINES >= 10000)

console._console_clear()
check("Clear empties BOTH copies", not console._console_lines
      and not console._console_text.rows)
console._console_text = FakeText()
console._console_replay()
check("and a console rebuilt after Clear is empty, not refilled",
      console._console_text.rows == [])

print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
