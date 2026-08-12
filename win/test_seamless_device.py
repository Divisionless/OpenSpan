"""One device is one surface. Its internal seams are not crossings.

Doug, 2026-08-11, on the Managed Mac: "I can feel it when I move my mouse
left ... I sometimes run into a vertical seam where as I move my mouse
across it, it jumps." And: "side mouse button should not travel to
intra-device seams, only where two devices meet."

Both came from treating a device's own screen boundary like a device
boundary. _switch_target called _place() -- "the ONLY discontinuous
assignment of the model position" -- which warps the pointer to a computed
landing point. Across an internal seam the target's window server has
ALREADY carried its pointer over, so that warp overrides a correct position
and any disagreement shows up as a jerk at one fixed screen column.

The layout here is a two-screen device (the Mac's shape), a third device
off to its left, and the PC to its right.
"""

import pathlib
import queue
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import openspan_portal  # noqa: E402
from openspan_targets import BASE_PORT, compute_adjacencies  # noqa: E402


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


def screen(ident, name, x, y, w, h):
    # res == size, so scale is exactly 1.0 and a dx of 30 moves 30 desk units.
    # That keeps "did the position stay continuous?" an exact question.
    return {"id": ident, "name": name, "x": x, "y": y, "w": w, "h": h,
            "res_w": w, "res_h": h, "refresh_hz": 60, "rotation": 0}


config = {
    "monitors": [{
        "name": r"\\.\DISPLAY4",
        "x": 0, "y": 0, "w": 1920, "h": 1080,
        "layout_x": 0, "layout_y": 0, "layout_w": 1920, "layout_h": 1080,
        "primary": True,
    }],
    "devices": [
        {"id": "mac", "name": "Managed Mac", "port": BASE_PORT,
         "radio": "", "enabled": True, "clipboard": False, "displays": [
             screen("mac-1", "Mac Display 1", -2000, 0, 1000, 800),
             screen("mac-2", "Mac Display 2", -1000, 0, 1000, 800)]},
        {"id": "tablet", "name": "Tablet", "port": BASE_PORT + 1,
         "radio": "", "enabled": True, "clipboard": False, "displays": [
             screen("tablet-1", "Tablet", -3000, 0, 1000, 800)]},
    ],
}


def fresh(target="mac", display="mac-1", vx=-1010.0, vy=400.0):
    b = openspan_portal.Portal.__new__(openspan_portal.Portal)
    b.cfg = config
    b.links = compute_adjacencies(config)
    b._displays = {(d["id"], s["id"]): s
                   for d in config["devices"] for s in d["displays"]}
    b._monitors = {m["name"]: m for m in config["monitors"]}
    b._target_ports = {d["id"]: d["port"] for d in config["devices"]}
    b._clipboard_devices = {d["id"]: False for d in config["devices"]}
    b.target_ready = {d["id"]: True for d in config["devices"]}
    b.active, b.cur, b.portals = True, None, []
    b.active_target, b.active_display = target, display
    b.vx, b.vy = vx, vy
    b.buttons = b.mods = 0
    b.raw_keys, b.remap, b.overrides = {}, {}, []
    b._chord_until = 0.0
    b.q = queue.Queue()
    b._emit_kbd = lambda: None
    # Every device's pointer position is already known, so no _resync fires.
    b._last_seen = {"mac": ("mac-1", vx, vy), "tablet": ("tablet-1", -2500.0, 400.0)}
    b._device_compensate = b._device_gain = {}
    b._device_accel = b._device_sens = {}
    b._motion = ()
    b._has_momentum = lambda: True
    b._pin_axis = lambda *a, **k: False
    b._side_held, b._button_jumps = 0, False
    b._gentle_logged = 0.0
    return b


# ---- 1. the seam itself ----------------------------------------------------
# mac-1 ends at x=-1000; mac-2 begins there. Push right across it.

b = fresh(vx=-1010.0, vy=400.0)
left = b._route_motion(30, 0)
check("crossing an internal seam stays on the same device",
      left is False and b.active_target == "mac")
check("crossing an internal seam moves to the neighbouring screen",
      b.active_display == "mac-2")
# THE WHOLE POINT. 30 units of motion from -1010 is -980, and nothing may
# rewrite it. Before the fix _place() warped to _position_inside()'s landing.
check("the position is exactly what the motion produced -- no jump",
      abs(b.vx - (-980.0)) < 1e-6)
check("the perpendicular axis is untouched by the seam",
      abs(b.vy - 400.0) < 1e-6)

# Nothing was sent to the device: a warp is HID reports on the wire, and an
# internal seam must cost none.
check("an internal seam puts no motion on the wire", b.q.empty())

# And it must be reversible -- back across the seam returns to where we were.
back = b._route_motion(-30, 0)
check("crossing back returns to the first screen",
      back is False and b.active_display == "mac-1")
check("crossing back returns to the exact starting position",
      abs(b.vx - (-1010.0)) < 1e-6)

# ---- 2. a real device boundary must STILL place -----------------------------
# Regression guard: the fix must not make cross-device handoffs continuous.
# mac-1 begins at x=-2000; the tablet lies to its left.

b = fresh(vx=-1990.0, vy=400.0)
b._route_motion(-30, 0)
check("crossing to another DEVICE still switches device",
      b.active_target == "tablet")
check("a device crossing is still a placement, not a raw continuation",
      abs(b.vx - (-2020.0)) > 1e-6)

# ---- 3. the side button travels between devices, not between screens -------

b = fresh(target="mac", display="mac-1")
found = b._nearest_surface("right", (b.vx, b.vy))
check("the nearest surface to the right is never the same device's own screen",
      found is None or found[2] != "mac")

# From mac-2, LEFT is mac-1 -- the case that used to hop across the seam.
b = fresh(target="mac", display="mac-2", vx=-990.0, vy=400.0)
found = b._nearest_surface("left", (b.vx, b.vy))
check("pushing the button toward an internal seam does not target that seam",
      found is None or found[2] != "mac")
check("it reaches past the device to the one genuinely that way",
      found is not None and found[2] == "tablet")

# The button must still work for what it is FOR.
b = fresh(target="mac", display="mac-2", vx=-990.0, vy=400.0)
found = b._nearest_surface("right", (b.vx, b.vy))
check("the button still finds the PC in the other direction",
      found is not None and found[1] == "local")

# ---- 4. the exclusion is by device, not by screen ---------------------------
# Stated as the invariant rather than a case, so a third Mac screen added
# tomorrow cannot quietly become a jump target again.
import ast  # noqa: E402

src = (pathlib.Path(__file__).parent / "openspan_portal.py").read_text(
    encoding="utf-8")
fn = next(n for n in ast.walk(ast.parse(src))
          if isinstance(n, ast.FunctionDef) and n.name == "_nearest_surface")
body = ast.get_source_segment(src, fn)
check("_nearest_surface excludes the whole active device",
      "device == self.active_target" in body
      and "self.active_display" not in body.split('"""')[-1])
