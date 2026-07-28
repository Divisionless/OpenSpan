"""Target/display geometry shared by the OpenSpan UI and input portal.

Version 1 of OpenSpan stored one draggable iPad rectangle in Windows pixel
coordinates.  Version 2 keeps that shape readable, but separates four things
that must not be conflated for a multi-display target:

* pixel resolution (editable)
* rotation (editable)
* refresh rate (editable metadata)
* desk/layout size and position (drag/resize geometry)

The layout rectangle is intentionally independent from resolution.  Resizing a
screen on the arrangement canvas therefore models its physical size without
silently changing the target's display mode.
"""

from __future__ import annotations

import copy
import json


CONFIG_VERSION = 3
# Devices are AGNOSTIC. There is no "iPad" and no "managed Mac" in this model --
# only N devices, each enumerated exactly as the user describes it, each owning
# its own lane: a radio (controller MAC), a daemon port, and its own displays.
# Ports are allocated from BASE_PORT upward; nothing is reserved for a "kind".
BASE_PORT = 9955
MIN_LAYOUT_SIZE = 120
# Desk units per PHYSICAL INCH. The desk layout exists only to describe how
# screens sit next to each other, and pointer travel is independent of it
# (traverse = resolution / gain -- the rectangle cancels), so sizing it from
# real inches costs nothing and makes the arrangement actually truthful. A
# 32" screen then really is ~1.9x the width of a 17" one, instead of the user
# having to inflate rectangles to fight a monitor whose size was pinned to its
# pixel count.
DESK_UNITS_PER_INCH = 100.0
# How far INSIDE a surface a crossing lands, in desk units. One constant governs
# both halves of every edge: the target side (openspan_portal._entry_point /
# _position_inside) and the Windows side (the exit_to points below). Landing
# 3px from a trigger with a +-1px tolerance is a 2px re-entry -- the arrival
# margin, not an accumulator, is what stops a crossing from bouncing back.
ARRIVE_MARGIN = 40.0


def physical_size(diagonal_in, res_w, res_h, rotation=0):
    """Desk (w, h) for a screen of this DIAGONAL with this aspect ratio.

    Aspect comes from the pixel resolution, rotation included, so a portrait
    32" panel is tall and narrow while a landscape one is wide and short --
    both correctly ~1.9x a 17" monitor."""
    width_px = max(1.0, float(res_w))
    height_px = max(1.0, float(res_h))
    if int(rotation) % 180 == 90:
        width_px, height_px = height_px, width_px
    hyp = (width_px ** 2 + height_px ** 2) ** 0.5
    diagonal_in = max(1.0, float(diagonal_in))
    w_in = diagonal_in * width_px / hyp
    h_in = diagonal_in * height_px / hyp
    return (max(MIN_LAYOUT_SIZE, int(round(w_in * DESK_UNITS_PER_INCH))),
            max(MIN_LAYOUT_SIZE, int(round(h_in * DESK_UNITS_PER_INCH))))


def oriented_resolution(display):
    """Return the effective width/height after the configured rotation."""
    width = max(320, int(display.get("res_w", 1920)))
    height = max(240, int(display.get("res_h", 1080)))
    rotation = int(display.get("rotation", 0)) % 360
    if rotation in (90, 270):
        return height, width
    return width, height


def set_layout_width(display, width):
    """Resize only the desk rectangle, preserving pixel resolution/aspect."""
    width = max(MIN_LAYOUT_SIZE, int(round(float(width))))
    res_w, res_h = oriented_resolution(display)
    display["w"] = width
    display["h"] = max(
        MIN_LAYOUT_SIZE, int(round(width * res_h / max(1, res_w))))
    return display


def rotate_display(display, rotation=None):
    """Rotate a display while preserving its physical diagonal/layout area."""
    old_w = max(MIN_LAYOUT_SIZE, int(display.get("w", MIN_LAYOUT_SIZE)))
    old_h = max(MIN_LAYOUT_SIZE, int(display.get("h", MIN_LAYOUT_SIZE)))
    if rotation is None:
        rotation = (int(display.get("rotation", 0)) + 90) % 360
    display["rotation"] = int(rotation) % 360
    # Swap the physical rectangle for a quarter turn. Pixel resolution remains
    # exactly as entered (e.g. 3840x2160 + 90° renders as 2160x3840).
    if display["rotation"] in (90, 270):
        display["w"], display["h"] = min(old_w, old_h), max(old_w, old_h)
    else:
        display["w"], display["h"] = max(old_w, old_h), min(old_w, old_h)
    return display


def allocate_port(config, taken=None):
    """First free daemon port at or above BASE_PORT. Ports are handed out in
    order of creation -- no port is reserved for any particular sort of device."""
    used = set(taken or ())
    used.update(
        int(device.get("port", 0)) for device in config.get("devices", []))
    port = BASE_PORT
    while port in used:
        port += 1
    return port


def allocate_device_id(config, taken=None):
    """Opaque, stable device id. Deliberately meaningless -- the human-readable
    label lives in `name`, so renaming a device never rewrites its identity
    (and therefore never orphans its bonds, port, or saved geometry)."""
    used = {str(device.get("id")) for device in config.get("devices", [])}
    used.update(str(t) for t in (taken or ()))
    index = 1
    while f"device-{index}" in used:
        index += 1
    return f"device-{index}"


def new_device(config, name=None, live_monitors=None, displays=None):
    """Build a device with ONE display, placed to the right of everything that
    already exists so it never lands on top of another surface (or on the
    taskbar edge). Every value here is a neutral starting point the user edits --
    no resolution, count, or rotation is assumed from anyone's hardware."""
    device_id = allocate_device_id(config)
    surfaces = layout_surfaces(config)
    if surfaces:
        # layout_surfaces returns {"rect": [x, y, w, h]}
        right = max(int(s["rect"][0]) + int(s["rect"][2]) for s in surfaces)
        top = min(int(s["rect"][1]) for s in surfaces)
    elif live_monitors:
        right = max(int(m.get("layout_x", m["x"]))
                    + int(m.get("layout_w", m["w"])) for m in live_monitors)
        top = min(int(m.get("layout_y", m["y"])) for m in live_monitors)
    else:
        right, top = 0, 0
    if displays is None:
        displays = [_normalize_display(
            {"x": right, "y": top, "res_w": 1920, "res_h": 1080},
            f"{device_id}-1", "Display 1")]
    return {
        "id": device_id,
        "name": str(name or "New device"),
        "port": allocate_port(config),
        "radio": "",          # controller MAC; "" = not assigned yet
        "enabled": True,
        "clipboard": False,   # opt-in; needs helper shortcuts on the device
        "scroll_invert": False,
        "pointer_gain": 1.0,    # points per HID unit (1.0 = accel off)
        "pointer_accel": 0.0,   # our own acceleration; 0.0 = linear
        "sensitivity": 1.0,     # per-device feel multiplier
        "compensate_target_accel": False,
        "modifier_remap": None,  # None = inherit the global keymap

        "displays": displays,
    }


def add_device(config, name=None, live_monitors=None):
    device = new_device(config, name=name, live_monitors=live_monitors)
    config.setdefault("devices", []).append(device)
    refresh_geometry(config)
    return device


def remove_device(config, device_id):
    before = len(config.get("devices", []))
    config["devices"] = [
        device for device in config.get("devices", [])
        if device.get("id") != device_id]
    refresh_geometry(config)
    return len(config.get("devices", [])) != before


def refresh_geometry(config):
    """Recompute the derived geometry (entrance points + adjacency graph)."""
    config["portals"] = compute_portals(config)
    config["links"] = compute_adjacencies(config)
    return config


def _normalize_monitor(live, saved):
    row = dict(live)
    saved = saved or {}
    row["layout_x"] = int(saved.get("layout_x", live["x"]))
    row["layout_y"] = int(saved.get("layout_y", live["y"]))
    row["layout_w"] = max(
        MIN_LAYOUT_SIZE, int(saved.get("layout_w", live["w"])))
    row["layout_h"] = max(
        MIN_LAYOUT_SIZE, int(saved.get("layout_h", live["h"])))
    row["refresh_hz"] = float(saved.get("refresh_hz", 60))
    diagonal = saved.get("diagonal_in")
    if diagonal:
        # A local monitor's desk size was its PIXEL count, which made a 17"
        # 1080p panel 1920 units wide and forced every real screen to be
        # inflated to match. Physical inches make them comparable.
        row["diagonal_in"] = float(diagonal)
        row["layout_w"], row["layout_h"] = physical_size(
            diagonal, live["w"], live["h"], 0)
    return row


def _normalize_display(display, fallback_id, fallback_name):
    row = dict(display or {})
    row["id"] = str(row.get("id") or fallback_id)
    row["name"] = str(row.get("name") or fallback_name)
    row["x"] = int(row.get("x", 0))
    row["y"] = int(row.get("y", 0))
    row["res_w"] = max(320, int(row.get("res_w", 1920)))
    row["res_h"] = max(240, int(row.get("res_h", 1080)))
    row["refresh_hz"] = max(24.0, float(row.get("refresh_hz", 60)))
    row["rotation"] = int(row.get("rotation", 0)) % 360
    if row["rotation"] not in (0, 90, 180, 270):
        row["rotation"] = 0
    diagonal = row.get("diagonal_in")
    if diagonal:
        row["diagonal_in"] = float(diagonal)
        row["w"], row["h"] = physical_size(
            diagonal, row["res_w"], row["res_h"], row["rotation"])
        return row
    if "w" not in row or "h" not in row:
        effective_w, effective_h = oriented_resolution(row)
        row["w"] = max(MIN_LAYOUT_SIZE, int(effective_w / 4))
        row["h"] = max(MIN_LAYOUT_SIZE, int(effective_h / 4))
    else:
        row["w"] = max(MIN_LAYOUT_SIZE, int(row["w"]))
        row["h"] = max(MIN_LAYOUT_SIZE, int(row["h"]))
    return row


def dedupe_display_ids(config):
    """No two devices may name a screen the same thing.

    A screen's id is only ever meaningful inside its own device, so a clash was
    survivable -- until something keyed by the id alone met it. It happened for
    real: the display editor minted "mac-N" whatever device was open, so a third
    device's second screen collided with the Managed Mac's. Rename the intruder
    rather than leave a config that reads as if one screen belongs to two
    machines. Geometry is recomputed from the rectangles, so nothing refers to
    the old name afterwards."""
    taken = set()
    renamed = []
    for device in config.get("devices", []):
        device_id = str(device.get("id") or "device")
        for display in device.get("displays", []):
            ident = str(display.get("id") or "")
            if ident and ident not in taken:
                taken.add(ident)
                continue
            probe = 1
            while f"{device_id}-{probe}" in taken:
                probe += 1
            fresh = f"{device_id}-{probe}"
            renamed.append((ident or "(blank)", fresh))
            display["id"] = fresh
            taken.add(fresh)
    return renamed


def normalize_config(raw, live_monitors):
    """Return a complete v2 config while preserving every v1 iPad setting."""
    if not live_monitors:
        raise ValueError("at least one Windows monitor is required")
    raw = raw if isinstance(raw, dict) else {}
    saved_monitors = {
        str(row.get("name", "")): row
        for row in raw.get("monitors", [])
        if isinstance(row, dict)
    }
    monitors = [
        _normalize_monitor(row, saved_monitors.get(str(row.get("name", ""))))
        for row in live_monitors
    ]

    # v3 reads `devices`. v2 configs carried `targets` (with a hardcoded
    # ipad/mac kind) and v1 carried a single bare `ipad` rectangle -- both are
    # migrated in as ordinary devices, keeping their id, name, port and layout
    # so an existing setup survives the upgrade untouched.
    raw_devices = raw.get("devices")
    if not isinstance(raw_devices, list) or not raw_devices:
        raw_devices = []
        for legacy in raw.get("targets", []):
            if isinstance(legacy, dict):
                migrated = dict(legacy)
                if migrated.get("kind") == "ipad" or migrated.get("id") == "ipad":
                    migrated.setdefault("clipboard", True)
                migrated.pop("kind", None)
                if "port" not in migrated and "daemon_port" in migrated:
                    migrated["port"] = migrated.pop("daemon_port")
                raw_devices.append(migrated)
        if not raw_devices and isinstance(raw.get("ipad"), dict):
            raw_devices = [{
                "id": "device-1",
                "name": "iPad",
                "port": BASE_PORT,
                # a v1 install had the clipboard bridge working -- carry the
                # capability forward, exactly as the v2 path does, so upgrading
                # never silently drops the feature
                "clipboard": True,
                "displays": [dict(raw["ipad"], id="device-1-1",
                                  name="Display 1")],
            }]

    devices = []
    used_ports = set()
    for index, target in enumerate(raw_devices):
        if not isinstance(target, dict):
            continue
        target_id = str(target.get("id") or f"device-{index + 1}")
        displays = [
            _normalize_display(
                row, f"{target_id}-{i + 1}", f"Display {i + 1}")
            for i, row in enumerate(target.get("displays", []))
            if isinstance(row, dict)
        ]
        if not displays:
            continue
        # Display ids MUST be unique within a target: the portal keys its
        # geometry on (target, display_id), so a duplicate silently drops one
        # panel -- input routed against the wrong rect/scale -- and the canvas
        # can no longer drag it. Ids arrive from disk unchecked, so enforce it
        # here rather than trusting the writer.
        seen_ids = set()
        for slot, row in enumerate(displays):
            if row["id"] in seen_ids:
                probe = slot + 1
                while f"{target_id}-{probe}" in seen_ids:
                    probe += 1
                row["id"] = f"{target_id}-{probe}"
            seen_ids.add(row["id"])
        # Every device owns a UNIQUE port: it is its own entrance point (its own
        # daemon, radio, advertisement and bonds). A collision would put two
        # devices on one lane, so re-allocate rather than trust the file.
        port = int(target.get("port", target.get("daemon_port", 0)) or 0)
        if port <= 0 or port in used_ports:
            port = allocate_port({"devices": devices}, taken=used_ports)
        used_ports.add(port)
        devices.append({
            "id": target_id,
            "name": str(target.get("name") or target_id),
            "port": port,
            "radio": str(target.get("radio", "") or ""),
            "enabled": bool(target.get("enabled", True)),
            # Capability, not a device type: the clipboard bridge needs helper
            # shortcuts installed ON the device, so it is opt-in per device.
            "clipboard": bool(target.get("clipboard", False)),
            # INPUT settings are per device: a Mac wants physical Alt to arrive
            # as Option, an iPad wants it as Command, and scroll direction is a
            # per-device preference. {} means "send modifiers through as-is".
            "scroll_invert": bool(target.get("scroll_invert", False)),
            # Target POINTS produced per HID unit. Exactly 1.0 once the target's
            # pointer acceleration is OFF; below 1.0 biases the real cursor to
            # reach an edge first, where the clamp re-syncs both cursors.
            "pointer_gain": max(0.05, min(8.0, float(
                target.get("pointer_gain", 1.0) or 1.0))),
            # Acceleration applied by US, on Windows. 0.0 = linear. Doing it
            # here (rather than letting the target OS do it) is what keeps the
            # virtual cursor exact: the same curve drives wire and model.
            "pointer_accel": max(0.0, min(4.0, float(
                target.get("pointer_accel", 0.0) or 0.0))),
            # Per-device FEEL knob. Devices differ (a Mac and an iPad apply
            # different internal scaling we cannot read), and the target's own
            # settings must stay untouched so it remains pleasant to use
            # standalone -- so the correction lives here instead.
            "sensitivity": max(0.1, min(4.0, float(
                target.get("sensitivity", 1.0) or 1.0))),
            # Invert the TARGET's own acceleration curve instead of asking the
            # user to switch it off. Only meaningful where that curve is known.
            "compensate_target_accel": bool(
                target.get("compensate_target_accel", False)),
            # None = inherit the global keymap (today's behaviour); a dict --
            # even an empty one -- is an explicit per-device override, which is
            # how a Mac gets physical Alt delivered as Option instead of Cmd.
            "modifier_remap": (
                dict(target["modifier_remap"])
                if isinstance(target.get("modifier_remap"), dict) else None),
            "displays": displays,
        })

    # NOTHING is fabricated. A config with no devices stays empty and the user
    # adds what they actually own -- no assumed iPad, no assumed Mac, no
    # assumed display count, resolution or rotation.
    result = {
        "version": CONFIG_VERSION,
        "monitors": monitors,
        "devices": devices,
    }
    # One-time upgrade for layouts saved before the adjacency graph existed:
    # re-run each device's first display through the two-axis snap so a
    # near-touching second edge becomes a real connection. Applies to every
    # device equally -- no device is privileged.
    if "links" not in raw:
        for device in devices:
            if not device.get("displays"):
                continue
            head = device["displays"][0]
            rect = (head["x"], head["y"], head["w"], head["h"])
            neighbors = [_monitor_layout(monitor) for monitor in monitors]
            for other in devices:
                for display in other.get("displays", []):
                    if display is head:
                        continue
                    neighbors.append((display["x"], display["y"],
                                      display["w"], display["h"]))
            head["x"], head["y"] = snap_rect_to_neighbors(rect, neighbors)
    # A screen's id is only meaningful inside its own device, but a clash still
    # reads as if one screen belongs to two machines -- and the editor really
    # did mint one. Heal it on load, before any geometry is derived from it.
    dedupe_display_ids(result)
    return refresh_geometry(result)


def device_by_id(config, device_id):
    return next(
        (device for device in config.get("devices", [])
         if device.get("id") == device_id),
        None)


def display_by_id(config, device_id, display_id):
    device = device_by_id(config, device_id)
    if not device:
        return None
    return next(
        (display for display in device.get("displays", [])
         if display.get("id") == display_id),
        None)


def _monitor_layout(monitor):
    return (
        int(monitor.get("layout_x", monitor["x"])),
        int(monitor.get("layout_y", monitor["y"])),
        int(monitor.get("layout_w", monitor["w"])),
        int(monitor.get("layout_h", monitor["h"])),
    )


def _mapped_span(lo, hi, layout_origin, layout_size, actual_origin, actual_size):
    start = actual_origin + (
        (lo - layout_origin) / max(1.0, float(layout_size))) * actual_size
    end = actual_origin + (
        (hi - layout_origin) / max(1.0, float(layout_size))) * actual_size
    return int(round(min(start, end))), int(round(max(start, end)))


def layout_surfaces(config):
    """Return every local/managed display in the shared desk coordinate space."""
    rows = []
    for monitor in config.get("monitors", []):
        x, y, width, height = _monitor_layout(monitor)
        rows.append({
            "kind": "local",
            "monitor": monitor["name"],
            "target": None,
            "display": None,
            "name": monitor["name"],
            "rect": [x, y, width, height],
        })
    for target in config.get("devices", []):
        if not target.get("enabled", True):
            continue
        for display in target.get("displays", []):
            rows.append({
                "kind": "target",
                "monitor": None,
                "target": target["id"],
                "display": display["id"],
                "name": f"{target['name']} · {display['name']}",
                "rect": [
                    int(display["x"]), int(display["y"]),
                    int(display["w"]), int(display["h"]),
                ],
            })
    return rows


def _surface_ref(surface):
    return {
        "kind": surface["kind"],
        "monitor": surface.get("monitor"),
        "target": surface.get("target"),
        "display": surface.get("display"),
        "name": surface.get("name", ""),
    }


def compute_adjacencies(config, tolerance=2, min_overlap=20):
    """Build a directed edge graph for PC↔target and target↔target travel."""
    surfaces = layout_surfaces(config)
    links = []

    def add(source, destination, side, to_side, axis, line, span):
        links.append({
            "source": _surface_ref(source),
            "destination": _surface_ref(destination),
            "side": side,
            "to_side": to_side,
            "axis": axis,
            "line": int(line),
            "span": [int(span[0]), int(span[1])],
        })

    for index, first in enumerate(surfaces):
        ax, ay, aw, ah = first["rect"]
        for second in surfaces[index + 1:]:
            # Windows already owns movement between its own monitors.
            if first["kind"] == second["kind"] == "local":
                continue
            bx, by, bw, bh = second["rect"]
            vlo, vhi = max(ay, by), min(ay + ah, by + bh)
            hlo, hhi = max(ax, bx), min(ax + aw, bx + bw)
            if vhi - vlo > min_overlap:
                if abs((ax + aw) - bx) <= tolerance:
                    add(first, second, "right", "left", "x",
                        ax + aw, (vlo, vhi))
                    add(second, first, "left", "right", "x",
                        bx, (vlo, vhi))
                if abs((bx + bw) - ax) <= tolerance:
                    add(first, second, "left", "right", "x",
                        ax, (vlo, vhi))
                    add(second, first, "right", "left", "x",
                        bx + bw, (vlo, vhi))
            if hhi - hlo > min_overlap:
                if abs((ay + ah) - by) <= tolerance:
                    add(first, second, "bottom", "top", "y",
                        ay + ah, (hlo, hhi))
                    add(second, first, "top", "bottom", "y",
                        by, (hlo, hhi))
                if abs((by + bh) - ay) <= tolerance:
                    add(first, second, "top", "bottom", "y",
                        ay, (hlo, hhi))
                    add(second, first, "bottom", "top", "y",
                        by + bh, (hlo, hhi))
    return links


def snap_rect_to_neighbors(rect, neighbors, threshold=None):
    """Snap one rectangle on both axes so it can touch two neighbors at once."""
    x, y, width, height = (int(value) for value in rect)
    others = [tuple(int(value) for value in row) for row in neighbors]
    threshold = (
        max(36, min(width, height) * 0.14)
        if threshold is None else float(threshold))
    x_candidates = []
    y_candidates = []

    def align(pos, size, other_pos, other_size):
        """Level this screen against its neighbour on the PERPENDICULAR axis.

        Edge contact alone is not enough: touching a neighbour while sitting a
        few units high is exactly what makes lining screens up fiddly. Snap to
        the three alignments people actually want -- tops level, bottoms level,
        centres level -- and only fall back to clamping into a legal overlap
        when none of them is close.
        """
        targets = (
            other_pos,                                       # tops level
            other_pos + other_size - size,                    # bottoms level
            other_pos + (other_size - size) / 2.0,            # centres level
        )
        best = min(targets, key=lambda t: abs(pos - t))
        # Alignment is stickier than edge contact: getting screens LEVEL is the
        # fiddly part, and being a little generous here is what makes it feel
        # intuitive rather than fought-for.
        if abs(pos - best) < threshold * 2.0:
            return int(round(best))
        return max(
            other_pos - size + 24,
            min(pos, other_pos + other_size - 24))

    for ox, oy, ow, oh in others:
        for distance, nx in (
                (abs(x - (ox + ow)), ox + ow),
                (abs((x + width) - ox), ox - width)):
            if distance < threshold:
                x_candidates.append({
                    "distance": distance, "x": nx,
                    "y": align(y, height, oy, oh),
                    "other": (ox, oy, ow, oh),
                })
        for distance, ny in (
                (abs(y - (oy + oh)), oy + oh),
                (abs((y + height) - oy), oy - height)):
            if distance < threshold:
                y_candidates.append({
                    "distance": distance, "y": ny,
                    "x": align(x, width, ox, ow),
                    "other": (ox, oy, ow, oh),
                })

    def overlap(lo1, hi1, lo2, hi2):
        return min(hi1, hi2) - max(lo1, lo2)

    # Prefer a valid two-edge solution over any single snap. This is what lets
    # an iPad touch a PC on its right while also touching a Mac above it.
    pairs = []
    for xc in x_candidates:
        for yc in y_candidates:
            nx, ny = xc["x"], yc["y"]
            ox, oy, ow, oh = xc["other"]
            x_ok = overlap(ny, ny + height, oy, oy + oh) > 20
            ox, oy, ow, oh = yc["other"]
            y_ok = overlap(nx, nx + width, ox, ox + ow) > 20
            if x_ok and y_ok:
                pairs.append((
                    xc["distance"] + yc["distance"],
                    abs(nx - x) + abs(ny - y), nx, ny))
    if pairs:
        _distance, _movement, nx, ny = min(pairs)
        return int(nx), int(ny)

    singles = [
        (row["distance"], abs(row["x"] - x) + abs(row["y"] - y),
         row["x"], row["y"])
        for row in x_candidates
    ] + [
        (row["distance"], abs(row["x"] - x) + abs(row["y"] - y),
         row["x"], row["y"])
        for row in y_candidates
    ]
    if singles:
        _distance, _movement, nx, ny = min(singles)
        return int(nx), int(ny)
    return x, y


def exit_inset(monitor, axis):
    """ARRIVE_MARGIN, expressed in this monitor's own Windows pixels.

    The desk layout and the Windows desktop are different unit systems, and the
    ratio differs per monitor, so a hardcoded pixel inset lands at a different
    real distance on every screen -- on a dense one it landed inside the +-1px
    trigger tolerance, which is the Windows half of the ping-pong."""
    mx, my, mw, mh = _monitor_layout(monitor)
    if axis == "x":
        return max(8, int(round(
            ARRIVE_MARGIN * float(monitor["w"]) / max(1.0, float(mw)))))
    return max(8, int(round(
        ARRIVE_MARGIN * float(monitor["h"]) / max(1.0, float(mh)))))


def portal_signature(config):
    """Everything the input portal actually reads, as a stable string.

    The portal loads geometry and per-device input settings ONCE, at process
    start, so the app must restart it whenever any of them change. The previous
    detector compared only the computed portals and links -- which are blind to
    a screen's RESOLUTION and ROTATION, the two numbers that set how far the
    pointer travels per HID unit. Change a screen from 2560x1440 to 3840x2160,
    or rotate it, and the adjacency graph comes out identical: no restart, and
    the running portal goes on measuring the screen that used to be there. An
    edge then fires part way across the new one.

    So the signature is taken over the fields themselves, not over a projection
    of them. Cosmetic edits (renames, refresh rate) are still free."""
    monitors = [
        [m.get("name"), m.get("x"), m.get("y"), m.get("w"), m.get("h"),
         m.get("layout_x"), m.get("layout_y"), m.get("layout_w"),
         m.get("layout_h"), bool(m.get("primary"))]
        for m in config.get("monitors", [])
    ]
    devices = []
    for device in config.get("devices", []):
        if not device.get("enabled", True):
            continue
        devices.append([
            device.get("id"), device.get("port"),
            bool(device.get("clipboard")), bool(device.get("scroll_invert")),
            device.get("pointer_gain"), device.get("pointer_accel"),
            device.get("sensitivity"),
            bool(device.get("compensate_target_accel")),
            device.get("modifier_remap"),
            [[d.get("id"), d.get("x"), d.get("y"), d.get("w"), d.get("h"),
              d.get("res_w"), d.get("res_h"), d.get("rotation")]
             for d in device.get("displays", [])],
        ])
    # The computed portals and links are DERIVED from exactly the rectangles
    # above, so including them would add nothing but the display names they
    # carry -- and a rename would then drop the portal's hooks mid-use.
    return json.dumps(
        [monitors, devices, bool(config.get("cross_requires_side_button"))],
        sort_keys=True, default=str)


def compute_portals(config):
    """Compute real Windows edge triggers from the independent desk layout."""
    out = []
    for target in config.get("devices", []):
        if not target.get("enabled", True):
            continue
        for display in target.get("displays", []):
            tx, ty = int(display["x"]), int(display["y"])
            tw, th = int(display["w"]), int(display["h"])
            for monitor in config.get("monitors", []):
                mx, my, mw, mh = _monitor_layout(monitor)
                # target left touches Windows monitor right
                if abs(tx - (mx + mw)) <= 2:
                    lo, hi = max(ty, my), min(ty + th, my + mh)
                    if hi - lo > 20:
                        span = _mapped_span(
                            lo, hi, my, mh, monitor["y"], monitor["h"])
                        out.append(_portal(
                            target, display, monitor, "target-left", "x",
                            monitor["x"] + monitor["w"] - 1, span, +1,
                            (monitor["x"] + monitor["w"]
                             - exit_inset(monitor, "x"), None)))
                # target right touches Windows monitor left
                if abs((tx + tw) - mx) <= 2:
                    lo, hi = max(ty, my), min(ty + th, my + mh)
                    if hi - lo > 20:
                        span = _mapped_span(
                            lo, hi, my, mh, monitor["y"], monitor["h"])
                        out.append(_portal(
                            target, display, monitor, "target-right", "x",
                            monitor["x"], span, -1,
                            (monitor["x"] + exit_inset(monitor, "x"), None)))
                # target top touches Windows monitor bottom
                if abs(ty - (my + mh)) <= 2:
                    lo, hi = max(tx, mx), min(tx + tw, mx + mw)
                    if hi - lo > 20:
                        span = _mapped_span(
                            lo, hi, mx, mw, monitor["x"], monitor["w"])
                        out.append(_portal(
                            target, display, monitor, "target-top", "y",
                            monitor["y"] + monitor["h"] - 1, span, +1,
                            (None, monitor["y"] + monitor["h"]
                             - exit_inset(monitor, "y"))))
                # target bottom touches Windows monitor top
                if abs((ty + th) - my) <= 2:
                    lo, hi = max(tx, mx), min(tx + tw, mx + mw)
                    if hi - lo > 20:
                        span = _mapped_span(
                            lo, hi, mx, mw, monitor["x"], monitor["w"])
                        out.append(_portal(
                            target, display, monitor, "target-bottom", "y",
                            monitor["y"], span, -1,
                            (None, monitor["y"] + exit_inset(monitor, "y"))))
    return out


def _portal(target, display, monitor, edge, axis, line, span, sign, exit_to):
    return {
        "target": target["id"],
        "target_name": target["name"],
        "target_display": display["id"],
        "target_display_name": display["name"],
        "daemon_port": int(target["port"]),
        "edge": edge,
        "monitor": monitor["name"],
        "axis": axis,
        "line": int(line),
        "span": [int(span[0]), int(span[1])],
        "span_axis": "y" if axis == "x" else "x",
        "sign": int(sign),
        "exit_to": [
            None if exit_to[0] is None else int(exit_to[0]),
            None if exit_to[1] is None else int(exit_to[1]),
        ],
    }


def validate_mac_displays(rows, device_id=None):
    """Normalize editor rows; raises ValueError with a user-facing message."""
    if not 1 <= len(rows) <= 8:
        raise ValueError("Choose between 1 and 8 Mac displays.")
    result = []
    seen = set()
    for index, raw in enumerate(rows):
        name = str(raw.get("name") or f"Mac Display {index + 1}").strip()
        try:
            res_w = int(raw["res_w"])
            res_h = int(raw["res_h"])
            refresh = float(raw["refresh_hz"])
            rotation = int(raw.get("rotation", 0))
            physical_width = float(raw.get("physical_width", 24.0))
        except (KeyError, TypeError, ValueError):
            raise ValueError(
                f"{name}: resolution, refresh rate, and physical width "
                "must be numbers.") from None
        if not 320 <= res_w <= 16384 or not 240 <= res_h <= 16384:
            raise ValueError(f"{name}: resolution is outside the supported range.")
        if not 24 <= refresh <= 480:
            raise ValueError(f"{name}: refresh rate must be 24–480 Hz.")
        if rotation not in (0, 90, 180, 270):
            raise ValueError(f"{name}: rotation must be 0°, 90°, 180°, or 270°.")
        if not 5 <= physical_width <= 100:
            raise ValueError(f"{name}: physical width must be 5–100 inches.")
        ident = str(raw.get("id") or f"mac-{index + 1}")
        if ident in seen:
            # Find a genuinely free id. The old fallback regenerated
            # f"mac-{index+1}", which for (mac-1, mac-3, +add -> mac-3) is the
            # SAME colliding name -- two displays then share one id, and the
            # portal's (target, id) dict silently drops one panel (misrouted
            # input) while the canvas can no longer drag it.
            base = device_id or "display"
            probe = index + 1
            while f"{base}-{probe}" in seen:
                probe += 1
            ident = f"{base}-{probe}"
        seen.add(ident)
        row = {
            "id": ident,
            "name": name,
            "res_w": res_w,
            "res_h": res_h,
            "refresh_hz": refresh,
            "rotation": rotation,
            "physical_width": physical_width,
        }
        result.append(row)
    return result
