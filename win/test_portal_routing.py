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

print("RESULT: ALL PASS")
