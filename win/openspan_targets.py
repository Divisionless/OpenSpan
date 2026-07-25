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


CONFIG_VERSION = 2
IPAD_PORT = 9955
MAC_PORT = 9956
MIN_LAYOUT_SIZE = 120


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


def _default_ipad(live_monitors, legacy=None):
    primary = next(
        (m for m in live_monitors if m.get("primary")), live_monitors[0])
    old = dict(legacy or {})
    old.setdefault("x", primary["x"] + primary["w"])
    old.setdefault("y", primary["y"])
    old.setdefault("w", 1080)
    old.setdefault("h", 810)
    old.setdefault("res_w", 1080)
    old.setdefault("res_h", 810)
    old.setdefault("rotation", 0)
    old.update({
        "id": "ipad-main",
        "name": "iPad",
        "refresh_hz": int(old.get("refresh_hz", 60)),
    })
    return {
        "id": "ipad",
        "name": "iPad",
        "kind": "ipad",
        "daemon_port": IPAD_PORT,
        "enabled": True,
        "displays": [old],
    }


def _default_mac(live_monitors):
    primary = next(
        (m for m in live_monitors if m.get("primary")), live_monitors[0])
    bottom = max(
        int(m.get("layout_y", m["y"]))
        + int(m.get("layout_h", m["h"]))
        for m in live_monitors)
    base_x = int(primary.get("layout_x", primary["x"]))
    specs = [
        ("mac-1", "Mac Display 1", 90, 540, 960),
        ("mac-2", "Mac Display 2", 0, 960, 540),
        ("mac-3", "Mac Display 3", 90, 540, 960),
    ]
    displays = []
    cursor_x = base_x
    for ident, name, rotation, width, height in specs:
        displays.append({
            "id": ident,
            "name": name,
            "x": cursor_x,
            "y": bottom,
            "w": width,
            "h": height,
            "res_w": 3840,
            "res_h": 2160,
            "refresh_hz": 60,
            "rotation": rotation,
        })
        cursor_x += width
    return {
        "id": "mac",
        "name": "Managed Mac",
        "kind": "mac",
        "daemon_port": MAC_PORT,
        "enabled": True,
        "displays": displays,
    }


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
    if "w" not in row or "h" not in row:
        effective_w, effective_h = oriented_resolution(row)
        row["w"] = max(MIN_LAYOUT_SIZE, int(effective_w / 4))
        row["h"] = max(MIN_LAYOUT_SIZE, int(effective_h / 4))
    else:
        row["w"] = max(MIN_LAYOUT_SIZE, int(row["w"]))
        row["h"] = max(MIN_LAYOUT_SIZE, int(row["h"]))
    return row


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

    targets = []
    for index, target in enumerate(raw.get("targets", [])):
        if not isinstance(target, dict):
            continue
        target_id = str(target.get("id") or f"target-{index + 1}")
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
        targets.append({
            "id": target_id,
            "name": str(target.get("name") or target_id.title()),
            "kind": str(target.get("kind") or target_id),
            "daemon_port": int(target.get(
                "daemon_port", IPAD_PORT if target_id == "ipad" else MAC_PORT)),
            "enabled": bool(target.get("enabled", True)),
            "displays": displays,
        })

    target_ids = {target["id"] for target in targets}
    if "ipad" not in target_ids:
        targets.insert(0, _default_ipad(monitors, raw.get("ipad")))
    if "mac" not in target_ids:
        targets.append(_default_mac(monitors))

    result = {
        "version": CONFIG_VERSION,
        "monitors": monitors,
        "targets": targets,
    }
    # One-time upgrade for layouts saved before the adjacency graph existed.
    # Re-run the iPad's snap with the new two-axis solver so a near-touching
    # second edge (such as Mac above + PC right) becomes a real connection.
    if "links" not in raw:
        ipad_target = next(
            (row for row in targets if row.get("id") == "ipad"), None)
        if ipad_target and ipad_target.get("displays"):
            ipad_display = ipad_target["displays"][0]
            rect = (
                ipad_display["x"], ipad_display["y"],
                ipad_display["w"], ipad_display["h"])
            neighbors = []
            for monitor in monitors:
                neighbors.append(_monitor_layout(monitor))
            for target in targets:
                for display in target.get("displays", []):
                    if display is ipad_display:
                        continue
                    neighbors.append((
                        display["x"], display["y"],
                        display["w"], display["h"]))
            ipad_display["x"], ipad_display["y"] = \
                snap_rect_to_neighbors(rect, neighbors)
    # Keep the legacy field current so an older executable can still operate
    # the iPad if the user rolls back to the proven single-target build.
    sync_legacy_ipad(result)
    result["portals"] = compute_portals(result)
    result["links"] = compute_adjacencies(result)
    return result


def target_by_id(config, target_id):
    return next(
        (target for target in config.get("targets", [])
         if target.get("id") == target_id),
        None)


def display_by_id(config, target_id, display_id):
    target = target_by_id(config, target_id)
    if not target:
        return None
    return next(
        (display for display in target.get("displays", [])
         if display.get("id") == display_id),
        None)


def sync_legacy_ipad(config):
    target = target_by_id(config, "ipad")
    if target and target.get("displays"):
        display = copy.deepcopy(target["displays"][0])
        for key in ("id", "name", "refresh_hz", "rotation"):
            display.pop(key, None)
        config["ipad"] = display
    return config


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
    for target in config.get("targets", []):
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


def compute_portals(config):
    """Compute real Windows edge triggers from the independent desk layout."""
    out = []
    for target in config.get("targets", []):
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
                            (monitor["x"] + monitor["w"] - 3, None)))
                # target right touches Windows monitor left
                if abs((tx + tw) - mx) <= 2:
                    lo, hi = max(ty, my), min(ty + th, my + mh)
                    if hi - lo > 20:
                        span = _mapped_span(
                            lo, hi, my, mh, monitor["y"], monitor["h"])
                        out.append(_portal(
                            target, display, monitor, "target-right", "x",
                            monitor["x"], span, -1,
                            (monitor["x"] + 3, None)))
                # target top touches Windows monitor bottom
                if abs(ty - (my + mh)) <= 2:
                    lo, hi = max(tx, mx), min(tx + tw, mx + mw)
                    if hi - lo > 20:
                        span = _mapped_span(
                            lo, hi, mx, mw, monitor["x"], monitor["w"])
                        out.append(_portal(
                            target, display, monitor, "target-top", "y",
                            monitor["y"] + monitor["h"] - 1, span, +1,
                            (None, monitor["y"] + monitor["h"] - 3)))
                # target bottom touches Windows monitor top
                if abs((ty + th) - my) <= 2:
                    lo, hi = max(tx, mx), min(tx + tw, mx + mw)
                    if hi - lo > 20:
                        span = _mapped_span(
                            lo, hi, mx, mw, monitor["x"], monitor["w"])
                        out.append(_portal(
                            target, display, monitor, "target-bottom", "y",
                            monitor["y"], span, -1,
                            (None, monitor["y"] + 3)))
    return out


def _portal(target, display, monitor, edge, axis, line, span, sign, exit_to):
    return {
        "target": target["id"],
        "target_name": target["name"],
        "target_display": display["id"],
        "target_display_name": display["name"],
        "daemon_port": int(target["daemon_port"]),
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


def validate_mac_displays(rows):
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
            probe = index + 1
            while f"mac-{probe}" in seen:
                probe += 1
            ident = f"mac-{probe}"
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
