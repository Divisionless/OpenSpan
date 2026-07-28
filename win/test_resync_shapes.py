"""A device's pointer must be findable whatever shape its screens make.

The whole position model rests on one claim: that a sequence of blind shoves
ends in ONE place no matter where the pointer started. That claim is not
obviously true. Shoving maps every possible position onto a boundary, and for
a shape with a symmetry -- a plus, a T -- two positions can map onto each other
forever and never collapse.

So the sequence is SEARCHED from the rectangles rather than assumed, and this
file checks the search against arrangements nobody would design on purpose:
gaps, staircases, crosses, a tall screen beside a short one. Users rearrange
monitors however they like; none of this may depend on them being tidy.

Each shape is verified END TO END -- the actual HID reports the portal would
queue are walked forward against an independent model of how a pointer clamps,
from a dense grid of starting positions.

Exit 0 = all pass.
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openspan_portal as P  # noqa: E402

fails = []
U = 1000        # one screen unit: desk units AND pixels, so 1 desk = 1 px


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (
        "" if cond or not detail else "\n      " + detail))
    if not cond:
        fails.append(name)


def broker_for(rects):
    """A Portal stub whose only knowledge is a list of rectangles."""
    portal = P.Portal.__new__(P.Portal)
    portal._displays = {
        ("dev", f"s{index}"): {
            "id": f"s{index}", "name": f"Screen {index}",
            "x": x, "y": y, "w": w, "h": h,
            "res_w": w, "res_h": h, "rotation": 0, "refresh_hz": 60,
        }
        for index, (x, y, w, h) in enumerate(rects)
    }
    portal._resync_plans = {}
    portal._last_seen = {}
    portal._device_gain = {"dev": 1.0}
    portal._device_compensate = {"dev": False}
    portal.q = __import__("queue").Queue()
    portal.active_display = None
    return portal


def land(portal, start, reports):
    """Where a pointer starting HERE ends after these reports.

    Independent of the planner: walks a pixel at a time and clamps each axis on
    its own, which is what a pointer does at the edge of a screen."""
    x, y = float(start[0]), float(start[1])
    for dx, dy in reports:
        steps = max(1, int(abs(dx) + abs(dy)))
        for _ in range(steps):
            step_x, step_y = dx / steps, dy / steps
            if portal._display_at("dev", x + step_x, y + step_y):
                x, y = x + step_x, y + step_y
            elif step_x and portal._display_at("dev", x + step_x, y):
                x += step_x
            elif step_y and portal._display_at("dev", x, y + step_y):
                y += step_y
    return x, y


SHAPES = {
    "one screen": [(0, 0, U, U)],
    "a row of three": [(0, 0, U, U), (U, 0, U, U), (2 * U, 0, U, U)],
    "an L": [(0, 0, U, 2 * U), (U, U, U, U), (2 * U, U, U, U)],
    "Doug's Mac -- two tall portraits, one short landscape, centred":
        [(0, 0, U, 3 * U), (U, 0, U, 3 * U), (2 * U, U, 3 * U, U)],
    "a T": [(0, 0, U, U), (U, 0, U, U), (2 * U, 0, U, U), (U, U, U, U)],
    "a staircase": [(0, 0, U, U), (U, U, U, U), (2 * U, 2 * U, U, U)],
    "a plus": [(U, 0, U, U), (0, U, U, U), (U, U, U, U),
               (2 * U, U, U, U), (U, 2 * U, U, U)],
    "a Z": [(0, 0, 2 * U, U), (U, U, 2 * U, U), (2 * U, 2 * U, 2 * U, U)],
    "portrait tower beside a wide short one":
        [(0, 0, U, 4 * U), (U, 3 * U, 4 * U, U)],
    "two screens meeting only at a corner":
        [(0, 0, U, U), (U, U, U, U)],
}

print("Every arrangement must collapse to ONE position from anywhere.\n")
for label, rects in SHAPES.items():
    portal = broker_for(rects)
    plan = portal._resync_plan("dev")
    if plan is None:
        check(f"{label}: a re-sync plan exists", False,
              "no sequence of shoves collapses this shape")
        continue
    sides, claimed = plan
    while not portal.q.empty():
        portal.q.get_nowait()
    portal._resync("dev")
    reports = []
    while not portal.q.empty():
        _who, kind, a, b, _c = portal.q.get_nowait()
        if kind == "m":
            reports.append((a, b))
    starts = []
    for x, y, w, h in rects:
        for fx in (0.03, 0.25, 0.5, 0.75, 0.97):
            for fy in (0.03, 0.25, 0.5, 0.75, 0.97):
                starts.append((x + w * fx, y + h * fy))
    worst = 0.0
    for start in starts:
        got = land(portal, start, reports)
        worst = max(worst, abs(got[0] - claimed[0]), abs(got[1] - claimed[1]))
    check(f"{label}: {len(starts)} starts -> one place "
          f"[{' then '.join(sides)}]",
          worst <= 2.0, f"worst miss {worst:.1f} units from the claim")

# A shape that genuinely cannot be pinned must SAY SO, not invent an answer.
# Two screens that touch at nothing -- a pointer cannot travel between them, so
# no shove can bring the two halves together.
islands = broker_for([(0, 0, U, U), (5 * U, 5 * U, U, U)])
check("an impossible arrangement is reported, not guessed",
      islands._resync_plan("dev") is None)

print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
