"""Headless checks for the agnostic N-device display geometry model.

There is no "iPad" and no "managed Mac" in the schema any more -- only N
devices, each one enumerated exactly as the user describes it. So these tests
BUILD the devices they need instead of leaning on anything the config layer
used to invent, and every assertion is expressed against a device this file
created (its own id, its own port, its own displays).
"""

import copy
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from openspan_targets import (  # noqa: E402
    BASE_PORT, CONFIG_VERSION, add_device, allocate_device_id, allocate_port,
    compute_adjacencies, compute_portals, device_by_id, display_by_id,
    new_device, normalize_config, oriented_resolution, refresh_geometry,
    remove_device, rotate_display, set_layout_width, snap_rect_to_neighbors,
    validate_mac_displays,
)


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


def screen(ident, name, x, y, w, h, res_w, res_h, rotation=0, refresh_hz=60):
    """One display rectangle, spelled out in full -- nothing is assumed."""
    return {
        "id": ident, "name": name,
        "x": x, "y": y, "w": w, "h": h,
        "res_w": res_w, "res_h": res_h,
        "refresh_hz": refresh_hz, "rotation": rotation,
    }


def device(ident, name, port, displays, **extra):
    row = {
        "id": ident, "name": name, "port": port,
        "radio": "", "enabled": True, "clipboard": False,
        "displays": displays,
    }
    row.update(extra)
    return row


live = [{
    "name": r"\\.\DISPLAY1",
    "x": 0, "y": 0, "w": 1920, "h": 1080, "primary": True,
}]


# === migration: an existing setup must survive the upgrade untouched ========
legacy = {
    "monitors": copy.deepcopy(live),
    "ipad": {
        "x": 1920, "y": 0, "w": 1080, "h": 810,
        "res_w": 1080, "res_h": 810,
    },
}
migrated = normalize_config(legacy, live)
first = migrated["devices"][0]
first_display = first["displays"][0]
check("v1 migration preserves the proven iPad rectangle as one device",
      len(migrated["devices"]) == 1
      and (first_display["x"], first_display["y"]) == (1920, 0)
      and (first_display["w"], first_display["h"]) == (1080, 810)
      and (first_display["res_w"], first_display["res_h"]) == (1080, 810)
      and first["port"] == BASE_PORT
      and migrated["version"] == CONFIG_VERSION)
check("v1 migration invents no second device and no legacy mirror key",
      "ipad" not in migrated and "targets" not in migrated
      and "kind" not in first)

v2_raw = {
    "version": 2,
    "monitors": copy.deepcopy(live),
    "targets": [
        {
            "id": "ipad", "kind": "ipad", "name": "iPad",
            "daemon_port": 9955, "enabled": True,
            "displays": [screen("ipad-main", "iPad",
                                1920, 0, 1080, 810, 1080, 810)],
        },
        {
            "id": "mac", "kind": "mac", "name": "Managed Mac",
            "daemon_port": 9956, "enabled": True,
            "displays": [
                screen("mac-1", "Mac Display 1",
                       3000, -900, 540, 960, 3840, 2160, rotation=90),
                screen("mac-2", "Mac Display 2",
                       3560, -900, 960, 540, 3840, 2160),
            ],
        },
    ],
    "links": [],   # already had a graph: no one-time snap upgrade
}
v2 = normalize_config(copy.deepcopy(v2_raw), live)
v2_ids = [row["id"] for row in v2["devices"]]
v2_ports = [row["port"] for row in v2["devices"]]
check("v2 targets migrate into devices keeping id, name, port and displays",
      v2_ids == ["ipad", "mac"]
      and v2_ports == [9955, 9956]
      and [row["name"] for row in v2["devices"]] == ["iPad", "Managed Mac"]
      and [len(row["displays"]) for row in v2["devices"]] == [1, 2]
      and [d["id"] for d in v2["devices"][1]["displays"]]
      == ["mac-1", "mac-2"]
      and v2["devices"][1]["displays"][0]["rotation"] == 90)
check("the v2 `kind` field does not survive into the device model",
      all("kind" not in row for row in v2["devices"])
      and all("daemon_port" not in row for row in v2["devices"]))
check("a v2 iPad lane keeps clipboard; every other lane stays opt-out",
      device_by_id(v2, "ipad")["clipboard"] is True
      and device_by_id(v2, "mac")["clipboard"] is False)

colliding = copy.deepcopy(v2_raw)
colliding["targets"][1]["daemon_port"] = 9955   # two lanes, one port
resolved = normalize_config(colliding, live)
check("two devices are never put on one lane: a clashing port is reassigned",
      len({row["port"] for row in resolved["devices"]}) == 2
      and resolved["devices"][0]["port"] == 9955)

empty = normalize_config({}, live)
check("an empty config fabricates nothing -- no device, portal or link",
      empty["devices"] == [] and empty["portals"] == []
      and empty["links"] == [] and len(empty["monitors"]) == 1)


# === a device carries exactly the displays the user enumerated =============
studio_raw = {
    "monitors": copy.deepcopy(live),
    "links": [],
    "devices": [device("device-1", "Studio", BASE_PORT, [
        screen("studio-1", "Portrait left",
               1920, -1000, 540, 960, 3840, 2160, rotation=90),
        screen("studio-2", "Center",
               2460, -600, 960, 540, 3840, 2160),
        screen("studio-3", "Portrait right",
               3420, -1000, 540, 960, 3840, 2160, rotation=90),
    ])],
}
studio = normalize_config(copy.deepcopy(studio_raw), live)["devices"][0]
check("a device carries every display the user enumerated, at its own res",
      len(studio["displays"]) == 3
      and all(
          (row["res_w"], row["res_h"]) == (3840, 2160)
          for row in studio["displays"])
      and studio["port"] == BASE_PORT)
check("per-display rotation survives and reorients the resolution",
      [row["rotation"] for row in studio["displays"]] == [90, 0, 90]
      and oriented_resolution(studio["displays"][0]) == (2160, 3840)
      and oriented_resolution(studio["displays"][1]) == (3840, 2160))

display = copy.deepcopy(studio["displays"][0])
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
mapped = normalize_config({"monitors": copy.deepcopy(live)}, live)
monitor = mapped["monitors"][0]
monitor.update(layout_x=0, layout_y=0, layout_w=800, layout_h=450)
wall = device("device-1", "Wall", BASE_PORT, [
    screen("wall-center", "Center", 800, 112, 960, 540, 3840, 2160)])
mapped["devices"] = [wall]
portals = compute_portals(mapped)
check("physical edge maps back onto the real Windows monitor",
      len(portals) == 1
      and portals[0]["target"] == wall["id"]
      and portals[0]["target_display"] == "wall-center"
      and portals[0]["daemon_port"] == wall["port"]
      and portals[0]["axis"] == "x"
      and portals[0]["line"] == 1919
      and 0 <= portals[0]["span"][0] < portals[0]["span"][1] <= 1080)

# One release can satisfy two desk constraints. This reproduces the user's
# arrangement: a device's right edge touches the PC while its top edge touches
# a SECOND device above it.
snapped = snap_rect_to_neighbors(
    (-3000, 21, 1080, 810),
    [
        (-1920, 0, 1920, 1080),      # PC
        (-3143, -1737, 3088, 1737),  # the other device's display
    ])
check("one drag release can snap to both the PC and another device",
      snapped == (-3000, 0))


# === adjacency: PC<->device and device<->device travel ======================
graph_cfg = {
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
    ],
}
upgrade_raw = copy.deepcopy(graph_cfg)
upgrade_raw["devices"][0]["displays"][0]["y"] = 21
upgraded = normalize_config(upgrade_raw, [{
    "name": r"\\.\DISPLAY4",
    "x": -1920, "y": 0, "w": 1920, "h": 1080, "primary": True,
}])
upgraded_head = device_by_id(upgraded, "device-1")["displays"][0]
check("pre-adjacency saved layout gains its intended second edge once",
      upgraded_head["x"] == -3000 and upgraded_head["y"] == 0
      and upgraded.get("links"))

links = compute_adjacencies(graph_cfg)
check("adjacency graph links device to device in both directions",
      any(
          row["source"].get("target") == "device-1"
          and row["destination"].get("target") == "device-2"
          and row["side"] == "top"
          for row in links)
      and any(
          row["source"].get("target") == "device-2"
          and row["destination"].get("target") == "device-1"
          and row["side"] == "bottom"
          for row in links))
check("adjacency graph preserves the device to PC edge too",
      any(
          row["source"].get("target") == "device-1"
          and row["destination"].get("kind") == "local"
          and row["side"] == "right"
          for row in links))


# === N devices: ports, ids, placement, removal, rename =====================
fleet = normalize_config({"monitors": copy.deepcopy(live)}, live)
made = [add_device(fleet, f"Device {index}", fleet["monitors"])
        for index in range(1, 6)]
check("N devices allocate unique consecutive ports from BASE_PORT",
      [row["port"] for row in made]
      == list(range(BASE_PORT, BASE_PORT + 5))
      and len({row["id"] for row in made}) == 5
      and len(fleet["devices"]) == 5)
check("a new device is a blank slate: one display, no radio, no clipboard",
      all(len(row["displays"]) == 1 and row["radio"] == ""
          and row["clipboard"] is False and row["enabled"] is True
          for row in made))
check("new devices are placed clear of everything already on the desk",
      len({(row["displays"][0]["x"], row["displays"][0]["y"])
           for row in made}) == 5
      and made[0]["displays"][0]["x"] == 1920)
check("allocate_port skips ports claimed outside the config",
      allocate_port(fleet, taken={BASE_PORT + 5}) == BASE_PORT + 6
      and allocate_device_id(fleet) == "device-6")
check("display_by_id resolves a display through its owning device",
      display_by_id(fleet, made[2]["id"], made[2]["displays"][0]["id"])
      is made[2]["displays"][0]
      and display_by_id(fleet, made[0]["id"], "nope") is None)

check("the first device on the desk owns a portal into the PC monitor",
      any(row["target"] == made[0]["id"] for row in fleet["portals"]))
victim = made[2]
victim_port = victim["port"]
removed = remove_device(fleet, victim["id"])
check("removing a device drops it and its geometry, leaving the rest",
      removed is True
      and device_by_id(fleet, victim["id"]) is None
      and len(fleet["devices"]) == 4
      and not any(row["target"] == victim["id"] for row in fleet["portals"])
      and not any(
          victim["id"] in (row["source"].get("target"),
                           row["destination"].get("target"))
          for row in fleet["links"])
      and any(row["target"] == made[0]["id"] for row in fleet["portals"]))
check("removing an unknown device changes nothing",
      remove_device(fleet, "device-does-not-exist") is False
      and len(fleet["devices"]) == 4)
check("a removed device's port returns to the pool",
      new_device(fleet)["port"] == victim_port)

keeper = fleet["devices"][0]
keeper_id = keeper["id"]
keeper_port = keeper["port"]
keeper_layout = copy.deepcopy(keeper["displays"])
keeper["name"] = "Renamed on a whim"
refresh_geometry(fleet)
reloaded = normalize_config(copy.deepcopy(fleet), live)
reloaded_keeper = device_by_id(reloaded, keeper_id)
check("renaming a device never rewrites its identity, port or layout",
      device_by_id(fleet, keeper_id) is keeper
      and keeper["port"] == keeper_port
      and keeper["displays"] == keeper_layout
      and reloaded_keeper is not None
      and reloaded_keeper["name"] == "Renamed on a whim"
      and reloaded_keeper["port"] == keeper_port
      and reloaded_keeper["displays"] == keeper_layout
      and [row["id"] for row in reloaded["devices"]]
      == [row["id"] for row in fleet["devices"]])
check("the renamed device's portal carries the new label, same lane",
      any(row["target"] == keeper_id
          and row["target_name"] == "Renamed on a whim"
          and row["daemon_port"] == keeper_port
          for row in fleet["portals"]))


# === the guest side of a second lane =======================================
guest = pathlib.Path(__file__).parents[1] / "guest"
service = (guest / "system" / "openspanble-mac.service").read_text(
    encoding="utf-8")
script = (guest / "set-hid-target.sh").read_text(encoding="utf-8")
check("a second device lane has an independent daemon identity and port",
      f"OPENSPAN_PORT={BASE_PORT + 1}" in service
      and "OPENSPAN_DEVICE_NAME=" in service
      and "openspanble-mac" in script)
check("radio assignment resolves a stable controller MAC to the current hci",
      "openspan_bt.py resolve --controller" in script
      and "OPENSPAN_ADAPTER=$HCI" in script)

template = (guest / "system" / "openspanble@.service").read_text(
    encoding="utf-8")
generic = (guest / "set-hid-device.sh").read_text(encoding="utf-8")
check("the guest lane is a template: no port, radio or name is hardcoded",
      "OPENSPAN_DEVICE_ID=%i" in template
      and "Environment=OPENSPAN_PORT=" not in template
      and "Environment=OPENSPAN_ADAPTER=" not in template
      and "openspanble@%i.service.d" in template)
check("a lane takes its id, radio, port and name from the caller",
      "DEVICE_ID CONTROLLER_MAC PORT NAME" in generic
      and "--remove" in generic)

print("RESULT: ALL PASS")
