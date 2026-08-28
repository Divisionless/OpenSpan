# SPDX-License-Identifier: AGPL-3.0-or-later
"""The control GUI is one ordered, vertically scrolling document.

FOUR sections, not five. The console left the page on 2026-08-27 and became
its own window. Law 10, as Doug defined it that morning: *"nested scrolling is
a scrolling surface inside another surface that scrolls."* The harm is scroll
hijack -- his gesture is on the page, the pointer drifts over a smaller
scrollable region, and the scroll is taken away from him mid-gesture. The test
is same-axis containment, so a surface that scrolls AS ITS OWN WINDOW is
lawful: nothing contains it, so there is no gesture for it to take. The console
was the one surface with an unbounded producer, so it was the one that had to
move -- and having moved, it may scroll, follow its own tail, and keep a
generous history.

The test never constructs App: doing that starts workers and can touch the VM.
It reads the assembly contract from the AST, then drives the shipped viewport
and console methods against deterministic fakes and withdrawn Tk controls. No
Toplevel is ever built here; the console window's contract is asserted from
source and its behaviour from its own methods against a fake Text.
"""
import ast
import collections
import os
import sys
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace

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


def _layout(function):
    parent, packs = {}, {}
    for node in ast.walk(function):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.value, ast.Call) and node.value.args):
            child, master = _name(node.targets[0]), _name(node.value.args[0])
            if child and master:
                parent[child] = master
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "pack"):
            continue
        child = _name(node.func.value)
        if child:
            packs.setdefault(child, []).append(node)
    return parent, packs


init = _method("App", "__init__")
show = _method("App", "show_section")
wheel = _method("App", "_on_page_mousewheel")
viewport = _method("App", "_on_page_viewport_configure")
content = _method("App", "_on_page_content_configure")
open_console = _method("App", "_open_console_window")
check("App.__init__ exists", init is not None)
check("all viewport methods exist",
      all((show, wheel, viewport, content, open_console)))
parent, packs = _layout(init)
init_src = ast.unparse(init)


print("\n---- one document, not selectable panes ----")
# FOUR sections since 2026-08-27. Console was the fifth and is now its own
# window: law 10 forbids a scroller inside a scroller, and the console is the
# one surface whose producer is unbounded, so it was the one that had to leave.
expected = (("desk", "Desk"), ("devices", "Devices"),
            ("bluetooth", "Bluetooth"), ("system", "System"))
check("PAGE_SPEC is the complete reading order", A.PAGE_SPEC == expected,
      repr(A.PAGE_SPEC))
check("the old pane API is absent", not hasattr(A.App, "select_pane")
      and not hasattr(A, "PANE_SPEC") and "_bridge_spacer" not in SOURCE)
check("there is one page Canvas and one vertical Scrollbar",
      "page_canvas = tk.Canvas(main" in SOURCE
      and "page_scroll = ttk.Scrollbar(main, orient=\"vertical\"," in SOURCE)
check("the scrollbar and Canvas are wired in both directions",
      "yscrollcommand=page_scroll.set" in init_src
      and "page_scroll.config(command=page_canvas.yview)" in init_src)
check("the document is one embedded Canvas window",
      "self._page_window = page_canvas.create_window" in SOURCE
      and "window=bridge" in SOURCE and 'anchor="nw"' in SOURCE)
check("content changes update scrollregion",
      "self._sync_page_scrollregion()" in ast.unparse(content))
check("viewport changes force the document to one-column width",
      "itemconfigure" in ast.unparse(viewport)
      and "self._page_window" in ast.unparse(viewport))
check("mouse wheel and legacy wheel buttons use the same page handler",
      all(token in SOURCE for token in
          ('bind_all("<MouseWheel>"', 'bind_all("<Button-4>"',
           'bind_all("<Button-5>"')))

scrollbars = [node for node in ast.walk(MODULE)
              if isinstance(node, ast.Call)
              and _name(node.func) == "ttk.Scrollbar"]
# LAW 10 IS PER WINDOW, NOT PER MODULE. It forbids a scrolling surface INSIDE
# another scrolling surface, because the wheel is hijacked mid-gesture; a
# surface that scrolls as its own window is contained by nothing and hijacks
# nothing. So the number to assert is one scrollbar per window, and which
# method each one is built in is the whole of the claim: the page's, in
# App.__init__, and the console window's, in _open_console_window. This once
# asserted 3 (page, device tree, in-page console), then 1, and is now 2 --
# every change of that number is a law-10 decision, not a tidy-up.
scroll_owners = {}
for _cls in [node for node in ast.walk(MODULE)
             if isinstance(node, ast.ClassDef)]:
    for _item in _cls.body:
        if not isinstance(_item, ast.FunctionDef):
            continue
        _count = len([n for n in ast.walk(_item) if isinstance(n, ast.Call)
                      and _name(n.func) == "ttk.Scrollbar"])
        if _count:
            scroll_owners[f"{_cls.name}.{_item.name}"] = _count
check("law 10: exactly two scrollbars, one per window",
      len(scrollbars) == 2, f"{len(scrollbars)} Scrollbar calls")
check("the page owns exactly one, built in App.__init__",
      scroll_owners.get("App.__init__") == 1, repr(scroll_owners))
check("the console window owns exactly one, built in _open_console_window",
      scroll_owners.get("App._open_console_window") == 1, repr(scroll_owners))
check("no third owner: nothing else in the module builds a scroller",
      set(scroll_owners) == {"App.__init__", "App._open_console_window"},
      repr(scroll_owners))
check("both scrollbars use the dark named style",
      all(any(keyword.arg == "style"
              and isinstance(keyword.value, ast.Name)
              and keyword.value.id == "SCROLLBAR_STYLE"
              for keyword in node.keywords)
          for node in scrollbars))


print("\n---- law 10: one scroller, every container adapts ----")


def _body_src(function):
    """Unparsed source of a function WITHOUT its docstring.

    Prose must never be able to pass or fail a contract check: a comment
    naming the exemption reads exactly like the exemption to a substring test.
    """
    clone = ast.parse(ast.unparse(function)).body[0]
    if (clone.body and isinstance(clone.body[0], ast.Expr)
            and isinstance(clone.body[0].value, ast.Constant)
            and isinstance(clone.body[0].value.value, str)):
        clone.body = clone.body[1:] or [ast.Pass()]
    return ast.unparse(clone)


def _mutators(receiver, verbs=("insert", "delete")):
    """Every method that adds or removes content from receiver.

    Source-level on purpose: naming the mutation points is the whole contract,
    and a container that grows to fit cannot be tested without a real window.
    """
    found = []
    for node in ast.walk(MODULE):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if not isinstance(item, ast.FunctionDef):
                continue
            body = _body_src(item)
            if any(f"{receiver}.{verb}(" in body for verb in verbs):
                found.append((node.name + "." + item.name, body))
    return found


wheel_src = _body_src(wheel)
check("the wheel handler carries no widget-type exemption",
      "isinstance" not in wheel_src
      and not any(token in wheel_src for token in
                  ("tk.Text", "tk.Listbox", "ttk.Treeview")),
      wheel_src)
check("the wheel handler is still scoped to the page canvas",
      "self._inside(widget, self._page_canvas)" in wheel_src)
check("adapting helpers are declared once, at module level",
      all(callable(getattr(A, name, None)) for name in
          ("fit_text_height", "fit_tree_height", "trim_text_to_lines",
           "bind_fit_text_height")))

bt_init = _body_src(_method("BtPanel", "__init__"))
tree_mutators = _mutators("self.tree")
check("the device tree has mutation points to check", bool(tree_mutators))
for where, body in tree_mutators:
    check(f"{where} restates the tree height after changing rows",
          "fit_tree_height(self.tree)" in body, where)
check("the device tree declares no fixed row count",
      "height=8" not in bt_init and "height=TREE_MIN_ROWS" in bt_init)

# self.out (the Bluetooth panel's log) is the ONE Text left on the page, so it
# is still the one that must fit its content and must never chase its own tail.
# self.console is gone from this list because it is gone from the page.
for receiver in ("self.out",):
    mutators = _mutators(receiver)
    check(f"{receiver} has mutation points to check", bool(mutators))
    for where, body in mutators:
        check(f"{where} refits {receiver} to its content",
              f"fit_text_height({receiver}" in body, where)
    check(f"{receiver} never scrolls itself to the tail",
          f"{receiver}.see(" not in SOURCE)
check("the one page Text declares no fixed line count",
      "height=5" not in bt_init and "height=TEXT_MIN_LINES" in bt_init)
check("the page document builds no console Text at all",
      "self.console" not in SOURCE and "tk.Text(" not in init_src)

print("\n---- the console is its own surface, and may scroll ----")
# THE CAP CHANGED MEANING. It used to bound a fitted container's CONTENT so an
# unbounded log could not grow the page. In its own window nothing about height
# depends on length, so only memory is left and the number is generous -- and
# it now bounds the App's line BUFFER, which is the log and outlives the window.
check("the console cap is still one module-level number",
      isinstance(A.CONSOLE_BUFFER_LINES, int)
      and A.CONSOLE_BUFFER_LINES > 0, repr(A.CONSOLE_BUFFER_LINES))
check("the old layout-era name is gone, so nothing reads the old meaning",
      not hasattr(A, "CONSOLE_RETAINED_LINES")
      and "CONSOLE_RETAINED_LINES" not in SOURCE)
check("a surface with its own scrollbar can afford real history",
      A.CONSOLE_BUFFER_LINES >= 10000, repr(A.CONSOLE_BUFFER_LINES))

# NOTE on quoting below: _body_src is ast.unparse output, and ast.unparse
# renders every string literal single-quoted whatever the file says. Tokens
# matched against a *_src therefore use 'single' quotes; tokens matched against
# SOURCE, which is the file's own text, use "double".
log_src = _body_src(_method("App", "log"))
check("every line lands in the buffer, unconditionally",
      "self._console_lines.append(line)" in log_src, log_src)
check("the buffer is bounded by the one constant, and by nothing else",
      "collections.deque(maxlen=CONSOLE_BUFFER_LINES)" in SOURCE)
check("the timestamp is taken when the line happens, not when it is painted",
      "time.strftime('%H:%M:%S ')" in log_src, log_src)
paint_src = _body_src(_method("App", "_console_paint"))
check("painting an open window is the SECOND half, and is skipped when there "
      "is no window", "self._console_text" in paint_src
      and "if widget is None" in paint_src, paint_src)
check("the widget is kept the same length as the buffer that feeds it",
      "trim_text_to_lines(widget, CONSOLE_BUFFER_LINES)" in paint_src)
check("trimming still deletes from the TOP, so the newest output survives",
      'widget.delete("1.0", f"{lines - retained + 1}.0")' in SOURCE)

console_src = _body_src(open_console) if open_console else ""
replay_src = _body_src(_method("App", "_console_replay"))
check("a reopened console is rebuilt from the buffer, from empty",
      "widget.delete('1.0', 'end')" in replay_src
      and "self._console_paint(tuple(self._console_lines))" in replay_src,
      replay_src)
check("opening the window replays the buffer into it",
      "self._console_replay()" in console_src, console_src)
clear_src = _body_src(_method("App", "_console_clear"))
check("Clear clears BOTH copies, or the console refills itself on reopen",
      "self._console_lines.clear()" in clear_src
      and "widget.delete('1.0', 'end')" in clear_src, clear_src)

# TAIL-FOLLOWING, which is lawful here and was not lawful on the page.
tail_src = _body_src(_method("App", "_console_at_tail"))
check("at-the-tail is decided from yview(), not from a flag we set",
      "yview()" in tail_src and "self._console_text" in tail_src, tail_src)
_decided = paint_src.find("at_tail = self._console_at_tail()")
_inserted = paint_src.find("widget.insert(")
check("the decision is made BEFORE the insert, never after",
      0 <= _decided < _inserted, f"decided at {_decided}, inserted at "
                                 f"{_inserted}")
check("new output follows the tail ONLY when the view is already at it",
      "if at_tail:" in paint_src and "widget.see('end')" in paint_src,
      paint_src)
check("nothing on the PAGE chases its own tail",
      not any(token in SOURCE for token in
              ('self.out.see("end")', 'c.see("end")')))
# Every see("end") in the module, by the method it lives in. Chasing the tail
# is exactly the yank law 10 is about, so the set of methods allowed to do it
# is the assertion, and both are the console window's. (BtPanel.rename's
# tree.see(mac) is not tail-chasing -- it realises one row before measuring it
# -- so the pattern matched here is the literal "end", not see() at large.)
seers = {node.name for node in ast.walk(MODULE)
         if isinstance(node, ast.FunctionDef)
         and any(isinstance(call, ast.Call)
                 and isinstance(call.func, ast.Attribute)
                 and call.func.attr == "see"
                 and len(call.args) == 1
                 and isinstance(call.args[0], ast.Constant)
                 and call.args[0].value == "end"
                 for call in ast.walk(node))}
check("the only tail-chasing see('end') calls belong to the console window",
      seers == {"_console_paint", "_console_replay"}, repr(sorted(seers)))


section_locals = ("pane_desk", "pane_devices", "pane_bluetooth",
                  "pane_system")
check("all four section frames are direct children of the document",
      all(parent.get(local) == "bridge" for local in section_locals),
      repr({local: parent.get(local) for local in section_locals}))
check("there is no fifth section frame left behind",
      "pane_console" not in SOURCE and len(section_locals) == len(A.PAGE_KEYS))
check("all sections are registered under PAGE keys",
      "self._page_sections =" in init_src
      and all(f'"{key}": pane_{key}' in SOURCE for key in A.PAGE_KEYS))
check("one loop maps every section in PAGE order",
      "for _key in PAGE_KEYS:" in SOURCE
      and 'self._page_sections[_key].pack(fill="x")' in SOURCE)
check("App never hides a page section",
      "pack_forget" not in init_src and "place_forget" not in init_src)
check("Bluetooth and Desk service widgets are still constructed once",
      parent.get("self.bt_panel") == "pane_bluetooth"
      and parent.get("self.canvas") == "arr_wrap"
      and parent.get("arr_wrap") == "pane_desk")


print("\n---- fixed safety header, scrolling footer ----")
check("readiness remains above the page Canvas in fixed chrome",
      parent.get("self.ready_lbl") == "full"
      and parent.get("page_canvas") == "main")
check("the build footer is the document's last row",
      parent.get("foot") == "bridge")
check("the footer states the canonical copyleft license",
      "AGPL-3.0-or-later" in init_src and "open source · MIT" not in init_src)
# CONSOLE IS NO LONGER A PAGE ANCHOR. The control in the header used to scroll
# the page to a Console section; it opens the console WINDOW now, and the
# window is closable -- deliberately unlike the main window, which refuses
# WM_CLOSE on the surface.
check("the Console control opens a window, not a section",
      "command=self._open_console_window" in init_src
      and "show_section" not in console_src
      and "_toggle_console" not in SOURCE)
check("the Console control no longer promises a place further down the page",
      '"↓  Console"' not in SOURCE and "self._cons_btn" in init_src)
check("the console window is closable, and closing it is its own handler",
      "protocol('WM_DELETE_WINDOW', self._close_console_window)"
      in console_src, console_src)
check("a second open reuses or rebuilds -- it never makes a second window",
      "if win is not None" in console_src
      and "self._console_win = win" in console_src
      and console_src.count("tk.Toplevel(") == 1, console_src)
close_src = _body_src(_method("App", "_close_console_window"))
check("closing drops the references it destroys, so none can go stale",
      "self._console_win = self._console_text = None" in close_src
      and "win.destroy()" in close_src, close_src)
check("closing the console never touches the app's own window",
      "self.root" not in close_src, close_src)


class FakeCanvas:
    def __init__(self):
        self.master = None
        self.region = None
        self.width = None
        self.scrolls = []
        self.fraction = None

    def bbox(self, _tag):
        return (0, 0, 600, 1500)

    def configure(self, **kwargs):
        self.region = kwargs.get("scrollregion", self.region)

    def itemconfigure(self, _item, **kwargs):
        self.width = kwargs.get("width")

    def yview_scroll(self, units, what):
        self.scrolls.append((units, what))

    def yview_moveto(self, fraction):
        self.fraction = fraction

    def winfo_height(self):
        return 500


class FakeBody:
    def winfo_reqheight(self):
        return 1500


class FakeSection:
    def __init__(self, y):
        self._y = y

    def winfo_y(self):
        return self._y


class FakeRoot:
    def update_idletasks(self):
        pass


class FakeWidget:
    def __init__(self, master):
        self.master = master


print("\n---- deterministic viewport behavior ----")
app = A.App.__new__(A.App)
app.root = FakeRoot()
app._page_canvas = FakeCanvas()
app._page_body = FakeBody()
app._page_window = "document"
app._page_sections = {
    "desk": FakeSection(0), "devices": FakeSection(300),
    "bluetooth": FakeSection(600), "system": FakeSection(1250),
}

app._sync_page_scrollregion()
check("scrollregion covers the full document", app._page_canvas.region
      == (0, 0, 600, 1500), repr(app._page_canvas.region))
app._on_page_viewport_configure(SimpleNamespace(width=777))
check("viewport resize sets embedded document width", app._page_canvas.width
      == 777, repr(app._page_canvas.width))
check("unknown anchors are rejected without moving", app.show_section("nope")
      is False and app._page_canvas.fraction is None)
check("'console' is not an anchor any more -- it is a window",
      app.show_section("console") is False
      and app._page_canvas.fraction is None)
# The LAST section cannot be scrolled past the end of the document, so its
# anchor lands on the furthest reachable top rather than on its own y.
check("the last section's anchor scrolls to the furthest reachable top",
      app.show_section("system") is True
      and abs(app._page_canvas.fraction - (1000 / 1500)) < 1e-9,
      repr(app._page_canvas.fraction))

inside = FakeWidget(app._page_canvas)
result = app._on_page_mousewheel(SimpleNamespace(
    widget=inside, delta=-120, num=None))
check("wheel over ordinary page content scrolls the document",
      result == "break" and app._page_canvas.scrolls[-1] == (3, "units"),
      repr(app._page_canvas.scrolls))
outside = FakeWidget(None)
before = list(app._page_canvas.scrolls)
check("wheel outside the page is untouched",
      app._on_page_mousewheel(SimpleNamespace(
          widget=outside, delta=-120, num=None)) is None
      and app._page_canvas.scrolls == before)


print("\n---- the log outlives its window ----")


class FakeConsoleText:
    """Enough of tk.Text for the console's own methods, and no window.

    The buffer/window split is the whole point of the change, so it is driven
    here rather than described: no Toplevel is built, and the app on Doug's
    desk is not touched.
    """

    def __init__(self, tail=1.0):
        self.rows = []        # one entry per inserted line
        self.state = "disabled"
        self.tail = tail      # what yview() reports as the visible end
        self.saw_end = 0      # how many times see("end") was called

    def config(self, **kwargs):
        self.state = kwargs.get("state", self.state)

    def insert(self, _where, text, _tag=None):
        if not self.rows or self.rows[-1].endswith("\n"):
            self.rows.append(text)
        else:
            self.rows[-1] += text

    def delete(self, _start, _end=None):
        self.rows = []

    def index(self, _spec):
        return f"{max(1, len(self.rows))}.0"

    def yview(self):
        return (0.0, self.tail)

    def see(self, _where):
        self.saw_end += 1


console = A.App.__new__(A.App)
console._console_lines = collections.deque(maxlen=4)
console._console_win = None
console._console_text = None          # the window is CLOSED

for _n in range(6):
    console.log("event", f"line {_n}")
check("with no window open, output still lands in the buffer",
      len(console._console_lines) == 4, repr(len(console._console_lines)))
check("the buffer keeps the NEWEST lines when it is full",
      [row[2] for row in console._console_lines]
      == ["line 2", "line 3", "line 4", "line 5"],
      repr([row[2] for row in console._console_lines]))
check("each buffered line carries the moment it happened",
      all(len(row) == 3 and row[0][2] == ":" for row in console._console_lines),
      repr(console._console_lines[0]))

# ...and now Doug reopens it. The window is rebuilt from the buffer alone.
console._console_text = FakeConsoleText()
console._console_replay()
check("a reopened console shows the history it was closed with",
      [row.split(" ", 1)[1].rstrip("\n")
       for row in console._console_text.rows]
      == ["line 2", "line 3", "line 4", "line 5"],
      repr(console._console_text.rows))
check("a freshly opened console opens at the tail",
      console._console_text.saw_end >= 1)

# TAIL-FOLLOWING: at the bottom, follow. Scrolled up to read, do not yank.
console._console_text = FakeConsoleText(tail=1.0)
console._console_replay()
following = console._console_text.saw_end
console.log("event", "arrived while at the bottom")
check("new output follows the tail when the view is already at the bottom",
      console._console_text.saw_end == following + 1,
      f"{following} -> {console._console_text.saw_end}")

console._console_text = FakeConsoleText(tail=0.42)   # scrolled up, reading
console._console_text.saw_end = 0
console.log("event", "arrived while he was reading")
check("new output does NOT yank a console he has scrolled up in",
      console._console_text.saw_end == 0,
      f"see(end) called {console._console_text.saw_end} times")
check("...but the line is there waiting, in the widget and in the buffer",
      any("arrived while he was reading" in row
          for row in console._console_text.rows)
      and console._console_lines[-1][2] == "arrived while he was reading")

console._console_clear()
check("Clear empties BOTH the buffer and the open widget",
      not console._console_lines and not console._console_text.rows)
console._console_text = FakeConsoleText()
console._console_replay()
check("and a console reopened after Clear is empty, not refilled",
      console._console_text.rows == [])

root = tk.Tk()
root.withdraw()
themed_app = A.App.__new__(A.App)
themed_app.root = root
themed_app._theme()
style = ttk.Style(root)
check("dark scrollbar has no light platform trough",
      style.lookup(A.SCROLLBAR_STYLE, "troughcolor").lower() == A.BG.lower()
      and style.lookup(A.SCROLLBAR_STYLE, "background").lower()
      == A.CARD.lower()
      and style.lookup(A.SCROLLBAR_STYLE, "arrowcolor").lower()
      == A.MUTED.lower())
scroll_map = dict(style.map(A.SCROLLBAR_STYLE, "background"))
check("dark scrollbar exposes pressed, hover and disabled feedback",
      scroll_map.get("pressed", "").lower() == A.ACCENT_DIM.lower()
      and scroll_map.get("active", "").lower() == A.PRESS.lower()
      and scroll_map.get("disabled", "").lower() == A.PANEL.lower(),
      repr(scroll_map))
# LAW 10, inverted from what this file used to assert. A real tk.Text inside
# the page no longer keeps the wheel: it is sized to its own content, so there
# is nothing for it to scroll and the page takes the turn.
nested = tk.Text(root)
nested.master = app._page_canvas
before = list(app._page_canvas.scrolls)
check("a nested Text hands the wheel to the page",
      app._on_page_mousewheel(SimpleNamespace(
          widget=nested, delta=-120, num=None)) == "break"
      and app._page_canvas.scrolls == before + [(3, "units")],
      repr(app._page_canvas.scrolls))
nested.master = root
nested.destroy()

real_canvas = tk.Canvas(root, width=600, height=300)
real_body = tk.Frame(real_canvas)
real_item = real_canvas.create_window((0, 0), window=real_body, anchor="nw")
real_sections = {}
for index, key in enumerate(A.PAGE_KEYS):
    frame = tk.Frame(real_body, width=600, height=80 + index * 10)
    frame.pack_propagate(False)
    tk.Label(frame, text=key).pack()
    frame.pack(fill="x")
    real_sections[key] = frame
root.update_idletasks()
real_app = A.App.__new__(A.App)
real_app.root = root
real_app._page_canvas = real_canvas
real_app._page_body = real_body
real_app._page_window = real_item
real_app._page_sections = real_sections
real_app._on_page_viewport_configure(SimpleNamespace(width=600))
root.update_idletasks()
real_app._sync_page_scrollregion()
before_bbox = tuple(int(value) for value in real_canvas.cget(
    "scrollregion").split())
check("real Tk maps every section in document order",
      real_body.pack_slaves() == [real_sections[key] for key in A.PAGE_KEYS],
      repr(real_body.pack_slaves()))
check("real embedded document receives the viewport width",
      int(float(real_canvas.itemcget(real_item, "width"))) == 600)
extra = tk.Frame(real_body, width=600, height=180)
extra.pack_propagate(False)
extra.pack(fill="x")
root.update_idletasks()
real_app._on_page_content_configure()
after_bbox = tuple(int(value) for value in real_canvas.cget(
    "scrollregion").split())
check("real dynamic content grows the reachable scrollregion",
      after_bbox[3] > before_bbox[3],
      f"{before_bbox} -> {after_bbox}")
real_canvas.destroy()
root.destroy()


print("\n---- viewport height is independent of document length ----")
for available, wanted, minimum in ((1440, 930, 680), (800, 800, 680),
                                    (600, 600, 600), (200, 400, 400)):
    got = A.page_window_plan(available)
    check(f"{available}px work area -> {wanted}px viewport / {minimum}px floor",
          got == (wanted, minimum), repr(got))
check("page_window_plan has no content-height parameter",
      "content" not in A.page_window_plan.__code__.co_varnames[
          :A.page_window_plan.__code__.co_argcount])

print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
