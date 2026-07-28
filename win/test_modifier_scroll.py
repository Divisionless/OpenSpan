"""Held modifier + scroll wheel must reach the ACTIVE device.

Drives the REAL Portal hook procedures with synthetic Windows structures --
no VM, no radio, no Bluetooth. Reproduces the exact reported gesture:

    hold Ctrl  ->  cross the edge onto a device  ->  scroll

Before the fix that arrived as a BARE wheel with no modifier, because enter()
never announced the already-held modifier. A device with the clipboard
capability was repaired ~0.35s later purely as a side effect of the clipboard
chord resync, which is why the fault only showed on a device without it.

Exit 0 = all pass.
"""
import os
import sys
import types

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openspan_portal as P  # noqa: E402

fails = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


VK_CTRL = 0xA2          # left control
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101


def make_portal(clipboard_capable):
    """A Portal with the hooks/sockets stubbed, one device, one entrance."""
    p = P.Portal.__new__(P.Portal)
    p.cfg = {"devices": [], "monitors": []}
    p.portals = [{
        "target": "dev", "target_name": "Dev", "target_display": "dev-1",
        "daemon_port": 9955, "axis": "x", "line": -1, "span": (0, 1000),
        "span_axis": "y", "sign": -1, "exit_to": (5, None),
        "monitor": "M",
    }]
    p.links = []
    # One 1000x800 screen at 1 desk unit per pixel, so a warp's arithmetic is
    # readable by eye. _place() needs the geometry: without it there is no way
    # to convert a desk jump into HID reports, and it correctly declines.
    p._displays = {("dev", "dev-1"): {
        "id": "dev-1", "name": "Dev", "x": 0, "y": 0, "w": 1000, "h": 800,
        "res_w": 1000, "res_h": 800, "rotation": 0, "refresh_hz": 60,
    }}
    p._monitors = {}
    p._target_ports = {"dev": 9955}
    p._clipboard_devices = {"dev": clipboard_capable}
    p.target_ready = {"dev": True}
    p.q = __import__("queue").Queue()
    p.socks = {}
    p.active = False
    p.cur = None
    p.active_target = None
    p.active_display = None
    p.vx = p.vy = 0.0
    p.mods = 0
    p.raw_keys = {}
    p.buttons = 0
    p.overrides = []
    p.remap = {}
    p._chord_until = 0.0
    p._last_sync_seq = -1
    p._esc_hist = []
    p.entry_along = 0
    p.perp = 0
    p.cx, p.cy = 500, 500
    p._last_seen = {}
    p._device_compensate = {"dev": False}
    p._device_gain = {}
    p._device_accel = {}
    p._device_sens = {}
    p._rem_x = p._rem_y = 0.0
    return p


def drain(p):
    """Everything the portal queued, as simple tuples."""
    out = []
    while not p.q.empty():
        out.append(p.q.get_nowait())
    return out


# --- the reported gesture, on a device WITHOUT the clipboard capability -----
p = make_portal(clipboard_capable=False)
p.mods = P.VK_MOD[VK_CTRL]          # Ctrl is already physically held
p.enter(p.portals[0], 100)          # cross the edge onto the device
p.q.put((p.active_target, "w", 0, 0, 1))   # scroll one notch

events = drain(p)
kbd = [e for e in events if e[1] == "k"]
wheel = [e for e in events if e[1] == "w"]
check("crossing onto a device announces the already-held modifier",
      bool(kbd) and kbd[0][2] == P.IPAD_MOD_BIT["ctrl"])
check("the scroll still reaches that device", bool(wheel))
check("the modifier is announced BEFORE the wheel",
      bool(kbd) and bool(wheel)
      and events.index(kbd[0]) < events.index(wheel[0]))

# --- and on a clipboard-capable device (must not regress) ------------------
p2 = make_portal(clipboard_capable=True)
p2.mods = P.VK_MOD[VK_CTRL]
p2.enter(p2.portals[0], 100)
kbd2 = [e for e in drain(p2) if e[1] == "k"]
check("a clipboard-capable device also gets the modifier on entry",
      bool(kbd2) and kbd2[0][2] == P.IPAD_MOD_BIT["ctrl"])

# --- out-and-back must not kill the modifier -------------------------------
# leave() used to zero the PHYSICAL mirror while the key was still down, so the
# later key-up cleared an already-clear bit and the modifier stayed dead.
p3 = make_portal(clipboard_capable=False)
p3.mods = P.VK_MOD[VK_CTRL]
p3.enter(p3.portals[0], 100)
drain(p3)
p3.leave()
check("leaving does not corrupt the physical modifier mirror",
      p3.mods == P.VK_MOD[VK_CTRL])
p3.enter(p3.portals[0], 100)
# the queue holds leave()'s explicit release (mods=0) and THEN the re-entry
# announcement, so the LAST keyboard report is the one the device ends up with
kbd3 = [e for e in drain(p3) if e[1] == "k"]
check("re-entering still announces the modifier (out-and-back survives)",
      bool(kbd3) and kbd3[-1][2] == P.IPAD_MOD_BIT["ctrl"])

# --- entry lands AT THE EDGE CROSSED, and the wire is told ------------------
# A relative HID link cannot move a target's cursor "to" a position -- so an
# earlier build stopped asserting one and restored the last saved position
# instead. That was worse: the saved point is the edge you LEFT by, which is a
# live exit trigger, and it is not the edge you just crossed. The fix is not to
# stop asserting, it is to MAKE THE ASSERTION TRUE: send the difference.
p4 = make_portal(clipboard_capable=False)
p4.enter(p4.portals[0], 100)
p4.active_display = "dev-1"
p4.vx, p4.vy = 640.0, 480.0          # where the user left that device
p4.leave()
check("leaving records the position ONCE PER DEVICE, not per display",
      p4._last_seen.get("dev") == ("dev-1", 640.0, 480.0))

entry_x, entry_y = p4._entry_point(p4.portals[0], 100)
drain(p4)
p4.enter(p4.portals[0], 100)
check("re-entering asserts the edge that was actually crossed",
      (p4.vx, p4.vy) == (entry_x, entry_y))
moves = [e for e in drain(p4) if e[1] == "m"]
check("and the jump is PAID FOR on the wire, not merely asserted",
      bool(moves))
check("the record follows the model, so the next warp starts from truth",
      p4._last_seen.get("dev") == ("dev-1", entry_x, entry_y))

p5 = make_portal(clipboard_capable=False)
p5.enter(p5.portals[0], 100)
entry5 = p5._entry_point(p5.portals[0], 100)
check("a first-ever entry still lands on the entry point",
      (p5.vx, p5.vy) == entry5)
# It used to assert a position and hope, because nothing was known to correct.
# Now it FINDS OUT: shove the pointer to a boundary the geometry says is
# unambiguous, so the warp that follows starts from a fact instead of nothing.
check("a first-ever entry RE-SYNCS rather than assuming",
      bool([e for e in drain(p5) if e[1] == "m"]))
check("and the device's position is known afterwards",
      p5._last_seen.get("dev") == ("dev-1", entry5[0], entry5[1]))

# --- our acceleration must be identical on the wire and in the model -------
# This is the whole reason acceleration belongs on THIS side: when the target
# OS accelerates, we cannot see what it did and the model drifts. When we do
# it, the same number drives the device and the virtual cursor.
def accel_factor(fx, fy, accel):
    mag = (fx * fx + fy * fy) ** 0.5
    if accel <= 0.0 or mag <= 0.0:
        return 1.0
    return min(P.ACCEL_MAX, 1.0 + accel * mag / P.ACCEL_PIVOT)


check("acceleration off is exactly linear", accel_factor(50, 0, 0.0) == 1.0)
check("a slow move is barely accelerated",
      1.0 < accel_factor(2, 0, 1.0) < 1.3)
check("a fast move is accelerated more than a slow one",
      accel_factor(60, 0, 1.0) > accel_factor(5, 0, 1.0))
check("acceleration is clamped so one report cannot explode",
      accel_factor(5000, 0, 4.0) == P.ACCEL_MAX)
check("acceleration is direction-independent (uses magnitude)",
      abs(accel_factor(30, 40, 1.0) - accel_factor(0, 50, 1.0)) < 1e-9)

# the sub-unit remainder must not throw away slow motion
p6 = make_portal(clipboard_capable=False)
p6._rem_x = p6._rem_y = 0.0
kept = 0.0
for _ in range(10):                    # ten 0.4-unit nudges = 4 units total
    fx = 0.4 + p6._rem_x
    dx = int(fx)
    p6._rem_x = fx - dx
    kept += dx
check("sub-unit movement accumulates instead of truncating to nothing",
      kept == 4.0)



# --- every physical click must reach the device, whatever the timing --------
# The button flag used to be a bare bool, and the button STATE was read again
# at send time. A click whose press and release fell inside one 8 ms tick was
# emitted once, carrying the post-release state -- the press never went out at
# all. Counting reports cannot see that; counting EDGES can.
def edges(sent):
    """Rising edges of the left button in what actually reached the wire."""
    count, held = 0, 0
    for msg in sent:
        state = msg.get("buttons", 0) & 1
        if state and not held:
            count += 1
        held = state
    return count


def click_run(*groups):
    """Drive button events through the real queue and the real batching.

    Each group is one sender tick's worth of hook events: 1 = press, 0 = release.
    """
    p = make_portal(clipboard_capable=False)
    p.active = True
    p.active_target = "dev"
    p.active_display = "dev-1"
    p.buttons = 0
    sent = []
    p.send = lambda target, msg: sent.append(msg)
    for group in groups:
        for pressed in group:
            p.buttons = (p.buttons | 1) if pressed else (p.buttons & ~1)
            p.q.put(("dev", "b", p.buttons, 0, 0))
        p._flush_queue()
    return sent


check("a click spread over two ticks is delivered",
      edges(click_run([1], [0])) == 1)
check("a click that begins and ends inside ONE tick is still delivered",
      edges(click_run([1, 0])) == 1)
check("two fast clicks in one tick are two presses, not one held button",
      edges(click_run([1, 0, 1, 0])) == 2)
check("a click straddling a tick boundary is one press, not two",
      edges(click_run([1], [], [0])) == 1)
check("and the button always ends released",
      (click_run([1, 0])[-1]["buttons"] & 1) == 0)

# --- a lost RELEASE must not stick forever ---------------------------------
# A keyboard report is STATE. A report that vanishes in transit is silent, and
# if the missing one is a release the target holds that modifier forever --
# every letter afterwards comes out accented, and no other keyboard can clear
# it, because no other keyboard owns this device's state.
p7 = make_portal(clipboard_capable=False)
p7.socks = {"dev": object()}
p7.mods = 0
p7.raw_keys = {}
p7._last_allclear = 0.0
p7._kbd_dirty = True
allclear = []
p7.send = lambda target, msg: allclear.append((target, msg))
p7._assert_nothing_held()
check("with nothing held, the portal says so on its own",
      allclear == [("dev", {"cmd": "kbd", "mods": 0, "keys": []})])

allclear.clear()
p7._assert_nothing_held()
check("and not more than once a second", allclear == [])

p8 = make_portal(clipboard_capable=False)
p8.socks = {"dev": object()}
p8.mods = P.VK_MOD[VK_CTRL]        # Ctrl really IS held
p8.raw_keys = {}
p8._last_allclear = 0.0
held = []
p8.send = lambda target, msg: held.append(msg)
p8._assert_nothing_held()
check("but it never claims nothing is held while something is", held == [])

p9 = make_portal(clipboard_capable=False)
p9.socks = {"dev": object()}
p9.mods = 0
p9.raw_keys = {0x41: 0x04}         # a letter is genuinely down
p9._last_allclear = 0.0
down = []
p9.send = lambda target, msg: down.append(msg)
p9._assert_nothing_held()
check("nor while a key is genuinely down", down == [])

print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
