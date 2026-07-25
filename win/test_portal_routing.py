"""Headless checks for direct target-to-target routing in the portal broker."""

import pathlib
import queue
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import openspan_portal  # noqa: E402
from openspan_targets import compute_adjacencies  # noqa: E402


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


config = {
    "monitors": [{
        "name": r"\\.\DISPLAY4",
        "x": -1920, "y": 0, "w": 1920, "h": 1080,
        "layout_x": -1920, "layout_y": 0,
        "layout_w": 1920, "layout_h": 1080,
        "primary": False,
    }],
    "targets": [
        {
            "id": "ipad", "name": "iPad", "daemon_port": 9955,
            "enabled": True,
            "displays": [{
                "id": "ipad-main", "name": "iPad",
                "x": -3000, "y": 0, "w": 1080, "h": 810,
                "res_w": 1080, "res_h": 810, "rotation": 0,
            }],
        },
        {
            "id": "mac", "name": "Managed Mac", "daemon_port": 9956,
            "enabled": True,
            "displays": [{
                "id": "mac-2", "name": "Mac Display 2",
                "x": -3143, "y": -1737, "w": 3088, "h": 1737,
                "res_w": 2560, "res_h": 1440, "rotation": 0,
            }],
        },
    ],
}

broker = openspan_portal.Portal.__new__(openspan_portal.Portal)
broker.cfg = config
broker.links = compute_adjacencies(config)
broker._displays = {
    (target["id"], display["id"]): display
    for target in config["targets"]
    for display in target["displays"]
}
broker._monitors = {
    monitor["name"]: monitor for monitor in config["monitors"]
}
broker.target_ready = {"ipad": True, "mac": True}
broker.active = True
broker.active_target = "ipad"
broker.active_display = "ipad-main"
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

left_pc = broker._route_motion(0, -20)
check("crossing the shared iPad top edge hands control directly to Mac",
      left_pc is False
      and broker.active_target == "mac"
      and broker.active_display == "mac-2")

broker.active_target = "ipad"
broker.active_display = "ipad-main"
broker.vx = -1925.0
broker.vy = 200.0
exits = []
broker.leave = lambda exit_to=None: exits.append(exit_to)
left_pc = broker._route_motion(20, 0)
check("crossing the iPad right edge still returns to the PC",
      left_pc is True and len(exits) == 1
      and exits[0][0] == -1917)

print("RESULT: ALL PASS")
