"""The mouse sensitivity slider has notches, and they must not eat a tuning.

Doug, 1 August: the slider was a continuous 0.1-3.0 scale whose readout showed
two decimal places while the value stored at three. It displayed `0.75` and
saved `0.747`. So the number on screen was not the number in the file, and a
setting arrived at by feel could never be read back off the dialog, let alone
typed in again.

The fix is notches. Two decisions shape what is checked here:

    * the ceiling stays at 3.0, not 2.0 — the top of the old range stays
      reachable, so no config that already exists becomes unrepresentable
    * notches snap ON DRAG ONLY, never on load — `0.747` and `0.686` are real
      tunings on his Mac and iPad. Opening the dialog to look at something
      else, then pressing Apply, must not quietly move either one

The second is the whole point of the split between `nearest_notch_index` (which
only positions the handle) and `snap_sensitivity` (which changes the value).
A test that only checked snapping would pass on an implementation that silently
rewrote both devices the first time the dialog was opened.

Runs headless: the root is withdrawn, so nothing appears on screen and a
running OpenSpan is untouched.

Exit 0 = all pass.
"""
import ast
import json
import os
import sys
import tkinter as tk

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


NOTCHES = A.SENSITIVITY_NOTCHES

# ---- the notch ladder itself -----------------------------------------------
check("the ladder is the agreed 18 notches",
      len(NOTCHES) == 18, f"{len(NOTCHES)}: {NOTCHES}")
check("it climbs, with no value repeated",
      all(NOTCHES[i] < NOTCHES[i + 1] for i in range(len(NOTCHES) - 1))
      and len(set(NOTCHES)) == len(NOTCHES), str(NOTCHES))
check("the ceiling stayed at 3.0, so nothing already saved is out of reach",
      NOTCHES[-1] == 3.0, f"top notch is {NOTCHES[-1]}")
check("the floor is 0.25",
      NOTCHES[0] == 0.25, f"bottom notch is {NOTCHES[0]}")
check("1.0 — the default, and 'no change' — is itself a notch",
      1.0 in NOTCHES)
check("the useful band 0.55-1.0 is in 0.05 steps",
      [round(v, 2) for v in NOTCHES if 0.55 <= v <= 1.0]
      == [0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0],
      str([v for v in NOTCHES if 0.55 <= v <= 1.0]))
check("above 1.0 the steps open out, because nobody tunes 2.5 vs 2.75",
      [v for v in NOTCHES if v > 1.0] == [1.25, 1.5, 1.75, 2.0, 2.5, 3.0],
      str([v for v in NOTCHES if v > 1.0]))
check("every notch is exactly two decimal places on screen",
      all(A.format_sensitivity(v) == f"{v:.2f}" for v in NOTCHES))

# ---- snapping, which is what a drag does -----------------------------------
check("a notch snaps to itself — dragging onto one and off again is a no-op",
      all(A.snap_sensitivity(v) == v for v in NOTCHES))
check("his Mac's 0.747 would snap to 0.75 IF dragged",
      A.snap_sensitivity(0.747) == 0.75, str(A.snap_sensitivity(0.747)))
check("his iPad's 0.686 would snap to 0.70 IF dragged",
      A.snap_sensitivity(0.686) == 0.7, str(A.snap_sensitivity(0.686)))
check("a value past the ceiling lands on the ceiling, not off the end",
      A.snap_sensitivity(9.9) == 3.0, str(A.snap_sensitivity(9.9)))
check("a value below the floor lands on the floor",
      A.snap_sensitivity(0.01) == 0.25, str(A.snap_sensitivity(0.01)))
check("a tie goes to the LOWER notch, so nothing drifts faster on its own",
      A.snap_sensitivity(0.375) == 0.25 and A.snap_sensitivity(2.25) == 2.0,
      f"{A.snap_sensitivity(0.375)}, {A.snap_sensitivity(2.25)}")
check("junk falls back to 1.0's notch rather than throwing in a GUI callback",
      A.snap_sensitivity(None) == 1.0 and A.snap_sensitivity("x") == 1.0)

# ---- the readout says what is in the file ----------------------------------
check("0.747 reads as 0.747, not 0.75 — the old bug, stated directly",
      A.format_sensitivity(0.747) == "0.747",
      A.format_sensitivity(0.747))
check("0.686 reads as 0.686",
      A.format_sensitivity(0.686) == "0.686", A.format_sensitivity(0.686))
check("a notch keeps the tidy two places",
      A.format_sensitivity(0.75) == "0.75"
      and A.format_sensitivity(1.0) == "1.00",
      f"{A.format_sensitivity(0.75)}, {A.format_sensitivity(1.0)}")
check("what is shown re-reads as the same number",
      all(abs(float(A.format_sensitivity(v)) - v) < 1e-9
          for v in (0.747, 0.686, 0.75, 1.0, 2.5, 3.0)))

# ---- the widget: on load it MOVES NOTHING ----------------------------------
root = tk.Tk()
root.withdraw()            # never draw on the desk this is being run from

var = tk.DoubleVar(value=0.747)
scale = A.notched_scale(root, var)
root.update_idletasks()
check("building the slider leaves a tuned 0.747 exactly where it was",
      abs(var.get() - 0.747) < 1e-9, str(var.get()))
check("...while the handle still rests on a notch, at 0.75",
      abs(scale.notch_position.get() - NOTCHES.index(0.75)) < 1e-9,
      str(scale.notch_position.get()))

ipad = tk.DoubleVar(value=0.686)
ipad_scale = A.notched_scale(root, ipad)
root.update_idletasks()
check("the same for the iPad's 0.686",
      abs(ipad.get() - 0.686) < 1e-9
      and abs(ipad_scale.notch_position.get() - NOTCHES.index(0.7)) < 1e-9,
      f"{ipad.get()} at index {ipad_scale.notch_position.get()}")

# ---- the widget: on drag it snaps ------------------------------------------
scale.set(6.4)             # a mouse lands between notches; the handle cannot
root.update_idletasks()
check("a drag that lands between notches takes the nearest one",
      abs(var.get() - 0.75) < 1e-9, str(var.get()))
check("...and the handle is left ON that notch, not where the mouse was",
      abs(scale.notch_position.get() - 6.0) < 1e-9,
      str(scale.notch_position.get()))

scale.set(11.0)
root.update_idletasks()
check("dragging to the 1.0 notch gives exactly 1.0",
      var.get() == 1.0, str(var.get()))

scale.set(len(NOTCHES) - 1)
root.update_idletasks()
check("dragging to the far right gives the 3.0 ceiling",
      var.get() == 3.0, str(var.get()))

scale.set(0)
root.update_idletasks()
check("dragging to the far left gives the 0.25 floor",
      var.get() == 0.25, str(var.get()))

reached = set()
for index in range(len(NOTCHES)):
    scale.set(float(index))
    root.update_idletasks()
    reached.add(round(var.get(), 4))
check("every one of the 18 notches is reachable by dragging",
      reached == {round(v, 4) for v in NOTCHES},
      str(sorted(set(round(v, 4) for v in NOTCHES) - reached)))

check("the value never stops between notches, whatever the mouse does",
      all((scale.set(x / 7.0), root.update_idletasks(),
           round(var.get(), 4))[2] in {round(v, 4) for v in NOTCHES}
          for x in range(0, 7 * (len(NOTCHES) - 1) + 1)))

root.destroy()

# ---- the dialog actually uses it -------------------------------------------
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "openspan.py"), encoding="utf-8") as handle:
    source = handle.read()
tree = ast.parse(source, filename="openspan.py")

sens_call = None
accel_call = None
for node in ast.walk(tree):
    if (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == "slider"
            and node.args and isinstance(node.args[0], ast.Constant)):
        if node.args[0].value == "sensitivity":
            sens_call = node
        elif node.args[0].value == "pointer_accel":
            accel_call = node

check("the sensitivity slider is built with notches",
      sens_call is not None
      and any(k.arg == "notches" for k in sens_call.keywords),
      "no notches= on the sensitivity slider")
check("pointer acceleration stays a free slide — it was never the complaint",
      accel_call is not None
      and not any(k.arg == "notches" for k in accel_call.keywords))
check("the dialog no longer prints sensitivity at a fixed two places",
      "record['sensitivity']:.2f" not in source
      and 'record["sensitivity"]:.2f' not in source,
      "the event line would still say 0.75 for a stored 0.747")

# ---- the live config is untouched by any of this ---------------------------
cfg_path = os.path.join(os.path.dirname(here), "openspan_config.json")
if os.path.exists(cfg_path):
    with open(cfg_path, encoding="utf-8") as handle:
        cfg = json.load(handle)
    devices = {d.get("id"): d for d in cfg.get("devices", [])}
    check("his tuned values are still in the file, unsnapped",
          devices.get("mac", {}).get("sensitivity") == 0.747
          and devices.get("ipad", {}).get("sensitivity") == 0.686,
          f"mac={devices.get('mac', {}).get('sensitivity')}, "
          f"ipad={devices.get('ipad', {}).get('sensitivity')}")
    check("every stored sensitivity is inside the slider's range",
          all(NOTCHES[0] <= float(d.get("sensitivity", 1.0)) <= NOTCHES[-1]
              for d in cfg.get("devices", [])),
          str({k: v.get("sensitivity") for k, v in devices.items()}))

print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
