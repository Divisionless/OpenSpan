"""Headless checks for direct device-to-device routing in the portal broker.

The broker knows nothing about what a device IS -- it routes by device id over
the adjacency graph. So this file builds three arbitrary devices of its own and
checks that a crossing lands on whichever device the layout says is there.
"""

import pathlib
import queue
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import openspan_portal  # noqa: E402
from openspan_targets import (  # noqa: E402
    BASE_PORT, compute_adjacencies, exit_inset,
)


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


def screen(ident, name, x, y, w, h, res_w, res_h):
    return {
        "id": ident, "name": name,
        "x": x, "y": y, "w": w, "h": h,
        "res_w": res_w, "res_h": res_h,
        "refresh_hz": 60, "rotation": 0,
    }


def device(ident, name, port, displays, clipboard=False):
    return {
        "id": ident, "name": name, "port": port,
        "radio": "", "enabled": True, "clipboard": clipboard,
        "displays": displays,
    }


# Desk layout -- three ordinary devices, none of them special:
#
#   device-2 spans the row above; device-3, device-1 and the Windows monitor
#   sit side by side beneath it. So device-1 shares its TOP edge with device-2,
#   its LEFT edge with device-3, and its RIGHT edge with the PC.
config = {
    "monitors": [{
        "name": r"\\.\DISPLAY4",
        "x": -1920, "y": 0, "w": 1920, "h": 1080,
        "layout_x": -1920, "layout_y": 0,
        "layout_w": 1920, "layout_h": 1080,
        "primary": False,
    }],
    "devices": [
        device("device-1", "Tablet", BASE_PORT, [
            screen("device-1-1", "Tablet",
                   -3000, 0, 1080, 810, 1080, 810)]),
        device("device-2", "Studio", BASE_PORT + 1, [
            screen("device-2-1", "Studio Center",
                   -3143, -1737, 3088, 1737, 2560, 1440)]),
        device("device-3", "Console", BASE_PORT + 2, [
            screen("device-3-1", "Console",
                   -4080, 0, 1080, 810, 1080, 810)]),
    ],
}

broker = openspan_portal.Portal.__new__(openspan_portal.Portal)
broker.cfg = config
broker.links = compute_adjacencies(config)
broker._displays = {
    (target["id"], display["id"]): display
    for target in config["devices"]
    for display in target["displays"]
}
broker._monitors = {
    monitor["name"]: monitor for monitor in config["monitors"]
}
broker._target_ports = {
    target["id"]: target["port"] for target in config["devices"]
}
broker._clipboard_devices = {
    target["id"]: target["clipboard"] for target in config["devices"]
}
broker.target_ready = {target["id"]: True for target in config["devices"]}
broker.active = True
broker.cur = None
broker.active_target = "device-1"
broker.active_display = "device-1-1"
broker.vx = -2500.0
broker.vy = 8.0
broker.buttons = 0
broker.mods = 0
broker.raw_keys = {}
broker.remap = {}
broker.overrides = []
broker._chord_until = 0.0
broker.q = queue.Queue()
broker._emit_kbd = lambda: None
# One position per DEVICE. _place() consults this on every handoff and pays for
# any jump in HID reports, so the instance needs its own dict.
broker._last_seen = {}
broker._device_compensate = {}
broker._device_gain = {}
broker._device_accel = {}
broker._device_sens = {}
broker._motion = ()
# These checks are about WHERE a crossing goes, not about whether it is allowed
# to happen. The momentum gate has its own checks at the end of this file.
broker._has_momentum = lambda: True

left_pc = broker._route_motion(0, -80)
check("crossing a shared top edge hands control directly to the device above",
      left_pc is False
      and broker.active_target == "device-2"
      and broker.active_display == "device-2-1")

# Routing is by id over the graph, not by a two-lane special case: come back
# down, then cross the OTHER shared edge onto a third device.
broker.active_target = "device-1"
broker.active_display = "device-1-1"
broker.vx = -2995.0
broker.vy = 400.0
left_pc = broker._route_motion(-80, 0)
check("crossing the other shared edge lands on the third device",
      left_pc is False
      and broker.active_target == "device-3"
      and broker.active_display == "device-3-1")

# A device whose daemon is not subscribed must never be handed the cursor --
# the crossing is refused and the pointer stays where it is.
broker.active_target = "device-1"
broker.active_display = "device-1-1"
broker.vx = -2995.0
broker.vy = 400.0
broker.target_ready["device-3"] = False
left_pc = broker._route_motion(-80, 0)
check("a crossing into a device whose daemon is not ready is refused",
      left_pc is False
      and broker.active_target == "device-1"
      and broker.vx == -3000.0)
broker.target_ready["device-3"] = True

# A drag must never be torn across devices; the handoff re-arms on release.
broker.vx = -2500.0
broker.vy = 8.0
broker.buttons = 1
left_pc = broker._route_motion(0, -80)
check("a held mouse button never tears a drag across devices",
      left_pc is False and broker.active_target == "device-1")
broker.buttons = 0

broker.active_target = "device-1"
broker.active_display = "device-1-1"
broker.vx = -1925.0
broker.vy = 200.0
exits = []
broker.leave = lambda exit_to=None, pin_side=None: (
    exits.append((exit_to, pin_side)))
left_pc = broker._route_motion(80, 0)
# The Windows cursor must land a REAL distance inside the monitor, not 3 px
# from a trigger with a +-1 px tolerance -- that was the Windows half of the
# ping-pong. The inset comes from the same ARRIVE_MARGIN the target side uses.
inset = exit_inset(config["monitors"][0], "x")
check("crossing the edge shared with the PC still returns to the PC",
      left_pc is True and len(exits) == 1
      and exits[0][0][0] == -1920 + inset)
check("and it lands far enough inside not to re-trigger the portal",
      inset >= 8 and exits[0][0][0] > -1920 + 3)
# Handing control back pins the axis you left by -- one hard push in that same
# direction -- so that coordinate becomes a measurement while the other is left
# untouched, preserving where you were ALONG the edge.
check("handing control back names the direction to pin",
      exits[0][1] == "right")


# --- a boundary is crossed on purpose, never by drifting into it ------------
# Reaching an edge slowly is what someone does when working ALONG that edge --
# picking something at the side of a screen. Being thrown onto another machine
# in the middle of that is wrong.
del broker._has_momentum                      # use the real one again
broker.leave = lambda exit_to=None, pin_side=None: exits.append((exit_to, pin_side))

broker.active = True
broker.active_target = "device-1"
broker.active_display = "device-1-1"
broker.vx, broker.vy = -1925.0, 200.0
broker._motion = ()
broker._armed_until = 0.0                     # nothing armed going in
broker._note_motion(4)                        # a careful, delicate move
exits.clear()
left_pc = broker._route_motion(80, 0)
check("a GENTLE arrival at a device edge does not cross",
      left_pc is False and not exits and broker.vx == -1920.0)

broker.vx, broker.vy = -1925.0, 200.0
broker._motion = ()
broker._note_motion(600)                      # a deliberate push
left_pc = broker._route_motion(80, 0)
check("a DELIBERATE push at the same edge does cross",
      left_pc is True and len(exits) == 1)

# ...but a seam between two screens of the SAME device is never gated: the
# target's own pointer crosses it freely, so refusing would put the model
# somewhere the pointer is not.
broker.active_target = "device-2"
broker.active_display = "device-2-1"
broker.vx, broker.vy = -3140.0, -900.0
broker._motion = ()
broker._note_motion(2)                        # barely moving
broker._route_motion(-80, 0)
check("a gentle move across a device's OWN seam still crosses",
      broker.active_target == "device-2")

# --- holding a mouse side button as the explicit "yes" ---------------------
# When this is on, the side button IS the intent, so it REPLACES the momentum
# gate rather than adding to it -- demanding a deliberate shove on top of an
# explicit yes would just be in the way.
broker._cross_button = True
broker._side_held = 0
broker._side_seen = True          # this mouse HAS sent one at some point
broker.active = True
broker.active_target = "device-1"
broker.active_display = "device-1-1"
broker.vx, broker.vy = -1925.0, 200.0
broker._motion = ()
broker._note_motion(600)                 # plenty of momentum...
exits.clear()
left_pc = broker._route_motion(80, 0)
check("with the option on, momentum alone no longer crosses",
      left_pc is False and not exits and broker.vx == -1920.0)

broker.vx, broker.vy = -1925.0, 200.0
broker._side_held = 0x0001               # side button down
broker._motion = ()                      # and barely moving
broker._note_motion(2)
left_pc = broker._route_motion(80, 0)
check("holding the side button crosses, even gently",
      left_pc is True and len(exits) == 1)

# a seam between two screens of the SAME device is never gated -- that is not
# moving between machines
broker._side_held = 0
broker.active_target = "device-2"
broker.active_display = "device-2-1"
broker.vx, broker.vy = -3140.0, -900.0
broker._motion = ()
broker._route_motion(-80, 0)
check("a device's own seam still needs no button",
      broker.active_target == "device-2")
broker._cross_button = False

# ...and even with the OPTION OFF, holding a side button lifts the pressure
# requirement. Those buttons are not used for anything else, so holding one can
# only mean a jump.
broker.active = True
broker.active_target = "device-1"
broker.active_display = "device-1-1"
broker.vx, broker.vy = -1925.0, 200.0
broker._motion = ()
broker._note_motion(2)                   # nowhere near enough on its own
broker._side_held = 0x0002
exits.clear()
left_pc = broker._route_motion(80, 0)
check("a held side button crosses even when the option is off",
      left_pc is True and len(exits) == 1)
broker._side_held = 0

# --- and the option must never lock the pointer in ------------------------
# If the option is on but this mouse has never sent a side button -- plenty
# report theirs as browser back/forward, or not at all -- refusing every
# crossing would trap the pointer with no way to reach the checkbox that turns
# the option off.
broker._cross_button = True
broker._side_held = 0
broker._side_seen = False         # nothing has ever arrived
broker._gentle_logged = 0.0
broker.active = True
broker.active_target = "device-1"
broker.active_display = "device-1-1"
broker.vx, broker.vy = -1925.0, 200.0
broker._motion = ()
broker._note_motion(600)          # a deliberate push
exits.clear()
left_pc = broker._route_motion(80, 0)
check("with no side button ever seen, a push still gets you out",
      left_pc is True and len(exits) == 1)

broker.vx, broker.vy = -1925.0, 200.0
broker._motion = ()
broker._armed_until = 0.0         # the previous push's 350 ms grace has expired
broker._note_motion(3)            # ...but a drift still does not
exits.clear()
left_pc = broker._route_motion(80, 0)
check("and a gentle drift still does not", left_pc is False and not exits)
broker._cross_button = False
broker._side_seen = False

print("RESULT: ALL PASS")
