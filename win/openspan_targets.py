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
# How many WINDOWS pixels of offset between two neighbouring monitors count as
# a leftover from dragging them together rather than an arrangement. Windows'
# display page snaps a dragged monitor against the one it is dropped on, and
# what survives is a few pixels of noise -- this desk's DISPLAY1 sits at x=4
# beside a primary at x=0. Under this many pixels the two are drawn exactly
# level; over it the offset is a decision and is drawn as one. It is a noise
# band, not a snap distance, so it stays small: 8px is 0.4% of a 1920 panel.
WINDOWS_EDGE_NOISE_PX = 8
# How many candidate translations per axis the block's escape search combines
# into diagonals. The straight moves are all tried, and they already include
# the ones guaranteed to clear every device, so this bounds the corner search
# without being able to cost it an answer.
_SHIFT_FAN = 12


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
        "keyboard_verbatim": False,   # send keys exactly as pressed
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


def _effective_diagonal(name, saved, sizes):
    """Which physical size wins for this monitor, and where it came from.

    Three sources can claim to know a panel's diagonal and they are not equal.
    A number somebody TYPED is a deliberate override -- the person at the desk
    with a tape measure outranks a panel that reports its own size wrong -- so
    it wins outright and keeps winning across every reload. Otherwise the panel
    speaks for itself: its EDID states the active area in centimetres, which is
    a measurement rather than a guess.

    A LEGACY value -- a diagonal saved before this app recorded WHO typed it --
    survives only where EDID has nothing to say. It outranks silence and
    nothing else: where the panel does state its own size, the panel wins and
    the screen is REDRAWN. That is deliberate. An unattributed number cannot be
    told apart from a default somebody accepted once, and between an accepted
    default and a measurement the measurement is the better answer -- this
    desk's primary carried 17.0" against an EDID that says 15.7". The redraw is
    not silent: normalize_config and merge_live_monitors both report it (see
    last_normalize_report and the merge's "resized"), which is what makes
    overriding it honest rather than surprising.

    Returns (diagonal_in, source) with source one of "user", "edid", the
    legacy value's own source, or None when nothing knows.
    """
    saved = saved or {}
    typed = saved.get("diagonal_in")
    if typed and str(saved.get("diagonal_source", "")) == "user":
        return float(typed), "user"
    measured = (sizes or {}).get(name)
    if measured:
        return float(measured), "edid"
    if typed:
        return float(typed), (str(saved.get("diagonal_source") or "") or None)
    return None, None


def _diagonal_changed(old, new):
    """True when a monitor's effective size really moved.

    EDID states the panel's edges in whole centimetres, so the diagonal it
    implies is only good to one decimal -- comparing floats exactly would
    report a "size change" every time the same panel was measured twice."""
    if not new:
        return False
    if not old:
        return True
    return abs(float(old) - float(new)) > 0.05


def _normalize_monitor(live, saved, sizes=None):
    row = dict(live)
    saved = saved or {}
    row["layout_x"] = int(saved.get("layout_x", live["x"]))
    row["layout_y"] = int(saved.get("layout_y", live["y"]))
    row["layout_w"] = max(
        MIN_LAYOUT_SIZE, int(saved.get("layout_w", live["w"])))
    row["layout_h"] = max(
        MIN_LAYOUT_SIZE, int(saved.get("layout_h", live["h"])))
    # Refresh is WINDOWS' fact about a local monitor, not ours to remember, so
    # the LIVE reading wins whenever there is one. The saved value survives only
    # as a fallback for a monitor whose mode could not be read this run.
    #
    # Nothing invents 60 any more. This line used to be
    # `float(saved.get("refresh_hz", 60))` while no code path anywhere ever
    # wrote a real number into it, so the hover card asserted "@ 60 Hz" for
    # every local screen -- a number the app had simply made up. An absent key
    # now means "not known", and the UI says nothing rather than lying.
    hz = live.get("refresh_hz") or saved.get("refresh_hz")
    if hz:
        row["refresh_hz"] = float(hz)
    else:
        row.pop("refresh_hz", None)
    diagonal, source = _effective_diagonal(
        str(live.get("name", "")), saved, sizes)
    if diagonal:
        # A local monitor's desk size was its PIXEL count, which made a 17"
        # 1080p panel 1920 units wide and forced every real screen to be
        # inflated to match. Physical inches make them comparable.
        row["diagonal_in"] = float(diagonal)
        # WHERE the number came from is kept beside it, because a typed size
        # has to survive the next EDID read and the hover card says which one
        # the picture is drawn from. Cleared first, so a stale attribution
        # cannot outlive the number it described.
        row.pop("diagonal_source", None)
        if source:
            row["diagonal_source"] = source
        row["layout_w"], row["layout_h"] = physical_size(
            diagonal, live["w"], live["h"], 0)
    return row


def _desk_per_pixel(monitor):
    """(x, y) desk units per Windows pixel for this monitor.

    The two axes are computed separately and are very nearly equal -- both come
    from the same diagonal and the same aspect -- but rounding layout_w/h to
    whole desk units leaves them slightly apart, and using one ratio for both
    axes would spend that rounding error on the taller axis of every screen."""
    return (
        float(monitor.get("layout_w", monitor["w"]))
        / max(1.0, float(monitor["w"])),
        float(monitor.get("layout_h", monitor["h"]))
        / max(1.0, float(monitor["h"])),
    )


def _seed_axis(reference_origin, origin, size, desk_size, ratio):
    """One axis of a monitor's desk position, scaled off a REFERENCE screen.

    Windows states a monitor's position as the offset of its TOP-LEFT corner,
    but what it means by putting DISPLAY4 at x=-1920 beside a 1920px primary is
    that the two panels touch. Mapping the top-left corner alone keeps that
    promise only while both panels are the same physical size: a 15.7" panel to
    the left of a 17.1" one lands its right edge ~120 desk units short of the
    primary, which is a visible gap where Windows says there is none.

    So the edge that FACES the reference is the one that gets mapped. For a
    monitor entirely before it on this axis that is the far edge (right, or
    bottom); for anything else it is the leading edge. A real gap in Windows
    still maps to a proportional gap, because the offset is scaled either way
    and only the corner being measured changes.

    This is the fallback, not the rule. A monitor that TOUCHES something
    already placed is seeded off that neighbour instead (_edge_seed), because
    one ratio cannot describe a block of mixed-size panels: scaling every
    screen by the primary's ratio is exactly what let a five-screen desk come
    apart, one screen landing on the primary itself. What is left for this is a
    monitor touching nothing placed -- a real gap in Windows -- where there is
    no shared edge to map and a proportional guess is the honest answer.
    """
    if origin + size <= reference_origin:
        return (origin + size - reference_origin) * ratio - desk_size
    return (origin - reference_origin) * ratio


def _block_seed(reference, monitor, origin):
    """Where `monitor` lands scaled off `reference`, which sits at `origin`."""
    ratio_x, ratio_y = _desk_per_pixel(reference)
    _mx, _my, desk_w, desk_h = _monitor_layout(monitor)
    return (
        origin[0] + _seed_axis(
            int(reference["x"]), int(monitor["x"]), int(monitor["w"]),
            desk_w, ratio_x),
        origin[1] + _seed_axis(
            int(reference["y"]), int(monitor["y"]), int(monitor["h"]),
            desk_h, ratio_y),
    )


def _windows_side(base, monitor):
    """Which side of `base` `monitor` is flush against, in WINDOWS pixels.

    "right", "left", "below", "above", or None when the two do not share an
    edge at all. Sharing an edge means the edges coincide AND the perpendicular
    spans genuinely overlap: a shared corner is not an adjacency, and neither
    is a screen that happens to line up with one across the desk.

    The four answers are mutually exclusive. An edge shared on x leaves the two
    x spans meeting at a point, so no y edge can be shared as well.
    """
    bx, by = int(base["x"]), int(base["y"])
    bw, bh = int(base["w"]), int(base["h"])
    mx, my = int(monitor["x"]), int(monitor["y"])
    mw, mh = int(monitor["w"]), int(monitor["h"])
    if min(by + bh, my + mh) - max(by, my) > 0:
        if mx == bx + bw:
            return "right"
        if mx + mw == bx:
            return "left"
    if min(bx + bw, mx + mw) - max(bx, mx) > 0:
        if my == by + bh:
            return "below"
        if my + mh == by:
            return "above"
    return None


def _along_edge(base_desk, base_desk_size, base_px, base_px_size, base_ratio,
                desk_size, px, px_size, ratio):
    """Where a monitor sits ALONG the edge it shares with its neighbour.

    Windows' arrangement page snaps a monitor being dragged against the one it
    is dropped on, so a handful of pixels left over from that drag is not an
    arrangement: this desk's DISPLAY1 sits at x=4 beside a primary at x=0 and
    nobody put it there. An offset within WINDOWS_EDGE_NOISE_PX of tops level,
    bottoms level or centres level is read as that leftover and drawn exactly
    level. Anything larger is a decision and is kept -- which is why the band is
    a few pixels wide rather than a snap distance.

    A real offset is mapped through the BAND THE TWO PANELS SHARE, not through
    either one's leading corner. The shared band is the only part of either
    panel the other can reach, and it is the thing being preserved: its middle
    is found on the neighbour in the NEIGHBOUR's desk-per-pixel ratio, found on
    this panel in THIS panel's, and the two points are laid on top of each
    other. Mapping a corner instead works only while both panels have the same
    pixel density -- put a 3840px 24" panel above a 1366px 27" one and the
    corner offset stretches by the neighbour's ratio while the panel's own body
    shrinks by its own, so the two slide past each other and the shared edge
    ends up shared with nothing. Mapping the band cannot do that: both mapped
    spans contain the same middle point, so they always overlap.
    """
    leading = px - base_px
    levels = (
        (abs(leading), base_desk),
        (abs((px + px_size) - (base_px + base_px_size)),
         base_desk + base_desk_size - desk_size),
        (abs((px + px_size / 2.0) - (base_px + base_px_size / 2.0)),
         base_desk + (base_desk_size - desk_size) / 2.0),
    )
    # Ties go to the first listed -- tops before bottoms before centres -- so
    # the answer is a function of the hardware and not of iteration order.
    off, level = min(levels, key=lambda row: row[0])
    if off <= WINDOWS_EDGE_NOISE_PX:
        return level
    middle = (max(base_px, px)
              + min(base_px + base_px_size, px + px_size)) / 2.0
    return (base_desk + (middle - base_px) * base_ratio
            - (middle - px) * ratio)


def _edge_seed(base, base_rect, monitor, side):
    """Where `monitor` sits once seeded off the neighbour it TOUCHES.

    The shared edge is mapped EXACTLY -- flush in Windows comes out flush on
    the desk, whatever the two panels measure -- and only the offset along that
    edge is scaled. Nothing here needs a snap afterwards to close a gap the
    seed opened, because the seed opens none.
    """
    bx, by, bw, bh = base_rect
    base_ratio_x, base_ratio_y = _desk_per_pixel(base)
    ratio_x, ratio_y = _desk_per_pixel(monitor)
    _mx, _my, desk_w, desk_h = _monitor_layout(monitor)
    if side in ("right", "left"):
        return (
            bx + bw if side == "right" else bx - desk_w,
            _along_edge(by, bh, int(base["y"]), int(base["h"]), base_ratio_y,
                        desk_h, int(monitor["y"]), int(monitor["h"]), ratio_y),
        )
    return (
        _along_edge(bx, bw, int(base["x"]), int(base["w"]), base_ratio_x,
                    desk_w, int(monitor["x"]), int(monitor["w"]), ratio_x),
        by + bh if side == "below" else by - desk_h,
    )


def _slide_along(rect, base_rect, side, placed, tolerance=2.0):
    """Keep the shared edge, and move ALONG it until there is room.

    Physical sizes do not have to pack the way pixel rectangles did: a 24"
    panel drawn beside a 17" one protrudes past both its neighbour's edges,
    into desk space its pixels never occupied, and a screen seeded flush
    against that neighbour can land on it. Pushing the newcomer off the way it
    came breaks the edge it was seeded on -- and that edge is the arrangement,
    the thing Windows actually said. Sliding along the edge keeps it: the
    screen stays flush against the neighbour it belongs to and sits further
    down it, which is what a person with two monitors and one desk would do.

    Candidates are the seed itself and every position flush past a screen
    already placed, nearest first, and each has to leave the newcomer both
    clear of everything and still genuinely overlapping the neighbour it was
    seeded from -- a slide that runs off the end of the shared edge has kept
    the edge and lost the contact. None means nothing along the edge works and
    the caller should fall back to the ordinary push.
    """
    x, y, width, height = rect
    # A horizontal shared edge (one screen above the other) is slid along x.
    axis_x = side in ("below", "above")
    start, size = (x, width) if axis_x else (y, height)
    base_lo, base_size = ((base_rect[0], base_rect[2]) if axis_x
                          else (base_rect[1], base_rect[3]))
    options = {start}
    for ox, oy, ow, oh in placed:
        lo, span = (ox, ow) if axis_x else (oy, oh)
        options.add(lo - size)
        options.add(lo + span)
    for option in sorted(options,
                         key=lambda value: (abs(value - start), value)):
        if min(option + size, base_lo + base_size) - max(option, base_lo) <= 0:
            continue
        candidate = ((option, y, width, height) if axis_x
                     else (x, option, width, height))
        if not any(rects_overlap(candidate, other, tolerance)
                   for other in placed):
            return int(candidate[0]), int(candidate[1])
    return None


def _escape(base, monitor, side=None):
    """The axis and direction Windows puts `monitor` on: ("x"|"y", +1/-1).

    Only ever spent as _push_out's last resort, so it answers for every pair
    including two screens Windows has stacked on the same rectangle -- there
    the centres coincide, and the tie falls to downward, which is where a
    duplicated display has always been drawn."""
    if side:
        return {"right": ("x", 1), "left": ("x", -1),
                "below": ("y", 1), "above": ("y", -1)}[side]
    dx = ((int(monitor["x"]) + int(monitor["w"]) / 2.0)
          - (int(base["x"]) + int(base["w"]) / 2.0))
    dy = ((int(monitor["y"]) + int(monitor["h"]) / 2.0)
          - (int(base["y"]) + int(base["h"]) / 2.0))
    if abs(dx) > abs(dy):
        return ("x", 1 if dx >= 0 else -1)
    return ("y", 1 if dy >= 0 else -1)


def _union_escape(rect, placed):
    """Which way out of `placed` is shortest for `rect`, as ("x"|"y", +1/-1).

    Used when the caller has no opinion. The four ways out are compared by how
    far each moves the rectangle, so an unhinted escape is still the short one.
    """
    x, y, width, height = rect
    left = min(ox for ox, _oy, _ow, _oh in placed)
    right = max(ox + ow for ox, _oy, ow, _oh in placed)
    top = min(oy for _ox, oy, _ow, _oh in placed)
    bottom = max(oy + oh for _ox, oy, _ow, oh in placed)
    dx = (x + width / 2.0) - (left + right) / 2.0
    dy = (y + height / 2.0) - (top + bottom) / 2.0
    cost_x = (right - x) if dx >= 0 else (x + width - left)
    cost_y = (bottom - y) if dy >= 0 else (y + height - top)
    if cost_x <= cost_y:
        return ("x", 1 if dx >= 0 else -1)
    return ("y", 1 if dy >= 0 else -1)


def _push_out(rect, placed, tolerance=2.0, escape=None):
    """Move a rectangle off anything it landed on top of, the short way.

    Two PC screens can share one pixel rectangle -- that is what Windows calls
    duplicating a display -- and a desk cannot draw one on top of the other. A
    rectangle that still overlaps after the seed is pushed clear along the axis
    it has penetrated LEAST, which is both the shortest way out and the one
    that leaves the arrangement recognisable.

    Those passes can also FAIL. Two neighbours can stand either side of a
    rectangle and hand it back and forth -- each push lands it on the other,
    the passes run out, and this used to return the last position it tried,
    still overlapping, with nothing said. The caller then appended a known-bad
    rectangle to `placed` and every later screen was measured against a screen
    that was not really there. So the result is re-tested,
    and a rectangle that is still colliding is put FLUSH BEYOND the union of
    everything placed, on the axis and in the direction Windows puts it. That
    position is always clear, because nothing placed reaches past the union, so
    this returns a legal rectangle or does not return.

    `escape` is that direction as ("x"|"y", +1/-1) -- the caller knows where
    Windows has the screen. Without one it is derived from the rectangle's own
    position (_union_escape), which keeps the answer deterministic either way.
    """
    x, y, width, height = rect
    for _pass in range(len(placed) + 1):
        moved = False
        for ox, oy, ow, oh in placed:
            if not rects_overlap((x, y, width, height),
                                 (ox, oy, ow, oh), tolerance):
                continue
            across = min(x + width, ox + ow) - max(x, ox)
            down = min(y + height, oy + oh) - max(y, oy)
            if across <= down:
                x = (ox - width if x + width / 2.0 <= ox + ow / 2.0
                     else ox + ow)
            else:
                y = (oy - height if y + height / 2.0 <= oy + oh / 2.0
                     else oy + oh)
            moved = True
        if not moved:
            break
    if not placed or not any(
            rects_overlap((x, y, width, height), other, tolerance)
            for other in placed):
        return int(round(x)), int(round(y))
    axis, sign = escape or _union_escape((x, y, width, height), placed)
    if axis == "x":
        x = (max(ox + ow for ox, _oy, ow, _oh in placed) if sign > 0
             else min(ox for ox, _oy, _ow, _oh in placed) - width)
    else:
        y = (max(oy + oh for _ox, oy, _ow, oh in placed) if sign > 0
             else min(oy for _ox, oy, _ow, _oh in placed) - height)
    return int(round(x)), int(round(y))


def _shift_cost(shift):
    """Order candidate block translations: least movement first, then fixed."""
    dx, dy = shift
    return (abs(dx) + abs(dy), abs(dx), abs(dy), dx, dy)


def _block_shift(rects, obstacles, tolerance=2.0):
    """The smallest translation that lifts the whole block off the devices.

    The PC's screens are arranged by Windows and the DEVICES are arranged by
    the user, and neither knows about the other: load a saved desk whose
    primary has since changed, or plug in a screen that was not there before,
    and the block is re-derived straight through an iPad. Moving the one screen
    that landed on something would be worse than the overlap -- the block would
    then disagree with Windows, which is the fault the block exists to prevent
    -- so the block moves as ONE thing.

    Candidates are the translations that put some PC screen flush beyond some
    device screen, plus the four that clear the devices' union outright. The
    union four are always clear, so a clear position always exists and this
    never has to return a known-bad one; the rest are tried first because they
    move the block less. Trying them in order of MOVEMENT is also what keeps
    the anchor honest: a block that already sits clear does not move at all,
    and one that does not moves as little as the devices allow.
    """
    rects = [tuple(float(value) for value in row) for row in (rects or [])]
    obstacles = [tuple(float(value) for value in row)
                 for row in (obstacles or [])]
    if not rects or not obstacles:
        return (0, 0)

    def clear(shift):
        dx, dy = shift
        return not any(
            rects_overlap((x + dx, y + dy, width, height), obstacle, tolerance)
            for x, y, width, height in rects for obstacle in obstacles)

    if clear((0.0, 0.0)):
        return (0, 0)
    xs, ys = {0.0}, {0.0}
    for ox, oy, ow, oh in obstacles:
        for x, y, width, height in rects:
            xs.add(ox + ow - x)
            xs.add(ox - (x + width))
            ys.add(oy + oh - y)
            ys.add(oy - (y + height))
    xs.add(max(ox + ow for ox, _oy, ow, _oh in obstacles)
           - min(x for x, _y, _w, _h in rects))
    xs.add(min(ox for ox, _oy, _ow, _oh in obstacles)
           - max(x + width for x, _y, width, _h in rects))
    ys.add(max(oy + oh for _ox, oy, _ow, oh in obstacles)
           - min(y for _x, y, _w, _h in rects))
    ys.add(min(oy for _ox, oy, _ow, _oh in obstacles)
           - max(y + height for _x, y, _w, height in rects))
    ordered_x = sorted(xs, key=lambda value: (abs(value), value))
    ordered_y = sorted(ys, key=lambda value: (abs(value), value))
    # Straight moves first, then the diagonals -- a corner can be nearer than
    # either edge. The diagonal fan is capped so the search stays bounded on a
    # desk with many device screens; the guaranteed union moves are in the
    # straight list, so the cap can never cost the answer.
    candidates = ([(dx, 0.0) for dx in ordered_x]
                  + [(0.0, dy) for dy in ordered_y]
                  + [(dx, dy) for dx in ordered_x[:_SHIFT_FAN]
                     for dy in ordered_y[:_SHIFT_FAN]])
    for shift in sorted(set(candidates), key=_shift_cost):
        if clear(shift):
            return (int(round(shift[0])), int(round(shift[1])))
    return (0, 0)


def _pixel_gap(first, second):
    """How far apart two monitors are in WINDOWS pixels, rectangle to
    rectangle. Zero for anything touching or overlapping."""
    across = max(int(first["x"]) - (int(second["x"]) + int(second["w"])),
                 int(second["x"]) - (int(first["x"]) + int(first["w"])), 0)
    down = max(int(first["y"]) - (int(second["y"]) + int(second["h"])),
               int(second["y"]) - (int(first["y"]) + int(first["h"])), 0)
    return (float(across) ** 2 + float(down) ** 2) ** 0.5


def pc_block_layout(monitors: list[dict],
                    anchor: tuple[float, float] | None = None,
                    obstacles: list[tuple] | None = None) -> list[dict]:
    """Place this PC's screens on the desk the way WINDOWS has them.

    THE PC BLOCK FOLLOWS WINDOWS. Where the PC's own screens sit relative to
    each other is settled in Windows Display Settings and nowhere else, so it is
    DERIVED here -- from the pixel arrangement Windows reports and each panel's
    physical size -- instead of being dragged into place one screen at a time.
    Dragging them apart was possible before this existed, which is how the
    canvas could disagree with Windows about the PC's own layout while both
    pictures looked perfectly plausible. What the user still positions is where
    the block as a whole SITS among the devices, and that is the anchor.

    Each screen is placed off the screen it TOUCHES. Walking outwards from the
    anchor over Windows' own adjacency graph, every monitor's shared edge is
    mapped exactly and only its offset along that edge is scaled -- through the
    ratio of the NEIGHBOUR it touches, the panel it is being measured from.
    Scaling everything by the primary's ratio instead is what used to take a
    mixed-size block apart. Over 400 Windows-legal desks of each size that
    dropped an adjacency Windows had on 24.5% of three-screen desks and 44.5%
    of four-screen ones, and drew one screen on top of another on 6.5% of
    five-screen ones -- in one of those a 14" laptop panel landed exactly on
    the primary, at (0, 0).

    A monitor that touches nothing placed -- a real gap in Windows -- is the
    one case with no shared edge to map. It is seeded proportionally from its
    nearest placed neighbour instead, and left where that puts it: a gap
    Windows reports is a fact about the desk, not damage to be repaired.

    `obstacles` are the DEVICE rectangles already on the desk. The PC's screens
    and the devices are arranged by different authorities, so a re-derived
    block can land on an iPad; when it does the whole block is translated clear
    (see _block_shift) rather than one screen being moved out of formation.
    None or [] skips that entirely.

    Input rows carry Windows' own x/y/w/h and primary flag plus an already
    derived layout_w/layout_h; NEW dicts come back with layout_x/layout_y set
    and everything else copied through, so the caller's records are never
    mutated underneath it. `anchor` places the primary and defaults to wherever
    the primary already sits, which makes the whole thing idempotent: running it
    on its own output reproduces that output exactly.

    Zero monitors give an empty list and one monitor is simply anchored.
    """
    rows = [dict(row) for row in (monitors or [])]
    if not rows:
        return rows
    primary = next((row for row in rows if row.get("primary")), rows[0])
    if anchor is None:
        anchor = (primary.get("layout_x", primary.get("x", 0)),
                  primary.get("layout_y", primary.get("y", 0)))
    anchor = (int(round(float(anchor[0]))), int(round(float(anchor[1]))))
    primary["layout_x"], primary["layout_y"] = anchor
    placed = [primary]
    rects = [_monitor_layout(primary)]
    # Sorted by name, and the walk below always takes the first match, so the
    # arrangement is a function of the hardware rather than of whatever order
    # EnumDisplayMonitors happened to return this run.
    remaining = sorted((row for row in rows if row is not primary),
                       key=lambda row: str(row.get("name", "")))

    while remaining:
        step = None
        for base in placed:
            for row in remaining:
                side = _windows_side(base, row)
                if side:
                    step = (base, row, side)
                    break
            if step:
                break
        if step is not None:
            base, row, side = step
            seed = _edge_seed(base, _monitor_layout(base), row, side)
        else:
            # Nothing left touches anything placed. Windows' own settings page
            # will not leave a gap, but the enumerator is not the settings
            # page, and whatever it reports still has to come out legible.
            base, row = min(
                ((base, row) for base in placed for row in remaining),
                key=lambda pair: (_pixel_gap(*pair),
                                  str(pair[1].get("name", "")),
                                  str(pair[0].get("name", ""))))
            side = None
            seed = _block_seed(
                base, row, (base["layout_x"], base["layout_y"]))
        _rx, _ry, width, height = _monitor_layout(row)
        seeded = (int(round(seed[0])), int(round(seed[1])), width, height)
        # A screen seeded off an edge keeps that edge if there is any way to:
        # it slides along it rather than being pushed off it. Only a screen
        # with no shared edge to keep, or one with nowhere along it to go, is
        # left to the push -- which never returns an overlapping position.
        slid = (_slide_along(seeded, _monitor_layout(base), side, rects)
                if side is not None else None)
        x, y = slid or _push_out(
            seeded, rects, escape=_escape(base, row, side))
        row["layout_x"], row["layout_y"] = int(x), int(y)
        placed.append(row)
        rects.append((int(x), int(y), width, height))
        # By IDENTITY. list.remove compares by VALUE, and these are dicts: two
        # rows that happened to be equal would see the wrong one dropped and
        # the placed one walked over again, on a list this loop is draining.
        remaining = [other for other in remaining if other is not row]

    shift_x, shift_y = _block_shift(
        [_monitor_layout(row) for row in rows], obstacles)
    if shift_x or shift_y:
        for row in rows:
            row["layout_x"] += shift_x
            row["layout_y"] += shift_y
    return rows


def _saved_block_anchor(monitors, saved_by_name):
    """Where to pin the PC block, given what the user had already placed.

    The block's position among the devices belongs to the user, and the primary
    is the screen that carries it: dragging any PC screen moves them all, so the
    primary's saved layout position IS the block's position.

    When the primary has no saved position -- Windows moved the flag onto a
    panel that was only just plugged in -- pinning the block to that panel's raw
    pixel origin would teleport the whole PC across the desk over a cable
    change. So the block is pinned instead so that the first screen that DOES
    have a saved position keeps it, and only the arrangement WITHIN the block
    is re-derived. None means nothing was ever placed and the primary keeps
    whatever position it already has.

    Which screen that is has to be settled by NAME, not by enumeration order.
    Two panels can both carry a saved position, EnumDisplayMonitors does not
    promise an order, and pinning the whole PC to whichever one came back first
    would move the desk between launches with nothing at all having changed.
    """
    if not monitors:
        return None
    primary = next((row for row in monitors if row.get("primary")), monitors[0])
    ordered = [primary] + sorted(
        (row for row in monitors if row is not primary),
        key=lambda row: str(row.get("name", "")))
    for row in ordered:
        saved = saved_by_name.get(str(row.get("name", ""))) or {}
        if saved.get("layout_x") is None or saved.get("layout_y") is None:
            continue
        if row is primary:
            return (int(saved["layout_x"]), int(saved["layout_y"]))
        # Where this screen ends up when the block is pinned at the origin --
        # asked of the layout itself rather than recomputed from a formula
        # beside it, so the anchor cannot drift away from the placement it is
        # supposed to invert.
        index = next(slot for slot, other in enumerate(monitors)
                     if other is row)
        probe = pc_block_layout(monitors, anchor=(0, 0))[index]
        return (int(round(float(saved["layout_x"]) - probe["layout_x"])),
                int(round(float(saved["layout_y"]) - probe["layout_y"])))
    return None


def merge_live_monitors(saved_monitors, live_monitors, sizes=None,
                        obstacles=None):
    """Re-read Windows WITHOUT discarding what only the user can know.

    Windows owns a local monitor's position, resolution, refresh rate and which
    one is primary, and it can be asked for all four at any moment. It does NOT
    know any monitor's physical size -- that is why `diagonal_in` is typed in by
    hand -- and it has no opinion about where the user has decided a screen sits
    in the desk arrangement.

    So a refresh REPLACES the machine's facts and PRESERVES the human's:
    diagonal_in and the hand-placed layout_x/layout_y survive for every monitor
    that is still attached. A refresh that reset diagonal_in would destroy the
    only field the user can supply, which would make the button worse than not
    having one at all.

    layout_w/layout_h are derived, not typed: a rectangle's size comes from the
    diagonal and the aspect the live resolution implies, so for a monitor that
    HAS a diagonal they are deliberately dropped and re-derived -- that is how a
    resolution change reaches the drawing.

    A monitor with NO diagonal is the opposite case: nothing can re-derive them,
    so dropping them silently reset that monitor's desk rectangle to its raw
    pixel count (measured on this desk: 900x506 -> 1920x1080). That moves every
    crossing band on the screen and changes portal_signature -- an eight-second
    input restart -- while the report below still said "nothing changed". So
    they are preserved in exactly that case and only that case.

    `sizes` maps GDI name -> diagonal in inches as read from each panel's EDID.
    It is the one input the arrangement always needed and nothing supplied, and
    it ranks below a diagonal somebody typed deliberately -- see
    _effective_diagonal. Passing None or {} leaves every size exactly as it was.

    `obstacles` are the DEVICE rectangles this desk already has, as (x, y, w, h)
    in desk units -- openspan's canvas hands over its own device_rects(). A
    screen that comes back after being unplugged is placed from Windows, and
    Windows has never heard of the iPad the user parked beside the primary, so
    without them the returning screen is drawn straight through it. With them
    the whole block steps clear. None (the default) behaves exactly as this
    always did, which keeps every existing caller correct.

    Returns (monitors, report). The report names what actually changed, so the
    caller can say so instead of silently rewriting the desk.
    """
    saved_by_name = {
        str(row.get("name", "")): row
        for row in (saved_monitors or []) if isinstance(row, dict)
    }
    live_names = [str(row.get("name", "")) for row in (live_monitors or [])]
    report = {"added": [], "removed": [], "resolution": [], "refresh": [],
              "primary": [], "resized": []}
    merged = []
    for live in (live_monitors or []):
        name = str(live.get("name", ""))
        saved = saved_by_name.get(name)
        if saved is None:
            report["added"].append(name)
            merged.append(_normalize_monitor(live, None, sizes))
            continue
        old_res = (int(saved.get("w", 0)), int(saved.get("h", 0)))
        new_res = (int(live.get("w", 0)), int(live.get("h", 0)))
        if old_res != new_res:
            report["resolution"].append(
                (name, f"{old_res[0]}x{old_res[1]}",
                 f"{new_res[0]}x{new_res[1]}"))
        old_hz = saved.get("refresh_hz")
        new_hz = live.get("refresh_hz")
        if new_hz and float(new_hz) != float(old_hz or 0):
            report["refresh"].append((name, old_hz, float(new_hz)))
        if bool(saved.get("primary")) != bool(live.get("primary")):
            report["primary"].append(name)
        # Only the human's fields are carried over. Everything else in `saved`
        # is Windows' and is now stale by definition.
        keep = {}
        for field in ("layout_x", "layout_y", "diagonal_in", "refresh_hz",
                      "diagonal_source"):
            if saved.get(field) is not None:
                keep[field] = saved[field]
        # The derived rectangle survives ONLY when there is no diagonal to
        # re-derive it from. See the docstring: with a diagonal these are
        # recomputed by _normalize_monitor and preserving them here would
        # freeze the drawing at the old resolution; without one, letting them
        # fall back to live w/h is a silent geometry loss.
        if not keep.get("diagonal_in"):
            for field in ("layout_w", "layout_h"):
                if saved.get(field) is not None:
                    keep[field] = saved[field]
        row = _normalize_monitor(live, keep, sizes)
        # A screen drawn 8% smaller because its EDID was finally read is a
        # change to the picture and is reported like one. Silently redrawing it
        # would be the same fault as silently drawing the desk a screen short.
        if _diagonal_changed(saved.get("diagonal_in"), row.get("diagonal_in")):
            report["resized"].append(
                (name, saved.get("diagonal_in"), row.get("diagonal_in")))
        merged.append(row)
    for name in saved_by_name:
        if name not in live_names:
            report["removed"].append(name)
    # The PC's screens are re-arranged from Windows on every re-read, for the
    # same reason they are on every load: Windows is the authority on where its
    # own screens are, and this is the moment its answer just changed. The
    # block as a whole stays where the user put it -- see _saved_block_anchor --
    # unless staying there would put it on a device, which only the caller
    # knows about.
    merged = pc_block_layout(merged, _saved_block_anchor(merged, saved_by_name),
                             obstacles=obstacles)
    return merged, report


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


_LAST_NORMALIZE_RESIZED = []


def last_normalize_report():
    """What the last normalize_config() call RESIZED: [(name, old, new), ...].

    A screen redrawn because its EDID was finally read is a change to the
    picture, and the merge path has always reported it. Loading a config does
    the same resizing and reported nothing, so a launch could redraw a screen
    smaller in silence.

    It is reported HERE rather than in the returned config because that config
    is written back to disk verbatim. normalize_config's result is the config
    itself; openspan's adopt() copies any top-level key it does not recognise
    straight through, and _persist json.dumps the whole thing into
    openspan_config.json and into every saved arrangement. A "_resized" key
    would therefore leak into the file and outlive the load it described.

    Module state, so it describes the LAST call in this process and nothing
    else: read it immediately after the call whose answer you want. Cleared at
    the start of every call, so a call that raises leaves no stale answer
    looking current, and a copy comes back so a reader cannot rewrite what the
    next one sees.
    """
    return list(_LAST_NORMALIZE_RESIZED)


def normalize_config(raw, live_monitors, sizes=None):
    """Return a complete v2 config while preserving every v1 iPad setting.

    `sizes` maps GDI name -> diagonal in inches from each panel's EDID; a
    diagonal the user typed still outranks it. None or {} changes nothing.
    Any screen this resizes is named in last_normalize_report()."""
    global _LAST_NORMALIZE_RESIZED
    _LAST_NORMALIZE_RESIZED = []
    if not live_monitors:
        raise ValueError("at least one Windows monitor is required")
    raw = raw if isinstance(raw, dict) else {}
    saved_monitors = {
        str(row.get("name", "")): row
        for row in raw.get("monitors", [])
        if isinstance(row, dict)
    }
    monitors = []
    resized = []
    for row in live_monitors:
        saved = saved_monitors.get(str(row.get("name", "")))
        monitor = _normalize_monitor(row, saved, sizes)
        if _diagonal_changed((saved or {}).get("diagonal_in"),
                             monitor.get("diagonal_in")):
            resized.append((str(row.get("name", "")),
                            (saved or {}).get("diagonal_in"),
                            monitor.get("diagonal_in")))
        monitors.append(monitor)
    _LAST_NORMALIZE_RESIZED = resized

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
            # For a device that already remaps its own modifiers, ours is a
            # second translation in series and the two fight.
            "keyboard_verbatim": bool(target.get("keyboard_verbatim", False)),
            # None = inherit the global keymap (today's behaviour); a dict --
            # even an empty one -- is an explicit per-device override, which is
            # how a Mac gets physical Alt delivered as Option instead of Cmd.
            "modifier_remap": (
                dict(target["modifier_remap"])
                if isinstance(target.get("modifier_remap"), dict) else None),
            "displays": displays,
        })

    # The PC's own screens are arranged in Windows Display Settings, so their
    # positions relative to each other are re-derived from Windows on every
    # load rather than read back from the file. Only where the block SITS among
    # the devices is remembered -- see pc_block_layout and _saved_block_anchor.
    #
    # It runs HERE, after the devices, because it needs to know where they are.
    # It used to run before them and could only see the PC, so loading a saved
    # arrangement whose primary had since changed re-derived the block straight
    # through a device screen: the "Mac 2k 2" arrangement put DISPLAY4 on top
    # of the Mac's third display and the desk lost five of its six portals. The
    # devices are DATA to this -- their rectangles are read, never moved --
    # and it still runs before the one-time snap below, so a device that snaps
    # to a monitor snaps to where that monitor actually ends up.
    monitors = pc_block_layout(
        monitors, _saved_block_anchor(monitors, saved_monitors),
        obstacles=[(display["x"], display["y"], display["w"], display["h"])
                   for device in devices if device.get("enabled", True)
                   for display in device.get("displays", [])])

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


def rects_overlap(a: tuple, b: tuple, tolerance: float = 2.0) -> bool:
    """True when two desk rectangles genuinely cover each other.

    Both are (x, y, w, h) in desk units. Edge contact is NOT overlap, and that
    distinction is the whole arrangement: every portal in this app exists
    because two rectangles touch, and compute_portals matches an edge to within
    2 units. So the same tolerance is spent here -- rectangles have to cross it
    on BOTH axes before this says yes, which is what lets a screen be parked
    flush against its neighbour without the drop being refused."""
    ax, ay, aw, ah = (float(value) for value in a)
    bx, by, bw, bh = (float(value) for value in b)
    across = min(ax + aw, bx + bw) - max(ax, bx)
    down = min(ay + ah, by + bh) - max(ay, by)
    return across > tolerance and down > tolerance


def _surface_key(surface):
    """The (kind, owner, id) tuple the canvas names this surface by."""
    if surface["kind"] == "local":
        return ("local", "windows", surface["monitor"])
    return ("target", surface["target"], surface["display"])


def overlapping_surfaces(config, key, rect,
                         tolerance: float = 2.0) -> list[str]:
    """Names of every OTHER surface that `rect` would land on top of.

    `key` is the canvas's own name for the surface being moved: ("local",
    "windows", monitor_name) for a PC screen and ("target", device_id,
    display_id) for a device's, exactly the tuples MultiArrangeCanvas._items()
    yields, so a caller can hand its current selection straight through. A key
    that names nothing still works -- the rectangle is then compared against
    every surface on the desk -- and the surface it names is always excluded,
    which is what makes this safe to call with a rectangle mid-drag.

    Empty means the drop is legal. The names come back exactly as
    layout_surfaces spells them, so a refusal can say what was in the way."""
    key = tuple(key) if key else None
    hits = []
    for surface in layout_surfaces(config):
        if key is not None and _surface_key(surface) == key:
            continue
        if rects_overlap(rect, surface["rect"], tolerance):
            hits.append(surface["name"])
    return hits


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
            bool(device.get("keyboard_verbatim")),
            device.get("modifier_remap"),
            [[d.get("id"), d.get("x"), d.get("y"), d.get("w"), d.get("h"),
              d.get("res_w"), d.get("res_h"), d.get("rotation")]
             for d in device.get("displays", [])],
        ])
    # The computed portals and links are DERIVED from exactly the rectangles
    # above, so including them would add nothing but the display names they
    # carry -- and a rename would then drop the portal's hooks mid-use.
    return json.dumps(
        [monitors, devices, bool(config.get("cross_requires_side_button")),
         bool(config.get("side_button_jumps_nearest"))],
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
