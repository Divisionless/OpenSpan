# SPDX-License-Identifier: AGPL-3.0-or-later
"""The control GUI is one ordered, vertically scrolling document.

The test never constructs App: doing that starts workers and can touch the VM.
It reads the assembly contract from the AST, then drives the shipped viewport
methods against deterministic fakes and withdrawn Tk controls.
"""
import ast
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
toggle = _method("App", "_toggle_console")
check("App.__init__ exists", init is not None)
check("all viewport methods exist", all((show, wheel, viewport, content, toggle)))
parent, packs = _layout(init)
init_src = ast.unparse(init)


print("\n---- one document, not selectable panes ----")
expected = (("desk", "Desk"), ("devices", "Devices"),
            ("bluetooth", "Bluetooth"), ("system", "System"),
            ("console", "Console"))
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
# LAW 10: one vertical scroller per window, and it is the page's. This used to
# assert 3 -- the page, the Bluetooth device tree and the console. The other
# two were nested scrollers; the correct number is 1.
check("law 10: the page's is the only scrollbar in the module",
      len(scrollbars) == 1, f"{len(scrollbars)} Scrollbar calls")
check("the one scrollbar uses the dark named style",
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

for receiver in ("self.out", "self.console"):
    mutators = _mutators(receiver)
    check(f"{receiver} has mutation points to check", bool(mutators))
    for where, body in mutators:
        check(f"{where} refits {receiver} to its content",
              f"fit_text_height({receiver}" in body
              or "fit_text_height(c)" in body, where)
    check(f"{receiver} never scrolls itself to the tail",
          f"{receiver}.see(" not in SOURCE)
check("neither Text declares a fixed line count",
      "height=5" not in bt_init
      and "height=TEXT_MIN_LINES" in bt_init
      and "height=TEXT_MIN_LINES" in init_src)

check("the console cap is one module-level number",
      isinstance(A.CONSOLE_RETAINED_LINES, int)
      and A.CONSOLE_RETAINED_LINES > 0, repr(A.CONSOLE_RETAINED_LINES))
log_src = _body_src(_method("App", "log"))
check("the console bounds its CONTENT by that number, then fits what remains",
      "trim_text_to_lines(c, CONSOLE_RETAINED_LINES)" in log_src
      and "fit_text_height(c)" in log_src, log_src)
check("trimming deletes from the TOP, so the newest output survives",
      'widget.delete("1.0", f"{lines - retained + 1}.0")' in SOURCE)
check("no output yanks the page by chasing its own tail",
      not any(token in SOURCE for token in
              ('self.out.see("end")', 'self.console.see("end")',
               'c.see("end")')))


section_locals = ("pane_desk", "pane_devices", "pane_bluetooth",
                  "pane_system", "pane_console")
check("all five section frames are direct children of the document",
      all(parent.get(local) == "bridge" for local in section_locals),
      repr({local: parent.get(local) for local in section_locals}))
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
check("Console is an anchor shortcut, not a visibility toggle",
      "show_section" in ast.unparse(toggle) and "console" in ast.unparse(toggle)
      and "select_pane" not in ast.unparse(toggle))


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
    "bluetooth": FakeSection(600), "system": FakeSection(900),
    "console": FakeSection(1250),
}

app._sync_page_scrollregion()
check("scrollregion covers the full document", app._page_canvas.region
      == (0, 0, 600, 1500), repr(app._page_canvas.region))
app._on_page_viewport_configure(SimpleNamespace(width=777))
check("viewport resize sets embedded document width", app._page_canvas.width
      == 777, repr(app._page_canvas.width))
check("unknown anchors are rejected without moving", app.show_section("nope")
      is False and app._page_canvas.fraction is None)
check("Console anchor scrolls to the furthest reachable top",
      app.show_section("console") is True
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
