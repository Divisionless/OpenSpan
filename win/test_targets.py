"""Headless checks for multi-target display geometry and compatibility."""

import copy
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from openspan_targets import (  # noqa: E402
    MAC_PORT, compute_adjacencies, compute_portals, normalize_config,
    oriented_resolution, rotate_display, set_layout_width,
    snap_rect_to_neighbors, target_by_id, validate_mac_displays,
)


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


live = [{
    "name": r"\\.\DISPLAY1",
    "x": 0, "y": 0, "w": 1920, "h": 1080, "primary": True,
}]
legacy = {
    "monitors": copy.deepcopy(live),
    "ipad": {
        "x": 1920, "y": 0, "w": 1080, "h": 810,
        "res_w": 1080, "res_h": 810,
    },
}
config = normalize_config(legacy, live)
ipad = target_by_id(config, "ipad")
mac = target_by_id(config, "mac")

check("v1 migration preserves the proven iPad rectangle",
      config["ipad"] == legacy["ipad"]
      and ipad["displays"][0]["x"] == 1920
      and ipad["daemon_port"] == 9955)
check("new Mac profile starts with three 4K displays",
      len(mac["displays"]) == 3
      and all(
          (row["res_w"], row["res_h"]) == (3840, 2160)
          for row in mac["displays"])
      and mac["daemon_port"] == MAC_PORT)
check("two default Mac displays are portrait",
      [row["rotation"] for row in mac["displays"]] == [90, 0, 90]
      and oriented_resolution(mac["displays"][0]) == (2160, 3840)
      and oriented_resolution(mac["displays"][1]) == (3840, 2160))

display = copy.deepcopy(mac["displays"][0])
identity = (
    display["res_w"], display["res_h"],
    display["refresh_hz"], display["rotation"])
set_layout_width(display, 700)
check("physical resize preserves resolution/Hz/rotation",
      identity == (
          display["res_w"], display["res_h"],
          display["refresh_hz"], display["rotation"])
      and display["w"] == 700)
rotate_display(display, 0)
check("rotation changes orientation without rewriting entered resolution",
      (display["res_w"], display["res_h"]) == (3840, 2160)
      and oriented_resolution(display) == (3840, 2160))

edited = validate_mac_displays([
    {
        "name": "Portrait left", "res_w": "3840", "res_h": "2160",
        "refresh_hz": "60", "rotation": "90", "physical_width": "24",
    },
    {
        "name": "Center at 2K", "res_w": "2560", "res_h": "1440",
        "refresh_hz": "120", "rotation": "0", "physical_width": "27",
    },
    {
        "name": "Portrait right", "res_w": "3840", "res_h": "2160",
        "refresh_hz": "60", "rotation": "270", "physical_width": "24",
    },
])
check("manual 2K mode, refresh, and both 90° rotations validate",
      edited[1]["res_w"] == 2560
      and edited[1]["res_h"] == 1440
      and edited[1]["refresh_hz"] == 120
      and edited[0]["rotation"] == 90
      and edited[2]["rotation"] == 270)

# Physical layout can differ from Windows pixel geometry. The portal span must
# be mapped back to the real Windows edge so resizing the drawing never moves
# the actual trigger off-screen.
mapped = normalize_config({}, live)
monitor = mapped["monitors"][0]
monitor.update(layout_x=0, layout_y=0, layout_w=800, layout_h=450)
mac = target_by_id(mapped, "mac")
mac["displays"] = [{
    "id": "mac-center", "name": "Center",
    "x": 800, "y": 112, "w": 960, "h": 540,
    "res_w": 3840, "res_h": 2160, "refresh_hz": 60, "rotation": 0,
}]
portals = compute_portals(mapped)
check("physical edge maps back onto the real Windows monitor",
      len(portals) == 1
      and portals[0]["target"] == "mac"
      and portals[0]["axis"] == "x"
      and portals[0]["line"] == 1919
      and 0 <= portals[0]["span"][0] < portals[0]["span"][1] <= 1080)

# One release can satisfy two desk constraints. This reproduces the user's
# arrangement: iPad right touches the PC while iPad top touches Mac Display 2.
snapped = snap_rect_to_neighbors(
    (-3000, 21, 1080, 810),
    [
        (-1920, 0, 1920, 1080),       # PC
        (-3143, -1737, 3088, 1737),  # Mac Display 2
    ])
check("one drag release can snap to both PC and Mac edges",
      snapped == (-3000, 0))

graph_cfg = {
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
upgrade_raw = copy.deepcopy(graph_cfg)
upgrade_raw["targets"][0]["displays"][0]["y"] = 21
upgraded = normalize_config(upgrade_raw, [{
    "name": r"\\.\DISPLAY4",
    "x": -1920, "y": 0, "w": 1920, "h": 1080, "primary": True,
}])
upgraded_ipad = target_by_id(upgraded, "ipad")["displays"][0]
check("pre-adjacency saved layout gains its intended second edge once",
      upgraded_ipad["x"] == -3000 and upgraded_ipad["y"] == 0
      and upgraded.get("links"))

links = compute_adjacencies(graph_cfg)
check("adjacency graph includes iPad to Mac and Mac to iPad",
      any(
          row["source"].get("target") == "ipad"
          and row["destination"].get("target") == "mac"
          and row["side"] == "top"
          for row in links)
      and any(
          row["source"].get("target") == "mac"
          and row["destination"].get("target") == "ipad"
          and row["side"] == "bottom"
          for row in links))
check("adjacency graph preserves the iPad to PC edge too",
      any(
          row["source"].get("target") == "ipad"
          and row["destination"].get("kind") == "local"
          and row["side"] == "right"
          for row in links))

guest = pathlib.Path(__file__).parents[1] / "guest"
service = (guest / "system" / "openspanble-mac.service").read_text(
    encoding="utf-8")
script = (guest / "set-hid-target.sh").read_text(encoding="utf-8")
check("managed Mac has an independent daemon identity and port",
      "OPENSPAN_PORT=9956" in service
      and "OpenSpan Mac Control" in service
      and "openspanble-mac" in script)
check("Mac radio assignment resolves stable controller MAC to current hci",
      "openspan_bt.py resolve --controller" in script
      and "OPENSPAN_ADAPTER=$HCI" in script)

print("RESULT: ALL PASS")
