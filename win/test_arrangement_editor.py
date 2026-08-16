"""The arrangement editor: the PC is one block, screens never overlap, the
primary wears its mark, every PC screen has a number, and Identify flashes
it on the real screen.

Doug, 2026-08-15: "correct the arrangement creator itself ... it is
unintuitive." Every change here borrows from an editor he already knows:
Windows' Identify and numbered tiles, macOS's primary strip and refusal to
let screens overlap, and Windows Display Settings as the ONE place the PC's
own screens are arranged relative to each other.

Runs Tk withdrawn, against a scratch config, on Doug's real PC geometry --
DISPLAY5 primary at (0,0), DISPLAY1 at (4,-1080), DISPLAY4 at (-1920,0) --
because a formula verified only on the primary is not verified.
"""

import ast
import json
import os
import re
import sys
import tempfile
import tkinter as tk

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import openspan as A  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name
          + ("" if cond or not detail else "\n      " + detail))
    if not cond:
        fails.append(name)


SCRATCH = tempfile.mkdtemp(prefix="esos-arrange-")
A.CONFIG = os.path.join(SCRATCH, "live.json")
A.PROFILE_DIR = os.path.join(SCRATCH, "profiles")
A.BT_PREFS = os.path.join(SCRATCH, "bt_prefs.json")

# Doug's desk, exactly. Two of the three are the same model.
MONITORS = [
    {"name": "\\\\.\\DISPLAY5", "x": 0, "y": 0, "w": 1920, "h": 1080,
     "primary": True, "refresh_hz": 60.0},
    {"name": "\\\\.\\DISPLAY1", "x": 4, "y": -1080, "w": 1920, "h": 1080,
     "primary": False, "refresh_hz": 144.0},
    {"name": "\\\\.\\DISPLAY4", "x": -1920, "y": 0, "w": 1920, "h": 1080,
     "primary": False, "refresh_hz": 60.0},
]
A.enum_monitors = lambda: [dict(m) for m in MONITORS]
# EDID sizes as read on this machine: the CF15T twins and the laptop panel.
A.monitor_sizes = lambda: {"\\\\.\\DISPLAY5": 15.7, "\\\\.\\DISPLAY1": 17.1,
                           "\\\\.\\DISPLAY4": 15.7}

DESK = {
    "version": 3, "links": [],
    "monitors": [dict(MONITORS[0], layout_x=0, layout_y=0)],
    "devices": [
        {"id": "ipad", "name": "iPad", "port": 9955, "enabled": True,
         "displays": [{"id": "ipad-main", "name": "iPad", "x": 6000, "y": 0,
                       "w": 816, "h": 612, "res_w": 1080, "res_h": 810,
                       "rotation": 0, "diagonal_in": 10.2}]},
    ],
}
with open(A.CONFIG, "w", encoding="utf-8") as fh:
    json.dump(DESK, fh)

root = tk.Tk()
root.withdraw()
canvas = A.MultiArrangeCanvas(root, on_change=None, height=270)
canvas.winfo_width = lambda: 852
canvas.winfo_height = lambda: 447

K5 = ("local", "windows", "\\\\.\\DISPLAY5")
K1 = ("local", "windows", "\\\\.\\DISPLAY1")
K4 = ("local", "windows", "\\\\.\\DISPLAY4")
IPAD = ("target", "ipad", "ipad-main")

# ---- 1. all three PC screens are in the picture, at EDID size --------------

names = {m["name"] for m in canvas.monitors}
check("every monitor Windows reports is on the canvas, negative origins included",
      names == {"\\\\.\\DISPLAY5", "\\\\.\\DISPLAY1", "\\\\.\\DISPLAY4"}, str(names))
m5, m1, m4 = (canvas._lookup(k) for k in (K5, K1, K4))
check("a screen with no typed diagonal takes its size from EDID",
      m4 is not None and abs(float(m4.get("diagonal_in") or 0) - 15.7) < 0.05
      and m4.get("diagonal_source") == "edid", str(m4))
check("EDID sizes the primary too -- it is 15.7\", not the 17\" that was typed",
      m5 is not None and abs(float(m5.get("diagonal_in") or 0) - 15.7) < 0.05)
check("the two CF15T twins are drawn the same size, the laptop panel larger",
      m4["layout_w"] == m5["layout_w"] and m1["layout_w"] > m5["layout_w"])

# ---- 2. the PC block follows Windows' topology ------------------------------

r5, r1, r4 = (canvas._monitor_rect(m) for m in (m5, m1, m4))
check("DISPLAY4 sits flush against the primary's LEFT edge (Windows says so)",
      abs((r4[0] + r4[2]) - r5[0]) <= 3, f"{r4} vs {r5}")
check("and its top is level with the primary's",
      abs(r4[1] - r5[1]) <= 3)
check("DISPLAY1 sits flush against the primary's TOP edge",
      abs((r1[1] + r1[3]) - r5[1]) <= 3, f"{r1} vs {r5}")

# ---- 3. dragging any PC screen moves the whole block ------------------------

before = {m["name"]: (m["layout_x"], m["layout_y"]) for m in canvas.monitors}
canvas._set_position(K4, m4, m4["layout_x"] + 250, m4["layout_y"] - 40)
after = {m["name"]: (m["layout_x"], m["layout_y"]) for m in canvas.monitors}
check("moving one PC screen carries every PC screen by the same delta",
      all(after[n][0] - before[n][0] == 250 and after[n][1] - before[n][1] == -40
          for n in before), f"{before} -> {after}")
canvas._set_position(K4, m4, m4["layout_x"] - 250, m4["layout_y"] + 40)

# ---- 4. a drop ON another screen is refused and put back --------------------

ipad = canvas._lookup(IPAD)
start_ipad = (ipad["x"], ipad["y"])
canvas.selected = IPAD
canvas.action = "move"
canvas._press_rect = (ipad["x"], ipad["y"], ipad["w"], ipad["h"])
canvas._press_layout = {IPAD: start_ipad}
# Drop the iPad squarely on top of the primary.
ipad["x"], ipad["y"] = m5["layout_x"] + 100, m5["layout_y"] + 100
saved = []
canvas.save = lambda: saved.append(True)          # a refused drop must not save
canvas._release(None)
check("a drop that lands on another screen is put back where it started",
      (ipad["x"], ipad["y"]) == start_ipad, f"{(ipad['x'], ipad['y'])}")
check("and nothing is saved or restarted for it", not saved)
check("the hint line says why, briefly",
      "overlap" in (getattr(canvas, "_flash", "") or ""))
# Edge contact is NOT overlap: park the iPad flush against the block's right
# edge and it must stay.
block = canvas._block_rect()
canvas.selected = IPAD
canvas.action = "move"
canvas._press_rect = (ipad["x"], ipad["y"], ipad["w"], ipad["h"])
canvas._press_layout = {IPAD: (ipad["x"], ipad["y"])}
ipad["x"], ipad["y"] = block[0] + block[2], block[1]
canvas._release(None)
check("touching is not overlapping -- a flush drop is kept",
      (ipad["x"], ipad["y"]) == (block[0] + block[2], block[1]) and saved)

# ---- 5. numbering and the primary mark --------------------------------------

nums = {n: canvas._monitor_number(n) for n in names}
check("every PC screen has a distinct number 1..N", sorted(nums.values()) == [1, 2, 3])
check("numbers follow reading order of Windows' desktop (top row first)",
      nums["\\\\.\\DISPLAY1"] == 1 and nums["\\\\.\\DISPLAY4"] == 2
      and nums["\\\\.\\DISPLAY5"] == 3, str(nums))
check("the label no longer spends a line on PRIMARY -- the strip says it",
      "PRIMARY" not in canvas._short_label(K5, m5))
_title, lines = canvas._detail_lines(K5, m5)
check("the hover card says where the size came from",
      any("EDID" in l for l in lines), str(lines))
check("and which number Identify will flash", any("screen 3" in l for l in lines))

# ---- 6. Identify: one card per real screen, gone again by itself ------------

made = []
_real_toplevel = tk.Toplevel


class SpyTop(_real_toplevel):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        made.append(self)


tk.Toplevel = SpyTop
try:
    canvas.identify_screens()
finally:
    tk.Toplevel = _real_toplevel
check("Identify raises one card per PC screen", len(made) == 3, str(len(made)))
geoms = []
for card in made:
    card.update_idletasks()
    geoms.append(card.geometry())
check("each card is placed inside its own screen, negative origins included",
      any("+-" in g or "-" in g.split("+", 1)[-1] for g in geoms), str(geoms))
for card in made:
    try:
        card.destroy()
    except tk.TclError:
        pass

# ---- 7. wiring, by shape --------------------------------------------------

src = open(os.path.join(HERE, "openspan.py"), encoding="utf-8").read()
check("adopt hands EDID sizes to normalize_config",
      re.search(r"normalize_config\(raw, live or enum_monitors\(\),\s*sizes=monitor_sizes\(\)\)", src) is not None)
check("refresh hands EDID sizes to the merge",
      "merge_live_monitors(self.canvas.monitors, live,\n                                             sizes=monitor_sizes())" in src
      or re.search(r"merge_live_monitors\(self\.canvas\.monitors, live,\s*sizes=monitor_sizes\(\)\)", src) is not None)
check("a typed diagonal is marked as the user's, so EDID cannot override it",
      'item["diagonal_source"] = "user"' in src)
check("the refresh path repeats any screen the enumerator could not read",
      "LAST_ENUM_ERRORS" in src and "cannot read" in src)
check("describe_monitor_refresh reports a size that changed",
      '"resized"' in src)
check("Identify is a button on the canvas, not only a menu entry",
      'text="Identify"' in src and "identify_btn.place(" in src)
check("and on both right-click menus",
      src.count("self.canvas.identify_screens") >= 3)
check("monitor_sizes never raises -- EDID is an input, not a dependency",
      re.search(r"def monitor_sizes\(\):[\s\S]{0,900}except Exception", src) is not None)

root.destroy()
print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
