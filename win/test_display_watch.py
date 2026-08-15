"""The desk is read, not remembered.

Doug, 2026-08-15: his Mac 2k arrangement "lost its third display". It was
never lost. An arrangement is a SNAPSHOT taken when saved, DISPLAY4 was
detached at that moment, and when it came back nothing re-read the desk --
so a two-monitor picture went on being treated as the truth while Windows
reported three. `Mac4k.json` still holds DISPLAY4 at x=-1920, which is how we
know it saves correctly when present.

The merge that fixes this already existed and was careful. It was simply
never reached except by pressing a button.
"""

import ast
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


ROOT = pathlib.Path(__file__).parent.parent
source = (ROOT / "win" / "openspan.py").read_text(encoding="utf-8")
tree = ast.parse(source)
app = next(n for n in tree.body
           if isinstance(n, ast.ClassDef) and n.name == "App")


def method(name):
    return next((n for n in app.body
                 if isinstance(n, ast.FunctionDef) and n.name == name), None)


watch = method("_watch_displays")
check("a watcher exists at all", watch is not None)
body = ast.get_source_segment(source, watch)

check("it asks Windows rather than trusting the saved arrangement",
      "enum_monitors()" in body)
check("it reuses the existing careful merge instead of replacing monitors",
      "_menu_refresh_monitors(automatic=True)" in body)
check("it reschedules itself, so one change is not the last one noticed",
      "self.root.after" in body and "_watch_displays" in body)
check("a dead window ends the loop quietly rather than raising in a timer",
      "tk.TclError" in body)
check("the watcher survives its own errors -- it is the desk's only link "
      "to reality", "except Exception" in body and "_emit" in body)
check("it is armed at startup", "self.ui(self._watch_displays)" in source)

# The first poll must RECORD, not report: otherwise every launch announces a
# change against a signature that never existed.
check("the first reading is a baseline, not a change",
      "self._display_sig is None" in body)
check("the baseline starts empty", "self._display_sig = None" in source)

# ---- the signature is what decides "changed" -------------------------------

# Importing openspan builds a GUI; lift the one static method out instead.
sig_node = next(n for n in ast.walk(app)
                if isinstance(n, ast.FunctionDef)
                and n.name == "_display_signature")
ns = {}
exec(compile(ast.Module(body=[sig_node], type_ignores=[]),
             "<lifted>", "exec"), ns)
signature = ns["_display_signature"].__func__ \
    if hasattr(ns["_display_signature"], "__func__") else ns["_display_signature"]

three = [
    {"name": r"\\.\DISPLAY5", "x": 0, "y": 0, "w": 1920, "h": 1080,
     "primary": True},
    {"name": r"\\.\DISPLAY1", "x": 4, "y": -1080, "w": 1920, "h": 1080,
     "primary": False},
    # The one that went missing, and it is at a NEGATIVE origin -- the class
    # of position this project has been bitten by before.
    {"name": r"\\.\DISPLAY4", "x": -1920, "y": 0, "w": 1920, "h": 1080,
     "primary": False},
]
two = three[:2]

check("losing a screen changes the signature",
      signature(three) != signature(two))
check("regaining it changes the signature back",
      signature(two) != signature(three)
      and signature(list(reversed(three))) == signature(three))
check("a negative-origin screen is part of the signature like any other",
      signature(three) != signature(
          [three[0], three[1], dict(three[2], x=1920)]))
check("mere enumeration ORDER is not a change",
      signature([three[2], three[0], three[1]]) == signature(three))
check("a resolution change is a change",
      signature(three) != signature(
          [dict(three[0], w=2560), three[1], three[2]]))
check("the primary moving is a change",
      signature(three) != signature(
          [dict(three[0], primary=False), dict(three[1], primary=True),
           three[2]]))

# ---- a background poll must never raise a modal ----------------------------

refresh = ast.get_source_segment(source, method("_menu_refresh_monitors"))
check("the refresh knows whether a person asked for it",
      "automatic" in refresh)
check("an automatic refresh with no monitors reports instead of alerting",
      re.search(r"if automatic:\s*\n\s*_emit\(", refresh) is not None)
check("the manual path still shows the dialog",
      "dark_alert(self.root, \"No monitors reported\"" in refresh)
check("the message says which of the two happened",
      "Windows screens changed" in refresh
      and "Windows screens re-read" in refresh)
check("the merge is still what updates the monitors",
      "merge_live_monitors" in refresh)
check("and the result is persisted, not just drawn",
      "self.canvas.save()" in refresh)
