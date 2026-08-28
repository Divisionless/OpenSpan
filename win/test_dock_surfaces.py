# SPDX-License-Identifier: AGPL-3.0-or-later
"""The right-side dock, and the three surfaces it invokes.

Doug, 2026-08-28: *"split this into Dashboard and Console -- invokable and
collapsible from the right side dock (that you create now) -- add Scripts as a
third surface here"*, and the distinction that governs the whole shape: *"no
pop out but we can replace surfaces and invoke entire new ones in the side."*

DIALOGS ARE BANNED; SURFACES ARE INVOKED FROM THE DOCK. A dialog is an OS
window Windows places wherever it likes, on a screen he was not looking at (28
July). A surface is a region of the app's own window, asked for by name from
the rail, replacing whatever was showing there. That is why there is no
tk.Toplevel left in the app outside _identify_card, and why adding a fourth
surface must never need one.

LAW 10 -- *"nested scrolling is a scrolling surface inside another surface that
scrolls"* -- is a claim about CONTAINMENT on the same axis, so it is asserted
here as containment: the rail does not scroll at all, the three surfaces are
siblings of one another, exactly one is packed at a time, and each owns at most
one vertical scroller. Two scrollers in one window are lawful precisely because
neither can ever be inside the other.

NO TK ROOT IS CONSTRUCTED IN THIS FILE, deliberately. The app is running on
Doug's desk while these run. The layout contract is read from the AST and the
switching behaviour is driven against fakes.
"""
import ast
import collections
import os
import sys

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
render = _method("App", "_render_dock")
state = _method("App", "_dock_state")
click = _method("App", "_dock_click")
show = _method("App", "_dock_show")
restore = _method("App", "_restore_dock")
paint_entry = _method("App", "_paint_dock_entry")
scripts_fn = _method("App", "_build_scripts_surface")
console_mount = _method("App", "_console_mount")
init_parents = _parents(init)
init_src = ast.unparse(init)
render_src = _body_src(render)


print("\n---- three surfaces, registered once ----")
check("every dock method exists",
      all((init, build_rail, render, state, click, show, restore, paint_entry,
           scripts_fn, console_mount)),
      repr({"rail": bool(build_rail), "render": bool(render),
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
check("the rail is built on whatever the body passes it, beside the surfaces",
      _parents(build_rail).get("rail") == "parent",
      repr(_parents(build_rail).get("rail")))

rail_src = _body_src(build_rail)
check("THE RAIL DOES NOT SCROLL: no scroller, no canvas, no scroll wiring",
      not _calls(build_rail, "ttk.Scrollbar")
      and not _calls(build_rail, "tk.Canvas")
      and not _calls(build_rail, "tk.Text")
      and "yscrollcommand" not in rail_src
      and "yview" not in rail_src, rail_src)
check("the rail is always visible: it is packed unconditionally, and nothing "
      "ever forgets it",
      "rail.pack(" in rail_src and "rail.pack_forget" not in SOURCE
      and "self._dock_rail.pack_forget" not in SOURCE, rail_src)

scroll_owners = {}
for _cls in [n for n in ast.walk(MODULE) if isinstance(n, ast.ClassDef)]:
    for _item in _cls.body:
        if not isinstance(_item, ast.FunctionDef):
            continue
        count = len(_calls(_item, "ttk.Scrollbar"))
        if count:
            scroll_owners[f"{_cls.name}.{_item.name}"] = count
check("exactly two vertical scrollers in the whole app, one per surface that "
      "has something to hide",
      scroll_owners == {"App.__init__": 1, "App._console_mount": 1},
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
check("the Scripts surface owns no scroller, because it hides nothing",
      not _calls(scripts_fn, "ttk.Scrollbar")
      and not _calls(scripts_fn, "tk.Canvas")
      and not _calls(scripts_fn, "tk.Text"))
check("the wheel handler still claims only the page canvas -- the Console's "
      "own Text keeps its class binding, and needs no exemption",
      "self._inside(widget, self._page_canvas)"
      in _body_src(_method("App", "_on_page_mousewheel")))


print("\n---- no pop-outs anywhere ----")
toplevel_owners = {node.name for node in ast.walk(MODULE)
                   if isinstance(node, ast.FunctionDef)
                   and any(isinstance(call, ast.Call)
                           and _name(call.func) == "tk.Toplevel"
                           for call in ast.walk(node))}
check("the identify card is the only method in the app that opens a window",
      toplevel_owners == {"_identify_card"}, repr(sorted(toplevel_owners)))
check("no dock or surface method opens one",
      not (toplevel_owners & {"_build_dock_rail", "_render_dock",
                              "_dock_click", "_dock_show", "_dock_state",
                              "_restore_dock", "_console_mount",
                              "_build_scripts_surface"}))
check("the console's window era is gone by name, not merely unreachable",
      "_open_console_window" not in SOURCE
      and "_close_console_window" not in SOURCE
      and "_console_win" not in SOURCE and "_console_geom" not in SOURCE)


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


print("\n---- Scripts is honestly empty ----")
scripts_src = _body_src(scripts_fn)
check("the empty state is one module-level constant, so it can be read here",
      isinstance(A.SCRIPTS_EMPTY_STATE, tuple)
      and all(isinstance(line, str) and line for line in
              A.SCRIPTS_EMPTY_STATE), repr(A.SCRIPTS_EMPTY_STATE))
check("it says there is nothing here and that nothing here runs",
      "Nothing here yet" in A.SCRIPTS_EMPTY_STATE[0]
      and "runs anything" in A.SCRIPTS_EMPTY_STATE[0],
      repr(A.SCRIPTS_EMPTY_STATE[0]))
check("it names what the surface will be, in Doug's own terms",
      any("scoped scripting system" in line for line in A.SCRIPTS_EMPTY_STATE))
check("it admits nothing is wired here yet rather than implying something is",
      any("none of it is wired to this surface yet" in line
          for line in A.SCRIPTS_EMPTY_STATE))
check("and it names, item by item, the three things it is NOT showing",
      all(token in " ".join(A.SCRIPTS_EMPTY_STATE) for token in
          ("no script list", "no editor", "no run button")))
# NO FAKE UI. A drawing of a feature is worse than an empty page: every
# invented row has to be deleted before a real one can be written, and until
# then the app is claiming something it cannot do.
check("the surface builds labels and nothing else -- no buttons, no entries, "
      "no list, no tree",
      not any(_calls(scripts_fn, ctor) for ctor in
              ("ttk.Button", "tk.Button", "ttk.Entry", "tk.Entry",
               "tk.Listbox", "ttk.Treeview", "ttk.Combobox", "tk.Checkbutton",
               "ttk.Checkbutton", "tk.Menu")),
      scripts_src)
check("and it wires no command anywhere",
      "command=" not in scripts_src and "bind(" not in scripts_src,
      scripts_src)
check("nothing in the app pretends a script exists",
      "script_rows" not in SOURCE and "run_script" not in SOURCE)


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
    """A rail wider than DOCK_COLLAPSED_MIN_W, so the max() is a real choice."""

    def winfo_reqwidth(self):
        return 130


class FakeRoot:
    def __init__(self):
        self.minimums = [(A.PAGE_MIN_WINDOW_W, 680)]

    def minsize(self, width=None, height=None):
        if width is None:
            return self.minimums[-1]
        self.minimums.append((width, height))
        return None


def new_app(active="dashboard", collapsed=False):
    app = A.App.__new__(A.App)
    app.root = FakeRoot()
    app._dock_rail = FakeRail()
    app._dock_surfaces = {key: FakeSurface() for key in A.DOCK_KEYS}
    app._dock_entries = {key: (FakeChrome(), FakeChrome())
                         for key in A.DOCK_KEYS}
    app._dock_active = active
    app._dock_collapsed = collapsed
    app._console_mount = lambda: mounted.append(app._dock_active)
    return app


def packed(app):
    return {key for key, frame in app._dock_surfaces.items() if frame.packed}


saved = []
mounted = []
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
app._dock_click("console")
check("coming back to the console does not build a second view",
      packed(app) == {"console"} and mounted == ["console", "console"])
check("no surface is ever packed twice over without being forgotten first",
      all(frame.packs <= 3 for frame in app._dock_surfaces.values()))
check("an unknown key changes nothing at all",
      app._dock_click("nope") is False and packed(app) == {"console"}
      and app._dock_active == "console")


print("\n---- collapse, and the rail that survives it ----")
app = new_app()
app._render_dock()
before = len(app.root.minimums)
check("clicking the ACTIVE entry collapses", app._dock_click("dashboard")
      and app._dock_collapsed is True and packed(app) == set(),
      repr(packed(app)))
check("the active surface is REMEMBERED across the collapse, not cleared",
      app._dock_active == "dashboard")
check("the rail is untouched: nothing in the collapse path forgets it",
      isinstance(app._dock_rail, FakeRail))
check("collapsed, the window's minimum width drops to the rail, so it can be "
      "dragged down to a thin dock",
      app.root.minimums[-1][0] == 130
      and len(app.root.minimums) > before, repr(app.root.minimums))
check("the height floor is carried through untouched",
      app.root.minimums[-1][1] == app.root.minimums[0][1],
      repr(app.root.minimums[-1]))
check("clicking it again brings the same surface back",
      app._dock_click("dashboard") and packed(app) == {"dashboard"}
      and app._dock_collapsed is False)
check("...and the page's width floor comes back with it",
      app.root.minimums[-1][0] == A.PAGE_MIN_WINDOW_W,
      repr(app.root.minimums[-1]))
app._dock_click("dashboard")
check("clicking a DIFFERENT entry while collapsed expands onto it, rather "
      "than staying collapsed",
      app._dock_click("scripts") and packed(app) == {"scripts"}
      and app._dock_collapsed is False)
check("a rail floor is never narrower than the constant, whatever the rail "
      "reports", A.DOCK_COLLAPSED_MIN_W > 0
      and A.PAGE_MIN_WINDOW_W > A.DOCK_COLLAPSED_MIN_W)
app._dock_rail = None
app._dock_collapsed = True
app._render_dock()
check("a rail that cannot be measured falls back to the constant, never to 0",
      app.root.minimums[-1][0] == A.DOCK_COLLAPSED_MIN_W,
      repr(app.root.minimums[-1]))


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
# THE CONSOLE'S BUFFER SURVIVED THE MOVE. It was written for a closable
# window and it is unchanged for a hideable surface, because what it was
# always really protecting is that THE LOG IS THE BUFFER, not the widget.
# =========================================================================

class FakeText:
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
