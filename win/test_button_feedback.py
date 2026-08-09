"""A button must react to being pressed, on the button itself.

Doug, 2 August: *"when i click a button i need visual indication it has been
clicked on the button itself. it needs to react in some way, even in a pending
state while the action runs."*

The cause was a single misreading of ttk that ran through every style map in the
file. **ttk's `active` state is HOVER, not pressed.** Every button map used only
`("active", …)`, so pressing a button changed nothing about it. The only way to
learn whether a click had registered was to watch for a side effect elsewhere in
the window — and 26 of this app's actions run on a worker thread and take
seconds, so "hasn't happened yet" and "the click missed" looked identical.

Two things are checked here, and the second is the one that would have shipped
broken:

    * every button style map carries a `pressed` entry at all
    * ORDER. ttk takes the FIRST matching state, and a held button is `pressed`
      AND `active` simultaneously — so `active` listed first means the press
      never shows. A disabled button is likewise never allowed to look pressed,
      so `disabled` must precede both.

There is also a trap in the file worth pinning: `_theme_widgets` sets a TButton
map and then REPLACES it a few lines later with one that adds the disabled
colours. A `pressed` entry added only to the first is dead code that tests
green if you check the wrong one. So this asserts EVERY map, not the first one
found.

The pending/busy half of Doug's request is not covered here — it belongs with
the wave that rewrites `_apply_device_rows`, because that method re-enables the
per-device verbs on a 3-second poll tick and would stomp any busy state set on
them.

Runs headless: the root is withdrawn, so nothing appears on screen and a running
OpenSpan is untouched.

Exit 0 = all pass.
"""
import ast
import os
import sys
import tkinter as tk
from tkinter import ttk

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openspan as A  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (
        "" if cond or not detail else "\n      " + detail))
    if not cond:
        fails.append(name)


BUTTON_STYLES = ("TButton", "Accent.TButton", "Danger.TButton")

# ---- the colours are real, and distinct from hover --------------------------
for const, hover in (("PRESS", "#221F2A"), ("PRESS_ACCENT", "#764BE2"),
                     ("PRESS_DANGER", "#66294C")):
    value = getattr(A, const, None)
    check(f"{const} exists and is a colour",
          isinstance(value, str) and value.startswith("#") and len(value) == 7,
          repr(value))
    check(f"{const} is not just the hover colour again",
          value != hover, f"{value} == hover {hover}")

# ---- EVERY map in the source carries pressed, not just the first ------------
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "openspan.py"), encoding="utf-8") as handle:
    tree = ast.parse(handle.read(), filename="openspan.py")

maps = []          # (style_name, [state names in background=, in order], lineno)
for node in ast.walk(tree):
    if not (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "map"
            and node.args and isinstance(node.args[0], ast.Constant)):
        continue
    style = node.args[0].value
    if style not in BUTTON_STYLES:
        continue
    states = []
    for kw in node.keywords:
        if kw.arg != "background":
            continue
        for elt in getattr(kw.value, "elts", []):
            first = getattr(elt, "elts", [None])[0]
            if isinstance(first, ast.Constant):
                states.append(first.value)
    maps.append((style, states, node.lineno))

check("the button style maps were found at all",
      len(maps) >= 6, f"found {len(maps)}")

for style, states, lineno in maps:
    check(f"{style} map at line {lineno} has a pressed state",
          "pressed" in states, f"states: {states}")

# ORDER is the part that silently fails: ttk takes the first match, and a held
# button is pressed AND active at once.
for style, states, lineno in maps:
    if "pressed" not in states or "active" not in states:
        continue
    check(f"{style} at line {lineno}: pressed is listed BEFORE active",
          states.index("pressed") < states.index("active"),
          f"states in order: {states}")

for style, states, lineno in maps:
    if "pressed" not in states or "disabled" not in states:
        continue
    check(f"{style} at line {lineno}: disabled beats pressed",
          states.index("disabled") < states.index("pressed"),
          f"a disabled button must never look pressed — {states}")

# The specific trap: two TButton maps exist and the LATER one wins. Both must
# carry it, or whichever one is effective today can quietly be the wrong one.
tbutton_maps = [(s, st, ln) for s, st, ln in maps if s == "TButton"]
check("there really is more than one TButton map (the overriding trap)",
      len(tbutton_maps) >= 2, f"{len(tbutton_maps)} found")
check("EVERY TButton map carries pressed, including the one that overrides",
      all("pressed" in st for _s, st, _ln in tbutton_maps),
      str([(ln, st) for _s, st, ln in tbutton_maps]))

# ---- and it survives contact with a real ttk Style --------------------------
root = tk.Tk()
root.withdraw()            # never draw on the desk this is being run from
A._theme_startup_buttons()
style = ttk.Style()
for name in BUTTON_STYLES:
    spec = style.map(name, "background")
    flat = [str(entry) for pair in spec for entry in pair]
    check(f"ttk itself reports a pressed background for {name}",
          any("pressed" in token for token in flat), str(spec))
root.destroy()

print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
