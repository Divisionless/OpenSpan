"""Property checks on the portal's position model. No VM, no radio, no Bluetooth.

These are the tests that would have caught the crossing bug the user reported as
"when i move over to the mac, it doesn't start at the edge, and then it returns
from that same false point to the PC, so i can't mouse over the right section at
all". Every one of them fails on the build that shipped that behaviour.

They assert THE RULE the portal now obeys -- the model may only change position
by an amount the wire also moved -- plus the two geometric properties that make
a relative HID link usable: an arrival never lands on a trigger, and crossing a
seam is reversible.

Run against the LIVE config when one is present, because a desk the user
actually sits at is the only layout that has ever found one of these. The
properties are layout-independent, so this stays honest if he rearranges.

Exit 0 = all pass.
"""
import math
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openspan_portal as P  # noqa: E402
from openspan_targets import (  # noqa: E402
    ARRIVE_MARGIN, compute_adjacencies, oriented_resolution,
)

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (
        "" if cond or not detail else "\n      " + detail))
    if not cond:
        fails.append(name)


class _NoCursor:
    """enter() re-centres the real Windows pointer. Never do that from a test --
    the app may well be running on this desk right now."""

    @staticmethod
    def SetCursorPos(*_args):
        return 1


def build():
    """A Portal wired to the live config with every side effect stubbed out."""
    cfg, portals = P.load_portals()
    broker = P.Portal.__new__(P.Portal)
    broker.cfg = cfg
    broker.portals = portals
    broker.links = compute_adjacencies(cfg)
    broker._displays = {
        (device["id"], display["id"]): display
        for device in cfg.get("devices", [])
        if device.get("enabled", True)
        for display in device.get("displays", [])
    }
    broker._monitors = {m["name"]: m for m in cfg.get("monitors", [])}
    primary = next((m for m in cfg["monitors"] if m.get("primary")),
                   cfg["monitors"][0])
    broker.cx = primary["x"] + primary["w"] // 2
    broker.cy = primary["y"] + primary["h"] // 2
    devices = [d for d in cfg.get("devices", []) if d.get("enabled", True)]
    broker._target_ports = {d["id"]: d.get("port", 9955) for d in devices}
    broker._clipboard_devices = {d["id"]: False for d in devices}
    broker._device_gain = {
        d["id"]: float(d.get("pointer_gain", 1.0)) for d in devices}
    broker._device_accel = {d["id"]: 0.0 for d in devices}
    broker._device_sens = {d["id"]: 1.0 for d in devices}
    broker._device_compensate = {
        d["id"]: bool(d.get("compensate_target_accel", False))
        for d in devices}
    broker.target_ready = {d["id"]: True for d in devices}
    broker._last_seen = {}
    broker.q = __import__("queue").Queue()
    broker.socks = {}
    broker.active = False
    broker.cur = None
    broker.active_target = None
    broker.active_display = None
    broker.vx = broker.vy = 0.0
    broker.mods = 0
    broker.raw_keys = {}
    broker.buttons = 0
    broker.overrides = []
    broker.remap = {}
    broker._chord_until = 0.0
    broker._last_sync_seq = -1
    broker._esc_hist = []
    broker.entry_along = 0
    broker.perp = 0
    broker._rem_x = broker._rem_y = 0.0
    broker._emit_kbd = lambda: None
    return broker


def drain_reports(broker, target):
    """The raw HID motion reports queued for one device."""
    out = []
    while not broker.q.empty():
        who, kind, a, b, _c = broker.q.get_nowait()
        if kind == "m" and who == target:
            out.append((a, b))
    return out


def simulate(broker, target, start_x, start_y, reports):
    """Where the TARGET's own pointer ends up if it obeys these reports.

    Deliberately not the warp's arithmetic: this walks the reports forward the
    way the device will, converting pixels to desk units at whatever screen the
    pointer is standing on at that moment (they differ -- a 32" 4K portrait
    panel and a 32" 1440p landscape one are not the same pixels per inch), and
    applying the target's own acceleration curve where it is in play."""
    x, y = float(start_x), float(start_y)
    compensating = broker._device_compensate.get(target)
    gain = float(broker._device_gain.get(target, 1.0)) or 1.0
    for a, b in reports:
        if compensating:
            length = math.hypot(a, b)
            travelled = P.apple_pixels(length)
            px = 0.0 if length == 0 else a / length * travelled
            py = 0.0 if length == 0 else b / length * travelled
        else:
            px, py = float(a), float(b)
        for _ in range(8):          # sub-step so one report may cross a seam
            display = broker._display_at(target, x, y)
            if not display:
                break
            res_w, res_h = oriented_resolution(display)
            x += px / 8.0 * gain * float(display["w"]) / max(1.0, float(res_w))
            y += py / 8.0 * gain * float(display["h"]) / max(1.0, float(res_h))
    return x, y


def scales(broker, target, display):
    res_w, res_h = oriented_resolution(display)
    gain = float(broker._device_gain.get(target, 1.0)) or 1.0
    return (gain * float(display["w"]) / max(1.0, float(res_w)),
            gain * float(display["h"]) / max(1.0, float(res_h)))


def margins(display):
    return (min(float(ARRIVE_MARGIN), max(2.0, float(display["w"]) * 0.1)),
            min(float(ARRIVE_MARGIN), max(2.0, float(display["h"]) * 0.1)))


P.user32 = _NoCursor()
broker = build()

if not broker._displays:
    print("no configured devices -- nothing to check")
    sys.exit(0)

print(f"live layout: {len(broker._displays)} screens across "
      f"{len({d for d, _ in broker._displays})} devices, "
      f"{len(broker.links)} links, {len(broker.portals)} PC entrances\n")


# =========================================================================
# P1. NO DISCARDED MOTION.
#
# The single invariant that flags an edge-pressure accumulator, a transition
# cooldown, a sender that re-sums per-report deltas, and a direction error --
# all four at once. Motion handed to _route_motion is motion the wire has
# ALREADY carried; if the model does not take all of it, the model and the
# target's real pointer drift apart with no mechanism to converge again.
# =========================================================================
worst = ("", 0.0)
for (target, display_id), display in broker._displays.items():
    scale_x, scale_y = scales(broker, target, display)
    margin_x, margin_y = margins(display)
    broker.active = True
    broker.active_target, broker.active_display = target, display_id
    # start well inside, and walk a distance that cannot reach any edge
    broker.vx = float(display["x"]) + float(display["w"]) / 2
    broker.vy = float(display["y"]) + float(display["h"]) / 2
    start = (broker.vx, broker.vy)
    commanded = 0.0
    for step in range(200):
        dx = 3 if step % 2 else -1        # net +1 per pair, never near an edge
        broker._route_motion(dx, 0)
        commanded += dx * scale_x
    moved = broker.vx - start[0]
    error = abs(moved - commanded)
    if error > worst[1]:
        worst = (f"{target}/{display_id}", error)
check("the model takes ALL the motion the wire carried (no gate eats any)",
      worst[1] < 1e-6,
      f"worst drift {worst[1]:.3f} desk units on {worst[0]}")

# and a single sustained lean must actually cross, not stall against a gate
stalled = []
for (target, display_id), display in broker._displays.items():
    for side, (dx, dy) in (("left", (-40, 0)), ("right", (40, 0)),
                           ("top", (0, -40)), ("bottom", (0, 40))):
        broker.active = True
        broker.active_target, broker.active_display = target, display_id
        broker.vx = float(display["x"]) + float(display["w"]) / 2
        broker.vy = float(display["y"]) + float(display["h"]) / 2
        broker._last_seen = {}
        # An outer edge of the desk has nothing beyond it and SHOULD stop the
        # pointer. The defect was an edge that has a neighbour over only part
        # of its length, where the rest was a silent unbounded wall.
        if not any(
                link["source"].get("kind") == "target"
                and link["source"].get("target") == target
                and link["source"].get("display") == display_id
                and link.get("side") == side
                for link in broker.links):
            continue
        moved_off = False
        for _ in range(400):
            if broker._route_motion(dx, dy):
                moved_off = True     # left to the PC
                break
            if (broker.active_target, broker.active_display) != (
                    target, display_id):
                moved_off = True     # handed to another surface
                break
        while not broker.q.empty():
            broker.q.get_nowait()
        if not moved_off:
            stalled.append(f"{target}/{display_id} {side}")
check("no edge is a silent wall -- every sustained lean leads somewhere",
      not stalled, "stalled at: " + ", ".join(stalled[:6]))


# =========================================================================
# P2. AN ARRIVAL LANDS AT THE EDGE CROSSED, AND NEVER ON A TRIGGER.
#
# Planted with a stale position saved on a DIFFERENT edge -- and on a different
# screen of the same device -- because that is exactly the state the reported
# bug was entered from.
# =========================================================================
bad_edge, on_trigger, unpaid = [], [], []
for portal in broker.portals:
    target = portal.get("target")
    display_id = portal.get("target_display")
    display = broker._displays.get((target, display_id))
    if not display:
        continue
    x, y = float(display["x"]), float(display["y"])
    width, height = float(display["w"]), float(display["h"])
    margin_x, margin_y = margins(display)
    scale_x, scale_y = scales(broker, target, display)
    lo, hi = portal["span"]
    plants = [None]
    # every corner of this screen, plus a point on every OTHER screen of the
    # same device -- the stale states a real session actually produces
    plants += [(display_id, x, y), (display_id, x + width, y + height),
               (display_id, x + width, y), (display_id, x, y + height)]
    plants += [(other_id, float(other["x"]) + float(other["w"]) / 2,
                float(other["y"]) + float(other["h"]) / 2)
               for (dev, other_id), other in broker._displays.items()
               if dev == target and other_id != display_id]
    for plant in plants:
        for index in range(9):
            along = lo + (hi - lo) * index / 8.0
            broker.active = False
            broker._last_seen = {} if plant is None else {target: plant}
            while not broker.q.empty():
                broker.q.get_nowait()
            broker.enter(portal, along)
            edge = portal.get("edge")
            # -- lands the arrival margin inside the edge NAMED BY THE PORTAL
            if edge == "target-left":
                depth = broker.vx - x
            elif edge == "target-right":
                depth = (x + width) - broker.vx
            elif edge == "target-top":
                depth = broker.vy - y
            else:
                depth = (y + height) - broker.vy
            if not (margin_x * 0.5 <= depth <= 2.0 * max(margin_x, margin_y)):
                bad_edge.append(f"{target}/{display_id} {edge} depth={depth:.0f}")
            # -- and is not sitting on any OTHER live crossing of this screen
            for link in broker.links:
                source = link["source"]
                if source.get("kind") != "target" \
                        or source.get("target") != target \
                        or source.get("display") != display_id:
                    continue
                side = link.get("side")
                distance = {
                    "left": broker.vx - x, "right": (x + width) - broker.vx,
                    "top": broker.vy - y, "bottom": (y + height) - broker.vy,
                }[side]
                span_lo, span_hi = link["span"]
                position = broker.vy if side in ("left", "right") else broker.vx
                if distance < 1.0 and span_lo <= position <= span_hi:
                    on_trigger.append(f"{target}/{display_id} on {side}")
            # -- and the jump from a known position was PAID FOR on the wire
            if plant is not None:
                reports = drain_reports(broker, target)
                landed = simulate(broker, target, plant[1], plant[2], reports)
                miss = math.hypot(landed[0] - broker.vx,
                                  landed[1] - broker.vy)
                jump = math.hypot(broker.vx - plant[1], broker.vy - plant[2])
                # one report is worth up to ~190 target px on a compensated
                # device; allow that plus a little for the seam sub-stepping
                if miss > max(2.0 * ARRIVE_MARGIN, jump * 0.03):
                    unpaid.append(
                        f"{target}/{display_id} pointer lands {miss:.0f} desk "
                        f"units from the model after a {jump:.0f}-unit jump")

check("entry lands at the edge the portal names, whatever was remembered",
      not bad_edge, "; ".join(sorted(set(bad_edge))[:4]))
check("an arrival never lands on another live crossing",
      not on_trigger, "; ".join(sorted(set(on_trigger))[:4]))
check("a model jump is always paid for in HID reports",
      not unpaid, "; ".join(sorted(set(unpaid))[:4]))


# =========================================================================
# P3. CROSSING A SEAM IS REVERSIBLE.
#
# Proportional arrival stretched the overlap across the destination's whole
# edge, so a round trip did not return you where you started -- and a large
# share of crossings landed in a band with no link back, which is what made a
# whole screen unreachable.
# =========================================================================
irreversible, stranded = [], []
for link in broker.links:
    source, destination = link["source"], link["destination"]
    if source.get("kind") != "target" or destination.get("kind") != "target":
        continue
    display = broker._displays.get(
        (source.get("target"), source.get("display")))
    if not display:
        continue
    lo, hi = link["span"]
    # Sweep only where a crossing can actually fire AND land: corners are
    # deliberately dead now, at both ends and on both surfaces, so that they
    # can be USED. Crossing at the very end of an overlap therefore lands a
    # corner-zone inside the neighbour, which is the rule working, not drift.
    landing = broker._displays.get(
        (destination.get("target"), destination.get("display")))
    if not landing:
        continue
    safe_lo, safe_hi = broker._corner_safe_span(display, link["side"])
    land_lo, land_hi = broker._corner_safe_span(landing, link["to_side"])
    lo = max(lo, safe_lo, land_lo)
    hi = min(hi, safe_hi, land_hi)
    if hi <= lo:
        continue
    for index in range(11):
        along = lo + (hi - lo) * index / 10.0
        broker.active = True
        broker._last_seen = {}
        broker.active_target = source.get("target")
        broker.active_display = source.get("display")
        broker.vx = float(display["x"]) + float(display["w"]) / 2
        broker.vy = float(display["y"]) + float(display["h"]) / 2
        if link["side"] in ("left", "right"):
            broker.vy = along
        else:
            broker.vx = along
        broker._switch_target(destination, link["to_side"], along)
        landed = (broker.vx, broker.vy)
        back = broker._matching_link(link["to_side"],
                                     landed[1] if link["to_side"] in
                                     ("left", "right") else landed[0])
        while not broker.q.empty():
            broker.q.get_nowait()
        if back is None:
            stranded.append(
                f"{source.get('display')} -> {destination.get('display')}"
                f" at {along:.0f}")
            continue
        broker._switch_target(back["destination"], back["to_side"],
                              landed[1] if link["to_side"] in ("left", "right")
                              else landed[0])
        returned = broker.vy if link["side"] in ("left", "right") else broker.vx
        if abs(returned - along) > 2.0 * ARRIVE_MARGIN:
            irreversible.append(
                f"{source.get('display')} at {along:.0f} came back "
                f"at {returned:.0f}")
        while not broker.q.empty():
            broker.q.get_nowait()

check("crossing a seam and coming straight back returns you where you were",
      not irreversible, "; ".join(irreversible[:4]))
check("no crossing lands in a band with no route back",
      not stranded, "; ".join(stranded[:4]))


# =========================================================================
# P4. EVERY SCREEN IS REACHABLE FROM THE PC.
#
# "i can't mouse over the right section at all" is, in the end, a graph
# question -- so ask it as one instead of discovering it by hand.
# =========================================================================
reachable = {(p.get("target"), p.get("target_display"))
             for p in broker.portals}
frontier = list(reachable)
while frontier:
    node = frontier.pop()
    for link in broker.links:
        source, destination = link["source"], link["destination"]
        if source.get("kind") != "target" or destination.get("kind") != "target":
            continue
        if (source.get("target"), source.get("display")) != node:
            continue
        nxt = (destination.get("target"), destination.get("display"))
        if nxt not in reachable:
            reachable.add(nxt)
            frontier.append(nxt)
missing = sorted(set(broker._displays) - reachable)
check("every configured screen is reachable from a PC entrance",
      not missing, "unreachable: " + ", ".join(f"{a}/{b}" for a, b in missing))

# =========================================================================
# P5. A RE-SYNC CONVERGES FROM ANYWHERE, WHATEVER THE SHAPE.
#
# A device's screens rarely form a rectangle: a shorter panel beside two taller
# ones makes the union an L. On an L, the boundary you reach by shoving one way
# DEPENDS on the other axis -- so a single shove establishes one of several
# positions, not one, and nothing can tell which. That is how a pointer ended up
# two screens away from the model. The re-sync must therefore land in the SAME
# place no matter where it started, or it has established nothing.
# =========================================================================
def simulate_clamped(target, start_x, start_y, reports):
    """Like simulate(), but the device CLAMPS at the edge of its own union --
    which is the entire mechanism a shove relies on."""
    x, y = float(start_x), float(start_y)
    compensating = broker._device_compensate.get(target)
    gain = float(broker._device_gain.get(target, 1.0)) or 1.0
    for a, b in reports:
        if compensating:
            length = math.hypot(a, b)
            travelled = P.apple_pixels(length)
            px = 0.0 if length == 0 else a / length * travelled
            py = 0.0 if length == 0 else b / length * travelled
        else:
            px, py = float(a), float(b)
        # ~1 px per sub-step: a shove is ONE huge report, and walking it coarsely
        # stops short of the clamp by up to a whole sub-step, which looks like a
        # convergence failure that is really just the simulation's resolution.
        steps = max(32, int(abs(px) + abs(py)))
        for _ in range(steps):
            display = broker._display_at(target, x, y)
            if not display:
                break
            res_w, res_h = oriented_resolution(display)
            step_x = px / steps * gain * float(display["w"]) / max(1.0, float(res_w))
            step_y = py / steps * gain * float(display["h"]) / max(1.0, float(res_h))
            if broker._display_at(target, x + step_x, y + step_y):
                x, y = x + step_x, y + step_y
            elif broker._display_at(target, x + step_x, y):
                x += step_x                      # slide along the boundary
            elif broker._display_at(target, x, y + step_y):
                y += step_y
    return x, y


diverged = []
for target in sorted({device for device, _display in broker._displays}):
    starts = []
    for (device, _display_id), display in broker._displays.items():
        if device != target:
            continue
        x, y = float(display["x"]), float(display["y"])
        w, h = float(display["w"]), float(display["h"])
        starts += [(x + w / 2, y + h / 2), (x + 5, y + 5),
                   (x + w - 5, y + h - 5), (x + 5, y + h - 5)]
    broker._last_seen = {}
    while not broker.q.empty():
        broker.q.get_nowait()
    claimed = broker._resync(target)
    reports = drain_reports(broker, target)
    if claimed is None:
        continue
    for start in starts:
        landed = simulate_clamped(target, start[0], start[1], reports)
        if math.hypot(landed[0] - claimed[0], landed[1] - claimed[1]) > 2.0:
            diverged.append(
                f"{target}: from ({start[0]:.0f},{start[1]:.0f}) landed "
                f"({landed[0]:.0f},{landed[1]:.0f}), claimed "
                f"({claimed[0]:.0f},{claimed[1]:.0f})")

check("a cold re-sync lands in the same place no matter where it started",
      not diverged, "; ".join(sorted(set(diverged))[:4]))


# =========================================================================
# P6. A CORNER IS A PLACE YOU CAN USE.
#
# Corners hold the things people reach for -- Start, Show Desktop, a close box,
# a hot corner -- and a crossing that fires there takes the pointer away
# mid-reach. A corner is also where a crossing is least trustworthy: a diagonal
# satisfies TWO edges at once, so which surface you land on comes down to
# whichever overshoot happened to be larger on that report. Both problems go
# away by not crossing there at all.
# =========================================================================
escaped = []
for (target, display_id), display in broker._displays.items():
    x, y = float(display["x"]), float(display["y"])
    w, h = float(display["w"]), float(display["h"])
    for corner_x, push_x in ((x, -60), (x + w, 60)):
        for corner_y, push_y in ((y, -60), (y + h, 60)):
            for probe in (0.0, 8.0, 20.0):
                broker.active = True
                broker._last_seen = {}
                broker.active_target, broker.active_display = target, display_id
                broker.vx = corner_x - (push_x / abs(push_x)) * probe
                broker.vy = corner_y - (push_y / abs(push_y)) * probe
                for _ in range(30):
                    if broker._route_motion(push_x, push_y):
                        escaped.append(
                            f"{target}/{display_id} corner "
                            f"({corner_x:.0f},{corner_y:.0f}) escaped to the PC")
                        break
                    if (broker.active_target, broker.active_display) != (
                            target, display_id):
                        escaped.append(
                            f"{target}/{display_id} corner "
                            f"({corner_x:.0f},{corner_y:.0f}) escaped to "
                            f"{broker.active_display}")
                        break
                while not broker.q.empty():
                    broker.q.get_nowait()

check("driving hard into a corner never crosses anywhere",
      not escaped, "; ".join(sorted(set(escaped))[:4]))


# =========================================================================
# P7. NO CROSSING EVER PAYS FOR A RE-SYNC.
#
# A re-sync ends at a corner of the arrangement. If it happens because someone
# crossed, that crossing then has to carry the pointer all the way back -- on
# this desk about 110 reports, a second and a half of the pointer sailing across
# two screens while they watch. It is correct and it is horrible, and the answer
# is to do it when the lane comes up instead, while nobody is looking.
# =========================================================================
expensive = []
for portal in broker.portals:
    target = portal.get("target")
    lo, hi = portal["span"]
    broker._last_seen = {}
    broker.active = False
    while not broker.q.empty():
        broker.q.get_nowait()
    broker._park_at_door(target)          # what the lane coming up now does
    parked = len(drain_reports(broker, target))
    broker.enter(portal, (lo + hi) / 2.0)
    crossing = len(drain_reports(broker, target))
    if crossing > 32:
        expensive.append(
            f"{portal.get('target_name', target)} via {portal['axis']}"
            f"={portal['line']}: {crossing} reports "
            f"(parking cost {parked}, which nobody waits for)")

check("a crossing after the lane came up is cheap -- the re-sync already ran",
      not expensive, "; ".join(expensive[:4]))

print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
