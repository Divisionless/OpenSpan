# SPDX-License-Identifier: AGPL-3.0-or-later
"""The DASHBOARD is one ordered, vertically scrolling document.

FOUR sections, not five. The console left the page on 2026-08-27 and is a
sibling surface of it since 2026-08-28. Law 10, as Doug defined it: *"nested
scrolling is a scrolling surface inside another surface that scrolls."* The
harm is scroll hijack -- his gesture is on the page, the pointer drifts over a
smaller scrollable region, and the scroll is taken away from him mid-gesture.
The test is same-axis CONTAINMENT, so two scrollers in one window are lawful
as long as neither is inside the other. The console was the one surface with an
unbounded producer, so it was the one that had to leave the page -- and having
left, it may scroll, follow its own tail, and keep a generous history.

This file owns the Dashboard's document contract and the console's buffer. The
dock that switches between them is asserted in test_dock_surfaces.py.

The test never constructs App: doing that starts workers and can touch the VM.
It reads the assembly contract from the AST, then drives the shipped viewport
and console methods against deterministic fakes and withdrawn Tk controls. No
Toplevel is ever built here -- there is no longer one to build -- and the
console's contract is asserted from source, its behaviour from its own methods
against a fake Text.
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
mount_console = _method("App", "_console_mount")
check("App.__init__ exists", init is not None)
check("all viewport methods exist",
      all((show, wheel, viewport, content, mount_console)))
parent, packs = _layout(init)
init_src = ast.unparse(init)


print("\n---- one document, not selectable panes ----")
# FOUR sections since 2026-08-27. Console was the fifth and is now a sibling
# surface: law 10 forbids a scroller inside a scroller, and the console is the
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
# LAW 10 IS PER SURFACE, NOT PER MODULE AND NOT PER WINDOW. It forbids a
# scrolling surface INSIDE another scrolling surface, because the wheel is
# hijacked mid-gesture; two scrollers that are SIBLINGS contain each other
# nowhere and hijack nothing. So the number to assert is one scrollbar per
# surface, and which method each one is built in is the whole of the claim:
# the Dashboard's, in App.__init__, and the Console's, in _console_mount. This
# once asserted 3 (page, device tree, in-page console), then 1, then 2 for the
# console window; it is still 2, and now both live in the same window --
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
# ONE PER SURFACE, and the count moves when a surface is added -- it has been
# 3, then 1, then 2, now 3 again, and each change was a law-10 decision rather
# than drift. So this asserts OWNERSHIP, not a number in isolation: every
# scroller belongs to exactly one surface, and no method owns two.
check("law 10: exactly three scrollbars, one per surface",
      len(scrollbars) == 3, f"{len(scrollbars)} Scrollbar calls")
check("the Dashboard owns exactly one, built in App.__init__",
      scroll_owners.get("App.__init__") == 1, repr(scroll_owners))
check("the Console owns exactly one, built in _console_mount",
      scroll_owners.get("App._console_mount") == 1, repr(scroll_owners))
check("Scripts owns exactly one, built in _build_scripts_surface",
      scroll_owners.get("App._build_scripts_surface") == 1,
      repr(scroll_owners))
check("no fourth owner: nothing else in the module builds a scroller",
      set(scroll_owners) == {"App.__init__", "App._console_mount",
                             "App._build_scripts_surface"},
      repr(scroll_owners))
check("the dock rail builds no scroller: a rail that scrolls could hide an "
      "entry, and every entry must always be reachable",
      not [n for n in ast.walk(_method("App", "_build_dock_rail"))
           if isinstance(n, ast.Call)
           and _name(n.func) in ("ttk.Scrollbar", "tk.Canvas")])
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
# LAW 10 AND THE FAULT BANNER (v3.157). The Radio options panel it replaced was
# a stack of packed widgets; this is a Frame of Frames that appears only while
# an audit has found a fault. Nothing in it scrolls, so it cannot be a second
# scroller inside the page's one scroller -- and it is shown and hidden by the
# packer, never by moving a viewport over hidden content.
_fault_row_src = _body_src(_method("BtPanel", "_build_fault_row"))
check("the fault banner introduces no scrolling surface of its own",
      not any(token in _fault_row_src for token in
              ("Scrollbar", "yscrollcommand", "xscrollcommand", "tk.Text",
               "tk.Canvas", "Treeview", "tk.Listbox")), _fault_row_src)
check("it is shown and hidden by the packer, not by a scroll position",
      "pack_forget()" in _body_src(_method("BtPanel", "_show_faults")))

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
# unbounded log could not grow the page. In a surface of its own nothing about
# height depends on length, so only memory is left and the number is generous
# -- and it now bounds the App's line BUFFER, which is the log and outlives any
# view of it.
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
check("painting a built view is the SECOND half, and is skipped when the "
      "console has never been invoked", "self._console_text" in paint_src
      and "if widget is None" in paint_src, paint_src)
check("the widget is kept the same length as the buffer that feeds it",
      "trim_text_to_lines(widget, CONSOLE_BUFFER_LINES)" in paint_src)
check("trimming still deletes from the TOP, so the newest output survives",
      'widget.delete("1.0", f"{lines - retained + 1}.0")' in SOURCE)

console_src = _body_src(mount_console) if mount_console else ""
replay_src = _body_src(_method("App", "_console_replay"))
check("a console is rebuilt from the buffer, from empty",
      "widget.delete('1.0', 'end')" in replay_src
      and "self._console_paint(tuple(self._console_lines))" in replay_src,
      replay_src)
check("building the view replays the buffer into it",
      "self._console_replay()" in console_src, console_src)
clear_src = _body_src(_method("App", "_console_clear"))
check("Clear clears BOTH copies, or the console refills itself on rebuild",
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
# is the assertion, and both are the console surface's. (BtPanel.rename's
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
check("the only tail-chasing see('end') calls belong to the console surface",
      seers == {"_console_paint", "_console_replay"}, repr(sorted(seers)))


section_locals = ("pane_desk", "pane_devices", "pane_bluetooth",
                  "pane_system")
check("all four section frames are direct children of the document",
      all(parent.get(local) == "bridge" for local in section_locals),
      repr({local: parent.get(local) for local in section_locals}))
check("there is no fifth section frame left behind",
      "pane_console" not in SOURCE and len(section_locals) == len(A.PAGE_KEYS))
check("the Dashboard's own cavity is a child of the body, beside the rail",
      parent.get("main") == "body" and parent.get("body") == "full",
      repr({"main": parent.get("main"), "body": parent.get("body")}))
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


# ---- the three crossing options moved to the picture they describe ---------
# They were rows 1-3 of the System section's ctl grid, two page sections away
# from the arrangement every one of them is a statement about. Doug asked for
# them directly under the canvas. PLACEMENT ONLY -- the variables, the commands
# and the persistence are the same objects they always were.
print("\n---- the crossing options sit under the arrangement ----")

checkbuttons = [node for node in ast.walk(init)
                if isinstance(node, ast.Call)
                and _name(node.func) == "ttk.Checkbutton"]
masters = [_name(node.args[0]) if node.args else None
           for node in checkbuttons]
labels = ["".join(part.value for part in ast.walk(word.value)
                  if isinstance(part, ast.Constant)
                  and isinstance(part.value, str))
          for node in checkbuttons for word in node.keywords
          if word.arg == "text"]
check("the Desk options frame is a sibling of the arrangement card, in the "
      "Desk section -- not a child of it, because the card is CARD-coloured "
      "and the ttk TCheckbutton style is configured on BG",
      parent.get("deskopts") == "pane_desk",
      repr(parent.get("deskopts")))
check("EVERY checkbutton in the window is in that frame",
      masters and set(masters) == {"deskopts"}, repr(masters))
check("...and it is the three crossing options, by name",
      len(checkbuttons) == 3
      and any("Invert scroll wheel" in text for text in labels)
      and any("mouse side button" in text for text in labels)
      and any("nearest screen" in text for text in labels), repr(labels))
ctl_children = [_name(node.args[0]) for node in ast.walk(init)
                if isinstance(node, ast.Call)
                and _name(node.func) in ("ttk.Button", "tk.Button",
                                         "ttk.Checkbutton", "self._portal_button")
                and node.args and _name(node.args[0]) == "ctl"]
check("none of them is left in the System section's ctl grid, and what is left "
      "of that row is the single Edit keymap button",
      "ttk.Checkbutton(ctl" not in SOURCE and len(ctl_children) == 1
      and "Edit keymap" in init_src, repr(ctl_children))
check("a grid of one is not a grid -- the row's columnconfigure went with the "
      "buttons that shared it",
      "ctl.columnconfigure" not in SOURCE and "ctl.grid" not in SOURCE)
# The third is only meaningful while the second is on, and said so with an
# indent. A pack has no columnspan to lose, so the inset is the padx.
jump = next((node for node in checkbuttons
             if any(word.arg == "text" and isinstance(word.value, ast.Constant)
                    and "nearest screen" in word.value.value
                    for word in node.keywords)), None)
inset = None
for node in ast.walk(init):
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "pack" and node.func.value is jump):
        inset = next((ast.literal_eval(word.value) for word in node.keywords
                      if word.arg == "padx"), None)
check("the jump option is still visually indented under the option it "
      "qualifies", isinstance(inset, tuple) and inset[0] >= 20, repr(inset))
check("all three still carry their own variable and their own command",
      all(len({word.arg for word in node.keywords} & {"variable", "command"})
          == 2 for node in checkbuttons))
check("and their persistence is untouched: the scroll flag is still a setting "
      "and the two crossing flags still live in the arrangement",
      'load_setting("scroll_invert", False)' in init_src.replace("'", '"')
      and '"cross_requires_side_button"' in init_src.replace("'", '"')
      and '"side_button_jumps_nearest"' in init_src.replace("'", '"'))


# ---- what Doug asked to be gone is GONE ------------------------------------
# Not hidden, not disabled: deleted, with the handlers that served them. Doug on
# the VM and shutdown buttons: *"Those buttons have never worked very well due
# to just how complex everything is that we are doing. I would always rather
# restart the machine than try to deal with all the race conditions that spawn
# with those buttons."* A machine restart is the supported recovery path.
print("\n---- the removed controls ----")

# Asked precisely, of the WIDGETS, not of the file. A substring sweep over
# openspan.py would trip on the paragraphs recording why these are gone -- and
# deleting the record to make a test pass is how the removal comes back as a
# "missing feature". So: every string this window ever puts on a button, and
# every string it ever relabels one to.
def button_labels(function):
    seen = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        target = _name(node.func) or ""
        is_button = target in ("ttk.Button", "tk.Button")
        is_relabel = target.endswith(".config") or target.endswith(".configure")
        if not (is_button or is_relabel):
            continue
        for word in node.keywords:
            if word.arg != "text":
                continue
            seen.update(part.value for part in ast.walk(word.value)
                        if isinstance(part, ast.Constant)
                        and isinstance(part.value, str))
    return seen


labels_anywhere = set()
for _node in ast.walk(MODULE):
    if isinstance(_node, ast.FunctionDef):
        labels_anywhere |= button_labels(_node)
for label in ("Bridge VM ✓", "Start Bridge VM", "Start VM", "Stop VM",
              "Cold-restart VM", "Restart keyboard", "Restart audio",
              "⏻ Shut down everything"):
    check(f"no button in the window is labelled “{label}”",
          label not in labels_anywhere, label)
# The Bluetooth panel's own audio remedy is a DIFFERENT control from the System
# grid's "Restart audio", and it survives: it sits beside the headphones it
# fixes, and restart_everything -- which it calls -- was never one of the VM
# teardown verbs.
check("the Bluetooth panel keeps its own “⟳ Restart audio”",
      "⟳ Restart audio" in labels_anywhere
      and 'self.btn_restart_audio = ttk.Button(bar, text="⟳ Restart audio"'
      in SOURCE)
# The tray entry is not a button and must NOT go: it is one of the two routes
# left to a graceful stop, and it reaches it through the same close dialog the
# window's X opens.
check("the tray's shut-down entry survives, and goes through the close dialog",
      'label="⏻  Shut down everything"' in SOURCE
      and "self._confirm_close" in SOURCE)
check("the System section's portal copy went with the row it shared",
      "self.portal_btn" not in SOURCE and "self.vm_btn" not in SOURCE)
check("...and the floating Desk portal control is the survivor",
      "self.desk_portal_btn" in SOURCE)
check("the five-button System grid and its registry are gone",
      "_sysbtn" not in SOURCE and "sysbtns" not in SOURCE)
check("the Modules section is gone, host, worker, painter and all",
      '_section(pane_system, "Modules")' not in SOURCE
      and "modules_box" not in SOURCE and "_module_worker" not in SOURCE
      and "_draw_modules" not in SOURCE and "module_host" not in init_src)
# The absence has to be documented where the widgets were, or it comes back as
# a "missing feature".
check("the source records WHY there is no shutdown or VM control",
      "restart the machine" in SOURCE
      and "supported recovery path" in SOURCE.lower())
# The one graceful stop that survives is not a button: it is the window's X and
# the tray entry that opens the same dialog.
check("the close dialog still reaches the full stop",
      "self._full_stop()" in SOURCE and "def _full_stop" in SOURCE)


print("\n---- fixed safety header, scrolling footer ----")
check("readiness remains above the page Canvas in fixed chrome",
      parent.get("self.ready_lbl") == "full"
      and parent.get("page_canvas") == "main")
check("the build footer is the document's last row",
      parent.get("foot") == "bridge")
check("the footer states the canonical copyleft license",
      "AGPL-3.0-or-later" in init_src and "open source · MIT" not in init_src)
# CONSOLE IS NEITHER A PAGE ANCHOR NOR A WINDOW. It was a section, then a
# header button that opened a Toplevel; it is a dock surface now, and the rail
# entry is the only way in. A second control onto the same surface would be a
# second place for the two to disagree about what is showing.
check("the header carries no Console control at all",
      "_cons_btn" not in SOURCE and '"↓  Console"' not in SOURCE
      and '"↗  Console"' not in SOURCE and "_toggle_console" not in SOURCE)
check("the console window and its closer are gone by name",
      "_open_console_window" not in SOURCE
      and "_close_console_window" not in SOURCE)
check("the console surface opens no window and keeps no window state",
      "Toplevel" not in console_src and "protocol(" not in console_src
      and "_console_win" not in SOURCE and "_console_geom" not in SOURCE,
      console_src)
check("it is built once and reused -- a second invoke does not build a "
      "second Text", "if self._console_text is not None" in console_src
      and "return self._console_text" in console_src, console_src)
check("it builds into its registered surface, not into the page",
      "self._dock_surfaces.get('console')" in console_src, console_src)
check("putting the console away is the dock's job, so it grows no Close "
      "button of its own", "Close" not in console_src, console_src)


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
    def __init__(self):
        self.minimums = [(940, 680)]
        self.width = 1120
        self.height = 930
        self.geometries = []

    def update_idletasks(self):
        pass

    def minsize(self, width=None, height=None):
        if width is None:
            return self.minimums[-1]
        self.minimums.append((width, height))
        return None

    # Invoking a surface now places the window as well as packing it -- see
    # App._dock_place, and test_dock_surfaces for the contract. Nothing here
    # should reach these; they exist so that if something does, it is recorded
    # rather than raising an AttributeError that reads like a different bug.
    def geometry(self, spec):
        self.geometries.append(spec)

    def winfo_width(self):
        return self.width

    def winfo_height(self):
        return self.height


class FakeWidget:
    def __init__(self, master):
        self.master = master


class FakeSurface:
    """A surface frame that records only whether it is packed."""

    def __init__(self):
        self.packed = False

    def pack(self, **_kwargs):
        self.packed = True

    def pack_forget(self):
        self.packed = False


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
# show_section invokes the Dashboard before it scrolls, so the fake app needs a
# dock. Writing settings is stubbed out: this suite must never touch the live
# openspan_settings.json of the app running on Doug's desk.
app._dock_surfaces = {key: FakeSurface() for key in A.DOCK_KEYS}
app._dock_entries = {}
app._dock_rail = None
app._dock_active = "console"
app._dock_collapsed = True
app._dock_expanded_w = None
app._desktop = None
_saved = []
A.save_setting = lambda key, value: _saved.append((key, value))

app._sync_page_scrollregion()
check("scrollregion covers the full document", app._page_canvas.region
      == (0, 0, 600, 1500), repr(app._page_canvas.region))
app._on_page_viewport_configure(SimpleNamespace(width=777))
check("viewport resize sets embedded document width", app._page_canvas.width
      == 777, repr(app._page_canvas.width))
check("unknown anchors are rejected without moving", app.show_section("nope")
      is False and app._page_canvas.fraction is None)
check("'console' is not an anchor -- it is a surface, reached from the dock",
      app.show_section("console") is False
      and app._page_canvas.fraction is None)
check("a rejected anchor does not invoke the Dashboard either",
      not app._dock_surfaces["dashboard"].packed and _saved == [])
# The LAST section cannot be scrolled past the end of the document, so its
# anchor lands on the furthest reachable top rather than on its own y.
check("the last section's anchor scrolls to the furthest reachable top",
      app.show_section("system") is True
      and abs(app._page_canvas.fraction - (1000 / 1500)) < 1e-9,
      repr(app._page_canvas.fraction))
check("...and it invoked the Dashboard first, because a section of a hidden "
      "surface cannot be scrolled to",
      app._dock_active == "dashboard" and app._dock_collapsed is False
      and app._dock_surfaces["dashboard"].packed
      and not app._dock_surfaces["console"].packed,
      repr({k: v.packed for k, v in app._dock_surfaces.items()}))

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


print("\n---- the log outlives its view ----")


class FakeConsoleText:
    """Enough of tk.Text for the console's own methods, and no widget.

    The buffer/view split is the whole point of the console, so it is driven
    here rather than described: nothing is built, and the app on Doug's desk is
    not touched.
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
console._console_text = None          # never invoked; there is no view

for _n in range(6):
    console.log("event", f"line {_n}")
check("with no console ever invoked, output still lands in the buffer",
      len(console._console_lines) == 4, repr(len(console._console_lines)))
check("the buffer keeps the NEWEST lines when it is full",
      [row[2] for row in console._console_lines]
      == ["line 2", "line 3", "line 4", "line 5"],
      repr([row[2] for row in console._console_lines]))
check("each buffered line carries the moment it happened",
      all(len(row) == 3 and row[0][2] == ":" for row in console._console_lines),
      repr(console._console_lines[0]))

# ...and now Doug invokes it. The view is built from the buffer alone.
console._console_text = FakeConsoleText()
console._console_replay()
check("a console invoked late shows the history logged before it existed",
      [row.split(" ", 1)[1].rstrip("\n")
       for row in console._console_text.rows]
      == ["line 2", "line 3", "line 4", "line 5"],
      repr(console._console_text.rows))
check("a freshly built console opens at the tail",
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
check("and a console rebuilt after Clear is empty, not refilled",
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
