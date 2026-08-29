# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persistent header and duplicate portal controls on the scrolling page.

The single-page cutover must not discard the two independent invariants the old
pane harness also protected: critical header tokens survive minimum width, and
both portal buttons remain views of one writer and one backend.
"""
import ast
import json
import os
import shutil
import sys
import tempfile
import threading
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


init = _method("App", "__init__")
init_src = ast.unparse(init)
root = tk.Tk()
root.withdraw()


print("\n---- critical header tokens at the 940px minimum ----")
loops = [node for node in ast.walk(init) if isinstance(node, ast.For)
         and any(isinstance(sub, ast.Subscript)
                 and _name(sub.value) == "self._ind"
                 for sub in ast.walk(node))]
check("the shipped header is built from INDICATOR_ORDER",
      len(loops) == 1 and _name(loops[0].iter) == "INDICATOR_ORDER")

try:
    live = json.loads(open(os.path.join(os.path.dirname(HERE),
                                       "openspan_config.json"),
                           encoding="utf-8").read())
    names = [(row.get("name") or row.get("id") or "device")
             for row in live.get("devices", [])]
except Exception:  # noqa: BLE001
    names = ["Managed Laptop", "Managed Mac", "iPad"]
longest = sorted(names, key=len, reverse=True)[:2] or ["A", "B"]
widest = {
    "vm": "VM ●", "ipad": "Managed Mac ● connected",
    "mac": "devices 3/3", "portal": "portal ○ off", "audio": "audio ●",
    "bcast": f"📡 {A.broadcast_names(longest)} BROADCASTING",
    "admin": "⚠ NOT ADMIN",
}
probe = tk.Frame(root, bg=A.BG)
probe.place(x=16, y=0, width=908, height=30)
probe.pack_propagate(False)
tokens = {}
for key in A.INDICATOR_ORDER:
    label = tk.Label(probe, text=widest[key], bg=A.BG, fg=A.MUTED,
                     font=("Consolas", 10))
    label.pack(side="left", padx=(0, 14))
    tokens[key] = label
root.update_idletasks()
placed = {key: (widget.winfo_width() > 1
                and widget.winfo_width() + 14
                >= widget.winfo_reqwidth() + 14)
          for key, widget in tokens.items()}
lost = [key for key in A.INDICATOR_MUST_SURVIVE if not placed[key]]
check("every non-negotiable header token survives minimum width",
      not lost, repr(lost))
check("the UIPI admin warning is first and survives",
      A.INDICATOR_ORDER[0] == "admin" and placed["admin"])
check("only the transient broadcast token may yield",
      all(placed[key] for key in A.INDICATOR_ORDER if key != "bcast"))
check("an empty elevated admin token adds no trailing indent",
      "_k == 'admin' and is_elevated()" in init_src)
probe.destroy()


print("\n---- both portal controls have one writer and backend ----")
app_class = next(node for node in MODULE.body
                 if isinstance(node, ast.ClassDef) and node.name == "App")
labels = {"Start portal", "Stop portal"}
styles = {"TButton", "Warn.TButton"}


def writes_portal_state(node):
    for call in ast.walk(node):
        if not (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr in ("config", "configure")):
            continue
        for keyword in call.keywords:
            values = {part.value for part in ast.walk(keyword.value)
                      if isinstance(part, ast.Constant)
                      and isinstance(part.value, str)}
            if keyword.arg == "text" and values & labels:
                return True
            if keyword.arg == "style" and values & styles:
                return True
    return False


writers = [method.name for method in app_class.body
           if isinstance(method, ast.FunctionDef)
           and writes_portal_state(method)]
check("exactly one App method writes portal text or style",
      writers == ["_render_portal_button"], repr(writers))
factory = _method("App", "_portal_button")
render = _method("App", "_render_portal_button")
busy = _method("App", "_busy_portal")
factory_src = ast.unparse(factory)
render_src = ast.unparse(render)
busy_src = ast.unparse(busy)
check("the shipped portal control uses the one factory",
      "self.desk_portal_btn = self._portal_button(" in SOURCE)
# The System section's copy is GONE with the "Bridge VM ✓" button it shared a
# row with. One surface now -- but the factory, the registry and the renderer
# are unchanged, which is the whole point of them: the count is data, not shape.
check("the System section's second copy is gone, not merely unpacked",
      "self.portal_btn" not in SOURCE)
check("exactly one portal control is built in the window",
      SOURCE.count("self._portal_button(") == 1,
      str(SOURCE.count("self._portal_button(")))
check("the factory owns command binding and registration",
      "toggle_portal_by_user" in factory_src
      and "self._portal_btns.append" in factory_src)
check("the renderer iterates the registry and names neither copy",
      "self._portal_btns" in render_src
      and "desk_portal_btn" not in render_src
      and "self.portal_btn" not in render_src)
check("the busy path parks every registered copy",
      "self._portal_btns" in busy_src and "self.busy" in busy_src)


def portal_geometry():
    factory_kwargs = place_kwargs = None
    for node in ast.walk(init):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)):
            continue
        if (node.func.attr == "_portal_button" and node.args
                and _name(node.args[0]) == "self.canvas"):
            factory_kwargs = {item.arg: ast.literal_eval(item.value)
                              for item in node.keywords}
        if node.func.attr == "place" and _name(node.func.value) \
                == "self.desk_portal_btn":
            place_kwargs = {item.arg: ast.literal_eval(item.value)
                            for item in node.keywords}
    return factory_kwargs, place_kwargs


factory_kwargs, place_kwargs = portal_geometry()
check("Desk portal placement comes from shipped source",
      factory_kwargs is not None and place_kwargs is not None,
      f"{factory_kwargs!r} / {place_kwargs!r}")

scratch = tempfile.mkdtemp(prefix="esotericos-controls-")
A.SETTINGS = os.path.join(scratch, "settings.json")
live_config = os.path.join(os.path.dirname(HERE), "openspan_config.json")
A.CONFIG = os.path.join(scratch, "config.json")
if os.path.isfile(live_config):
    shutil.copyfile(live_config, A.CONFIG)
app = A.App.__new__(A.App)
app.root = root
app._ui_thread = threading.get_ident()
app.portal_proc = None
app._portal_live = lambda: False
called = []
app.toggle_portal = lambda: called.append("toggle")
app._portal_btns = []
canvas = tk.Canvas(root, width=800, height=300)
desk_button = A.App._portal_button(app, canvas, **(factory_kwargs or {}))
desk_button.place(**(place_kwargs or {}))
# A SECOND control is still driven here even though the window ships one. The
# registry, the renderer and the parked wait are the machinery that made adding
# the Desk copy safe and removing the System copy a one-line change; a test that
# only ever drives one button stops proving any of it, and the two-surfaces-
# one-state bug it guards against is exactly what would come back.
second = tk.Frame(root)
second_button = A.App._portal_button(app, second)
second_button.pack()
app.desk_portal_btn = desk_button
check("the factory registered both controls in build order",
      app._portal_btns == [desk_button, second_button])

for state, text, style in ((False, "Start portal", "Warn.TButton"),
                           (True, "Stop portal", "TButton")):
    A.App._render_portal_button(app, state)
    observed = [(button.cget("text"), str(button.cget("style")))
                for button in app._portal_btns]
    check(f"both controls render the {'on' if state else 'off'} state",
          observed == [(text, style), (text, style)], repr(observed))

A.App._render_portal_button(app, True)
restore = A.App._busy_portal(app, "Stopping portal…")
check("a parked wait disables and labels both controls",
      all("disabled" in button.state()
          and button.cget("text") == "Stopping portal…"
          for button in app._portal_btns))
A.App._render_portal_button(app, False)
check("poll rendering cannot overwrite either parked wait",
      all(button.cget("text") == "Stopping portal…"
          for button in app._portal_btns))
restore()
check("one restore releases both controls",
      all("disabled" not in button.state() for button in app._portal_btns))
desk_button.invoke()
second_button.invoke()
check("both controls reach the same backend exactly once",
      called == ["toggle", "toggle"], repr(called))


print("\n---- floating Desk control at the wider single-page canvas ----")
arrangement = A.MultiArrangeCanvas(root, on_change=None, height=270)
page_width = 1050
arrangement.winfo_width = lambda: page_width
arrangement._fit_height()
page_height = arrangement.winfo_reqheight()
arrangement.winfo_height = lambda: page_height
arrangement.redraw()
root.update_idletasks()
button_width = desk_button.winfo_reqwidth()
button_height = desk_button.winfo_reqheight()
button_x0 = (page_width - button_width) / 2
button_x1 = button_x0 + button_width
button_y1 = page_height - 8
button_y0 = button_y1 - button_height
rectangles = []
for key, item in arrangement._items():
    x, y, width, height = arrangement._rect(key, item)
    x0, y0 = arrangement.w2c(x, y)
    x1, y1 = arrangement.w2c(x + width, y + height)
    rectangles.append((key, x0, y0, x1, y1))
hits = [key for key, x0, y0, x1, y1 in rectangles
        if x0 < button_x1 and x1 > button_x0
        and y0 < button_y1 and y1 > button_y0]
check("the button overlaps no screen at the new wider page geometry",
      not hits, repr(hits))
hint = arrangement.bbox("hint")
check("the button also clears the canvas instruction line",
      hint is None or not (hint[0] < button_x1 and hint[2] > button_x0
                           and hint[1] < button_y1 and hint[3] > button_y0),
      f"hint={hint!r} button=({button_x0},{button_y0},"
      f"{button_x1},{button_y1})")

root.destroy()
shutil.rmtree(scratch, ignore_errors=True)
print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
