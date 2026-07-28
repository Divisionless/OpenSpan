#!/usr/bin/env python3
"""OpenSpan Portal — Input-Director-style edge crossing to the iPad.

Reads openspan_config.json (produced by openspan_setup.py) to learn
where the iPad sits among your real monitors. When your mouse crosses
the shared border (the "portal"), keyboard + mouse are captured and
streamed to the iPad over BLE; cross back to return control to the PC.

  Cross the portal edge   -> control the iPad
  Move back across it, or -> control the PC
  press Esc 3x in a row   -> panic exit (Ctrl+Alt+Q kept as backup)
  press Ctrl+Alt+I        -> toggle manually (ignores geometry)

Pure ctypes; closing this console unhooks everything (safety net).
"""

import ctypes
import ctypes.wintypes as wt
import json
import math
import os
import socket
import sys
import threading
import time
import queue

from openspan_targets import (
    ARRIVE_MARGIN, compute_adjacencies, compute_portals, exit_inset,
    oriented_resolution,
)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

if getattr(sys, "frozen", False):  # OpenSpan.exe --portal: data sits at
    _ROOT = os.path.dirname(os.path.abspath(sys.executable))  # the exe
else:
    _ROOT = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".."))
CONFIG_PATH = os.path.join(_ROOT, "openspan_config.json")

SEND_HZ = 120
# THE RULE THIS FILE OBEYS:
#
#   The model may only change position by an amount the wire also moved.
#   When the model must be somewhere the wire has not taken it, SEND THE
#   DIFFERENCE.
#
# A relative HID link cannot move a target's cursor "to" a place, and every
# previous attempt to work around that fact -- remembering a position instead of
# asserting one, requiring sustained pressure before a crossing commits, a
# cooldown after each transition, stretching a crossing proportionally onto the
# destination's whole edge -- traded a small honest error for a large hidden
# one. Each of them discarded motion the wire had already delivered, so the
# model and the target's real pointer drifted apart with no way back.
#
# There is no workaround needed. The target's pointer CAN be moved to a known
# place: by sending exactly the delta the model just jumped (_place/_warp).
#
# ARRIVE_MARGIN (shared with openspan_targets, so both halves of every edge use
# one number) is what keeps an arrival off the trigger it arrived through.
# Geometry does that job now; no accumulator is required.
MAX_WARP_REPORTS = 64      # a full-union warp on any real device is < 40
PIN_OVERSHOOT = 400.0      # target pixels driven PAST an edge, to force the
                           # clamp that makes the pinned position a fact
# Pointer acceleration applied HERE, on Windows, rather than by the target OS.
# That is the whole point: because we compute it, the SAME accelerated delta
# feeds both the wire and the virtual cursor, so the model cannot drift from
# reality the way it does when the target accelerates behind our back.
# factor = 1 + accel * (magnitude / ACCEL_PIVOT), clamped to ACCEL_MAX.
ACCEL_PIVOT = 12.0
ACCEL_MAX = 5.0

# --- Apple's pointer-acceleration curve, and its inverse ------------------
# macOS transforms EVERY HID report by a function of that report's MAGNITUDE
# alone -- no time or rate term (a BlueZ GATT peripheral publishes no report
# rate, so Apple's rateMultiplier is 1). The curve below is Apple's shipped
# acceleration table at the default setting, reconstructed and checked against
# six measured points to under 0.06 px.
#
# Because it depends only on per-report magnitude, and because WE choose how
# motion is split into reports, it is INVERTIBLE: ask for a pixel distance,
# solve for the single report magnitude that produces it, and send exactly
# that one report. Apple then accelerates it into precisely the distance we
# wanted. The user keeps macOS acceleration ON and the pointer stays exact.
# Saturation begins at 206 counts, beyond our 8-bit field, so we never reach
# the flat part where the inverse would not exist.
_APPLE_CURVE = ((0.0, 0.0), (2.6388, 0.5373), (25.7194, 23.6418),
                (71.6418, 136.1194), (136.8537, 199.1642),
                (205.7313, 214.9254))


def _piecewise(value, points, inverse=False):
    lo_i, hi_i = (1, 0) if inverse else (0, 1)
    if value <= points[0][hi_i]:
        return points[0][lo_i]
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        a_in, a_out = (ay, ax) if inverse else (ax, ay)
        b_in, b_out = (by, bx) if inverse else (bx, by)
        if value <= b_in:
            span = b_in - a_in
            frac = 0.0 if span <= 0 else (value - a_in) / span
            return a_out + frac * (b_out - a_out)
    return points[-1][lo_i]


def apple_pixels(counts):
    """Pixels macOS moves for ONE report of this magnitude."""
    return _piecewise(abs(float(counts)), _APPLE_CURVE)


def apple_counts(pixels):
    """Report magnitude that yields this many pixels after Apple's curve."""
    return _piecewise(abs(float(pixels)), _APPLE_CURVE, inverse=True)

# FKA chords bound (on the iPad, Settings > Accessibility > Keyboards >
# Full Keyboard Access > Commands) to the two clipboard Shortcuts -- see
# CLIPBOARD_DESIGN.md / CLIPBOARD_SETUP.md. (mods byte, HID usage):
FKA_FETCH = (0x01 | 0x04, 0x0A)  # Ctrl+Opt+G -> "Paste from PC" shortcut
FKA_PUSH = (0x01 | 0x04, 0x0B)   # Ctrl+Opt+H -> "Copy to PC" shortcut
FKA_HOLD = 0.05                  # chord hold time before release


def _clip_seq():
    """Windows clipboard sequence number: bumps on every clipboard change,
    readable without opening the clipboard. Lets the portal sync the
    clipboard to the iPad only when there is actually something new."""
    try:
        return int(ctypes.windll.user32.GetClipboardSequenceNumber())
    except Exception:  # noqa: BLE001
        return -1


def _load_settings():
    path = os.path.join(_ROOT, "openspan_settings.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


_SETTINGS = _load_settings()
MOUSE_SENS = float(_SETTINGS.get("mouse_sensitivity", 1.0))
# Where the OpenSpan daemon lives. 127.0.0.1 = a VM on this PC; set
# daemon_host to another machine's LAN IP to use ITS Bluetooth as the
# bridge (keeps this PC's radio free for headphones).
DAEMON_HOST = _SETTINGS.get("daemon_host", "127.0.0.1")
DEFAULT_DAEMON_PORT = int(_SETTINGS.get("daemon_port", 9955))

# Scroll-wheel direction. Read LIVE from openspan_settings.json so the app's
# "Invert scroll" toggle applies without restarting the portal. A tiny watcher
# thread refreshes it off the hook thread (never file I/O inside a hook proc).
SCROLL_INVERT = bool(_SETTINGS.get("scroll_invert", False))


def _scroll_watcher():
    global SCROLL_INVERT
    while True:
        try:
            SCROLL_INVERT = bool(_load_settings().get("scroll_invert", False))
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.4)


def get_clipboard_text():
    """Read Unicode text from the Windows clipboard (stdlib ctypes)."""
    CF_UNICODETEXT = 13
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    # prototype the HANDLE paths: default 32-bit restype truncates 64-bit
    # clipboard handles -> GlobalLock(NULL) -> silent empty reads
    u.OpenClipboard.argtypes = [ctypes.c_void_p]
    u.GetClipboardData.restype = ctypes.c_void_p
    u.GetClipboardData.argtypes = [ctypes.c_uint]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [ctypes.c_void_p]
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    if not u.OpenClipboard(None):
        return ""
    try:
        h = u.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        p = k.GlobalLock(h)
        if not p:
            return ""
        try:
            return ctypes.c_wchar_p(p).value or ""
        finally:
            k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()

# ---- Win32 constants ----
WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
WM_MOUSEWHEEL = 0x020A
LLMHF_INJECTED = 0x01

VK_HID = {}
for i in range(26):
    VK_HID[0x41 + i] = 0x04 + i
for i in range(1, 10):
    VK_HID[0x30 + i] = 0x1E + (i - 1)
VK_HID[0x30] = 0x27
VK_HID.update({
    0x0D: 0x28, 0x1B: 0x29, 0x08: 0x2A, 0x09: 0x2B, 0x20: 0x2C,
    0xBD: 0x2D, 0xBB: 0x2E, 0xDB: 0x2F, 0xDD: 0x30, 0xDC: 0x31,
    0xBA: 0x33, 0xDE: 0x34, 0xC0: 0x35, 0xBC: 0x36, 0xBE: 0x37,
    0xBF: 0x38, 0x14: 0x39,
    0x25: 0x50, 0x27: 0x4F, 0x26: 0x52, 0x28: 0x51,
    0x24: 0x4A, 0x23: 0x4D, 0x21: 0x4B, 0x22: 0x4E,
    0x2D: 0x49, 0x2E: 0x4C,
})
for i in range(12):
    VK_HID[0x70 + i] = 0x3A + i

VK_MOD = {
    0xA2: 0x01, 0xA0: 0x02, 0xA4: 0x04, 0x5B: 0x08,
    0xA3: 0x10, 0xA1: 0x20, 0xA5: 0x40, 0x5C: 0x80,
    0x11: 0x01, 0x10: 0x02, 0x12: 0x04,
}

# ---- keymap name tables ----
NAME_TO_USAGE = {}
for _i, _ch in enumerate("abcdefghijklmnopqrstuvwxyz"):
    NAME_TO_USAGE[_ch] = 0x04 + _i
for _i, _ch in enumerate("1234567890"):
    NAME_TO_USAGE[_ch] = 0x1E + _i
NAME_TO_USAGE.update({
    "enter": 0x28, "return": 0x28, "esc": 0x29, "escape": 0x29,
    "backspace": 0x2A, "tab": 0x2B, "space": 0x2C, "minus": 0x2D,
    "equals": 0x2E, "left": 0x50, "right": 0x4F, "up": 0x52, "down": 0x51,
    "home": 0x4A, "end": 0x4D, "pageup": 0x4B, "pagedown": 0x4E,
    "delete": 0x4C, "insert": 0x49,
})
for _i in range(12):
    NAME_TO_USAGE[f"f{_i + 1}"] = 0x3A + _i
USAGE_TO_NAME = {}
for _n, _u in NAME_TO_USAGE.items():
    USAGE_TO_NAME.setdefault(_u, _n)

# iPad HID modifier bits by name (left-variant; iPad ignores L/R).
IPAD_MOD_BIT = {"ctrl": 0x01, "shift": 0x02, "alt": 0x04,
                "cmd": 0x08, "gui": 0x08, "win": 0x08}


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD),
                ("flags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD),
                ("flags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.CFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)

# Declare 64-bit-correct prototypes — without these, ctypes defaults
# handle/return types to 32-bit int and truncates pointers, so
# SetWindowsHookEx fails with an invalid module handle.
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC,
                                     ctypes.c_void_p, wt.DWORD]
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                  wt.WPARAM, wt.LPARAM]
user32.GetMessageW.restype = ctypes.c_int
user32.GetMessageW.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.c_uint, ctypes.c_uint]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
kernel32.GetModuleHandleW.restype = ctypes.c_void_p
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]


def load_portals():
    """Turn the saved arrangement into a list of enter-able edges.

    Each portal: which monitor edge line to watch, the span along it,
    the axis+sign of movement 'into' the iPad, and where to drop the
    real cursor on exit.
    """
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    # Every entrance point is derived from the device list -- there is no
    # per-device-type portal builder. A config with no devices simply has no
    # entrances (nothing to route to), which is a valid, quiet state.
    if cfg.get("devices"):
        return cfg, compute_portals(cfg)
    return cfg, []


class Portal:
    # Class-level defaults so a partially constructed Portal is well-defined
    # (the routing tests build one directly).
    portals = []                # PC-facing entrances (set in __init__)
    _device_remap = {}          # device id -> remap dict, or None to inherit
    _device_scroll_invert = {}  # device id -> bool
    # Where each device's pointer actually is. ONE entry per device, because a
    # device has ONE pointer -- keying this by (device, display) was duplicated
    # state for a single physical thing, and it is why returning to a device
    # through one screen could restore a position saved on another.
    # This is only trustworthy because nothing else in this file moves the model
    # without moving the wire by the same amount; see THE RULE above.
    _last_seen = {}             # device id -> (display_id, vx, vy)
    _device_gain = {}           # device id -> points per HID unit
    _device_accel = {}          # device id -> acceleration strength (0 = off)
    _device_sens = {}           # device id -> feel multiplier
    _device_compensate = {}     # device id -> invert the target's own curve
    _rem_x = 0.0                # sub-unit remainders: slow motion must not be
    _rem_y = 0.0                # truncated away to nothing

    def __init__(self):
        self.cfg, self.portals = load_portals()
        self.links = compute_adjacencies(self.cfg)
        self._displays = {
            (device["id"], display["id"]): display
            for device in self.cfg.get("devices", [])
            if device.get("enabled", True)
            for display in device.get("displays", [])
        }
        self._monitors = {
            monitor["name"]: monitor
            for monitor in self.cfg.get("monitors", [])
        }
        # primary-screen center for relative-capture re-centering
        prim = next((m for m in self.cfg["monitors"] if m["primary"]),
                    self.cfg["monitors"][0])
        self.cx = prim["x"] + prim["w"] // 2
        self.cy = prim["y"] + prim["h"] // 2
        self.active = False
        self._last_seen = {}   # device id -> (display_id, vx, vy)
        self.cur = None        # active portal
        self.entry_along = 0   # position along the edge at entry
        self.perp = 0          # perpendicular displacement into iPad
        self.raw_keys = {}     # vk -> hid usage (held non-modifier keys)
        self.mods = 0          # physical modifier byte (L/R bits)
        self.buttons = 0
        self.active_target = None
        self.active_display = None
        self.vx = 0.0
        self.vy = 0.0
        # Each device carries its OWN port -- its own entrance point. No lane
        # is special-cased and no port is reserved for a kind of device.
        self._target_ports = {
            device["id"]: int(device.get("port", DEFAULT_DAEMON_PORT))
            for device in self.cfg.get("devices", [])
            if device.get("enabled", True)
        }
        # Which devices opted into the clipboard bridge. Replaces the old
        # `active_target == "ipad"` test so the feature follows a capability,
        # not a hardcoded device name.
        self._clipboard_devices = {
            device["id"]: bool(device.get("clipboard", False))
            for device in self.cfg.get("devices", [])
        }
        # INPUT settings are per device. Scroll direction is a plain per-device
        # preference. The modifier remap matters more: {"alt": "cmd"} is an
        # iPad convention, and sending it to a Mac delivers physical Alt as the
        # GUI bit -- which a Mac with Command/Control swapped reports as
        # CONTROL, so Option becomes unreachable. None inherits the global
        # keymap so existing behaviour is unchanged.
        self._device_scroll_invert = {
            device["id"]: bool(device.get("scroll_invert", False))
            for device in self.cfg.get("devices", [])
        }
        self._device_gain = {
            device["id"]: float(device.get("pointer_gain", 1.0) or 1.0)
            for device in self.cfg.get("devices", [])
        }
        self._device_accel = {
            device["id"]: max(0.0, min(4.0, float(
                device.get("pointer_accel", 0.0) or 0.0)))
            for device in self.cfg.get("devices", [])
        }
        self._device_sens = {
            device["id"]: max(0.1, min(4.0, float(
                device.get("sensitivity", 1.0) or 1.0)))
            for device in self.cfg.get("devices", [])
        }
        self._device_compensate = {
            device["id"]: bool(device.get("compensate_target_accel", False))
            for device in self.cfg.get("devices", [])
        }
        self._device_remap = {
            device["id"]: (dict(device["modifier_remap"])
                           if isinstance(device.get("modifier_remap"), dict)
                           else None)
            for device in self.cfg.get("devices", [])
        }
        if not self._target_ports:
            self._target_ports = {
                portal.get("target"): int(
                    portal.get("daemon_port", DEFAULT_DAEMON_PORT))
                for portal in self.portals
            }
        self.target_ready = {
            target: False for target in self._target_ports
        }
        self.remap, self.overrides = self._load_keymap()
        self._chord_until = 0.0   # passthrough reports are dropped until
        #                           then, so they can't clobber an FKA chord
        self._hot_down = set()    # clipboard-hotkey VKs currently held
        self._esc_hist = []       # timestamps of consecutive Esc presses
                                  # (panic bail: Esc x3 within 2s)
        #                           (typematic autorepeat must not re-fire)
        self._last_chord = 0.0    # plus a hard min-interval between chords
        self._last_sync_seq = None  # Windows clipboard seq at last iPad sync
        #   (None -> the first portal entry always hands the clipboard over)
        self._push_pending = False  # collapse overlapping copy-pushes
        self.q = queue.Queue()
        self.socks = {}
        self._mcb = HOOKPROC(self._mouse_proc)
        self._kcb = HOOKPROC(self._kbd_proc)

    def _load_keymap(self):
        path = os.path.join(_ROOT, "openspan_keymap.json")
        try:
            with open(path) as f:
                km = json.load(f)
        except (OSError, ValueError) as e:
            print(f"[portal] no keymap ({e}); using passthrough")
            return {}, []
        remap = {k.lower(): v.lower()
                 for k, v in km.get("modifier_remap", {}).items()}
        overrides = []
        for ov in km.get("overrides", []):
            frm = [t.lower() for t in ov["from"]]
            to = [t.lower() for t in ov["to"]]
            fmods = frozenset(t for t in frm if t in IPAD_MOD_BIT
                              or t == "win")
            fkeys = frozenset(t for t in frm if t in NAME_TO_USAGE)
            omods = 0
            for t in to:
                if t in IPAD_MOD_BIT:
                    omods |= IPAD_MOD_BIT[t]
            okeys = [NAME_TO_USAGE[t] for t in to if t in NAME_TO_USAGE]
            overrides.append((fmods, fkeys, omods, okeys))
        print(f"[portal] keymap: {len(overrides)} override(s), "
              f"remap={remap or 'none'}")
        return remap, overrides

    def _connect(self, target):
        port = self._target_ports.get(target, DEFAULT_DAEMON_PORT)
        try:
            sock = socket.create_connection((DAEMON_HOST, port), 3)
            sock.settimeout(2)
            self.socks[target] = sock
            print(f"[portal] connected {target} to daemon "
                  f"{DAEMON_HOST}:{port}")
            return sock
        except OSError as exc:
            print(f"[portal] {target} daemon unreachable ({exc})")
            return None

    def send(self, target, obj):
        sock = self.socks.get(target) or self._connect(target)
        if sock is None:
            if self.active and self.active_target == target:
                self.leave()
            return
        try:
            sock.sendall((json.dumps(obj) + "\n").encode())
            try:
                sock.recv(64)
            except socket.timeout:
                pass
        except OSError:
            # NEVER hold the mouse hostage while forwarding is failing: if
            # the link dies mid-capture, hand control back to the PC FIRST,
            # then worry about reconnecting. (leave() is non-blocking.)
            if self.active and self.active_target == target:
                print("[portal] link lost while captured — releasing the "
                      "mouse to the PC")
                self.leave()
            try:
                sock.close()
            except OSError:
                pass
            self.socks.pop(target, None)
            print(f"[portal] {target} link lost; it will reconnect on demand")

    def _status_watcher(self):
        """Keep hook-time edge decisions local; never do I/O in a hook proc."""
        while True:
            for target, port in self._target_ports.items():
                ready = False
                try:
                    sock = socket.create_connection((DAEMON_HOST, port), 0.5)
                    sock.settimeout(0.5)
                    sock.sendall(b'{"cmd":"status"}\n')
                    data = b""
                    while b"\n" not in data and len(data) < 4096:
                        chunk = sock.recv(512)
                        if not chunk:
                            break
                        data += chunk
                    sock.close()
                    status = json.loads(data.split(b"\n", 1)[0].decode())
                    # Gate on the KEYBOARD subscription only -- the same truth
                    # the app and the guest daemon use. Also requiring
                    # mouse_subscribed meant a bonded host that re-subscribed to
                    # only the keyboard report (which per BLE spec it may) left
                    # every edge silently shut while the UI read "connected".
                    ready = bool(status.get("kbd_subscribed"))
                except Exception:  # noqa: BLE001
                    ready = False
                if ready != self.target_ready.get(target):
                    # log the transition so a shut edge is never unexplained
                    print(f"[portal] {target} "
                          f"{'READY' if ready else 'not ready'} "
                          f"(kbd_subscribed={ready})", flush=True)
                self.target_ready[target] = ready
                if not ready and self.active \
                        and self.active_target == target:
                    print(f"[portal] {target} disconnected while captured — "
                          "control returning to the PC")
                    self.leave()
            time.sleep(0.8)

    # ---- mode switch / shared-layout routing ----------------------------
    @staticmethod
    def _monitor_layout(monitor):
        return (
            int(monitor.get("layout_x", monitor["x"])),
            int(monitor.get("layout_y", monitor["y"])),
            int(monitor.get("layout_w", monitor["w"])),
            int(monitor.get("layout_h", monitor["h"])),
        )

    @staticmethod
    def _clamp(value, lo, hi):
        return max(float(lo), min(float(hi), float(value)))

    def _entry_point(self, portal, along):
        display = self._displays.get((
            portal.get("target"), portal.get("target_display")))
        if not display:
            return 0.0, 0.0
        x, y = float(display["x"]), float(display["y"])
        width, height = float(display["w"]), float(display["h"])
        monitor = self._monitors.get(portal.get("monitor"))
        layout_along = float(along)
        if monitor:
            mx, my, mw, mh = self._monitor_layout(monitor)
            if portal["axis"] == "x":
                layout_along = my + (
                    (float(along) - monitor["y"])
                    / max(1.0, float(monitor["h"]))) * mh
            else:
                layout_along = mx + (
                    (float(along) - monitor["x"])
                    / max(1.0, float(monitor["w"]))) * mw
        margin_x = min(float(ARRIVE_MARGIN), max(2.0, width * 0.1))
        margin_y = min(float(ARRIVE_MARGIN), max(2.0, height * 0.1))
        # Inset BOTH axes. Clamping the along axis to the bare rectangle put a
        # legitimate fresh entry exactly on a corner -- the far right of the
        # DISPLAY4-top span lands on mac-2's right edge, which is itself a live
        # exit to DISPLAY1. An arrival must never touch another trigger.
        lo_x, hi_x = x + margin_x, x + width - margin_x
        lo_y, hi_y = y + margin_y, y + height - margin_y
        edge = portal.get("edge")
        if edge == "target-left":
            return x + margin_x, self._clamp(layout_along, lo_y, hi_y)
        if edge == "target-right":
            return x + width - margin_x, self._clamp(layout_along, lo_y, hi_y)
        if edge == "target-top":
            return self._clamp(layout_along, lo_x, hi_x), y + margin_y
        return self._clamp(layout_along, lo_x, hi_x), y + height - margin_y

    def _portal_for(self, target, display):
        """A PC-facing portal belonging to this surface, for exit fallback."""
        for portal in self.portals:
            if portal.get("target") == target                     and portal.get("target_display") == display:
                return portal
        return None

    def _matching_link(self, side, along):
        for link in self.links:
            source = link["source"]
            if source.get("kind") != "target" \
                    or source.get("target") != self.active_target \
                    or source.get("display") != self.active_display \
                    or link.get("side") != side:
                continue
            lo, hi = link["span"]
            if not lo <= along <= hi:
                continue
            destination = link["destination"]
            if destination.get("kind") == "target" \
                    and not self.target_ready.get(
                        destination.get("target"), False):
                continue
            return link
        # NEAREST-LINK FALLBACK. An edge range with no link was a silent,
        # unbounded wall: mac-2's right edge is 47% dead, because the PC monitor
        # beside it is shorter than the 32" panel, so leaning right anywhere in
        # the top half of that edge did nothing whatsoever. Fall back to the
        # closest link on the SAME side -- a shorter neighbour then receives the
        # whole edge instead of only the slice that literally overlaps it.
        candidates = []
        for link in self.links:
            source = link["source"]
            if source.get("kind") != "target" \
                    or source.get("target") != self.active_target \
                    or source.get("display") != self.active_display \
                    or link.get("side") != side:
                continue
            destination = link["destination"]
            if destination.get("kind") == "target" \
                    and not self.target_ready.get(
                        destination.get("target"), False):
                continue
            lo, hi = link["span"]
            candidates.append(
                (min(abs(float(along) - lo), abs(float(along) - hi)), link))
        if not candidates:
            return None
        return min(candidates, key=lambda row: row[0])[1]

    def _position_inside(self, destination, to_side, along):
        """Where a crossing LANDS: the crossing coordinate itself, in desk units.

        This used to stretch the overlap span across the destination's whole
        edge, so 30% down the edge you left became 30% down the edge you arrived
        on. That was a 3.4x magnification on the iPad -> Mac seam, it was not
        reversible (a round trip did not return you where you started), and it
        deposited a large share of Mac-to-Mac crossings into a band of the next
        screen from which no link led back -- the direct reason a whole screen
        could not be reached.

        The desk IS the shared physical space; that is its entire purpose. An
        identity arrival is reversible by construction, it matches what the user
        actually sees in front of him, and it always lands inside the return
        link's span because that span is the geometric overlap. Reaching the
        part of a taller neighbour that overhangs is done by MOVING once you are
        there -- not by distorting where you arrive."""
        display = self._displays.get((
            destination.get("target"), destination.get("display")))
        if not display:
            return self.vx, self.vy
        x, y = float(display["x"]), float(display["y"])
        width, height = float(display["w"]), float(display["h"])
        margin_x = min(float(ARRIVE_MARGIN), max(2.0, width * 0.1))
        margin_y = min(float(ARRIVE_MARGIN), max(2.0, height * 0.1))
        lo_x, hi_x = x + margin_x, x + width - margin_x
        lo_y, hi_y = y + margin_y, y + height - margin_y
        if to_side == "left":
            return x + margin_x, self._clamp(along, lo_y, hi_y)
        if to_side == "right":
            return x + width - margin_x, self._clamp(along, lo_y, hi_y)
        if to_side == "top":
            return self._clamp(along, lo_x, hi_x), y + margin_y
        return self._clamp(along, lo_x, hi_x), y + height - margin_y

    def _local_exit_point(self, destination, to_side, along):
        monitor = self._monitors.get(destination.get("monitor"))
        if not monitor:
            return self.cx, self.cy
        mx, my, mw, mh = self._monitor_layout(monitor)
        if to_side in ("left", "right"):
            actual_along = monitor["y"] + (
                (float(along) - my) / max(1.0, float(mh))) * monitor["h"]
            inset = exit_inset(monitor, "x")
            x = (monitor["x"] + inset if to_side == "left"
                 else monitor["x"] + monitor["w"] - inset)
            y = self._clamp(
                actual_along, monitor["y"], monitor["y"] + monitor["h"] - 1)
        else:
            actual_along = monitor["x"] + (
                (float(along) - mx) / max(1.0, float(mw))) * monitor["w"]
            x = self._clamp(
                actual_along, monitor["x"], monitor["x"] + monitor["w"] - 1)
            inset = exit_inset(monitor, "y")
            y = (monitor["y"] + inset if to_side == "top"
                 else monitor["y"] + monitor["h"] - inset)
        return int(round(x)), int(round(y))

    def _display_at(self, target, x, y):
        """Which of this device's screens contains a desk point."""
        for (device, _display_id), display in self._displays.items():
            if device != target:
                continue
            dx, dy = float(display["x"]), float(display["y"])
            if dx <= x <= dx + float(display["w"]) \
                    and dy <= y <= dy + float(display["h"]):
                return display
        return None

    def _emit_move(self, target, px, py):
        """Queue a TARGET-PIXEL displacement as legal HID reports."""
        if not self._device_compensate.get(target):
            dx, dy = int(round(px)), int(round(py))
            if dx or dy:
                self.q.put((target, "m", dx, dy, 0))
            return
        # The Apple inverse is defined PER REPORT, so a warp to a compensated
        # device has to be pre-split into single legal reports here rather than
        # handed to the sender as one sum it would re-split arbitrarily.
        remaining = math.hypot(px, py)
        if remaining <= 0.0:
            return
        ux, uy = px / remaining, py / remaining
        for _ in range(MAX_WARP_REPORTS):
            if remaining < 0.5:
                break
            counts = apple_counts(min(remaining, apple_pixels(127)))
            dx = max(-127, min(127, int(round(ux * counts))))
            dy = max(-127, min(127, int(round(uy * counts))))
            if not (dx or dy):
                break
            self.q.put((target, "m", dx, dy, 0))
            remaining -= apple_pixels(math.hypot(dx, dy))

    def _warp(self, target, from_x, from_y, to_x, to_y):
        """Move a target's REAL pointer by a desk-space displacement.

        This is what makes asserting a position legitimate. Desk units convert
        to target pixels at a rate that differs per DISPLAY -- a 32" 4K portrait
        panel and a 32" 1440p landscape one are nothing like the same pixels per
        inch -- so the straight path is walked in small steps and each step is
        converted by the screen it actually passes through. Deterministic from
        the geometry alone; nothing about any particular desk is assumed."""
        steps = 64
        px = py = 0.0
        for index in range(steps):
            frac = (index + 0.5) / steps
            display = self._display_at(
                target, from_x + (to_x - from_x) * frac,
                from_y + (to_y - from_y) * frac)
            if not display:
                continue
            res_w, res_h = oriented_resolution(display)
            gain = float(self._device_gain.get(target, 1.0)) or 1.0
            px += (to_x - from_x) / steps * res_w / max(
                1.0, gain * float(display["w"]))
            py += (to_y - from_y) / steps * res_h / max(
                1.0, gain * float(display["h"]))
        self._emit_move(target, px, py)

    def _outer_bound(self, target, side, along):
        """This device's outermost desk coordinate in one direction.

        Taken over the screens that actually span `along`, so an L-shaped
        arrangement gives the boundary the pointer will really meet rather than
        the bounding box of everything."""
        bounds = []
        for (device, _display_id), display in self._displays.items():
            if device != target:
                continue
            x, y = float(display["x"]), float(display["y"])
            width, height = float(display["w"]), float(display["h"])
            if side in ("left", "right"):
                if y <= along <= y + height:
                    bounds.append(x if side == "left" else x + width)
            elif x <= along <= x + width:
                bounds.append(y if side == "top" else y + height)
        if not bounds:
            return None
        return min(bounds) if side in ("left", "top") else max(bounds)

    def _pin(self, target, side, along):
        """Shove this device's pointer hard against its own outer boundary.

        A relative HID link cannot ask a device where its pointer is -- but the
        device's own window server CLAMPS that pointer at the edge of its
        display union. So driving it hard in one direction turns its position on
        that axis from an accumulated belief into a measured fact, using nothing
        but the target's own clamp.

        Done as the LAST act of leaving a device, in the direction you left by.
        By then your attention is on the screen you moved TO, so the travel is
        never watched -- and the next time you cross back, the model does not
        have to remember anything. It knows.

        Doug's idea, 2026-07-27: "whatever screen it was on, can you, as the
        last action, shove the mouse as far as it goes in that same direction.
        This will always reset the relative position per window boundary."
        """
        edge = self._outer_bound(target, side, along)
        if edge is None:
            return False
        if side in ("left", "right"):
            to_x, to_y = edge, float(along)
        else:
            to_x, to_y = float(along), edge
        # exact travel to the boundary...
        self._warp(target, self.vx, self.vy, to_x, to_y)
        # ...then well past it, so rounding cannot leave the pointer a pixel
        # short of the clamp that makes this whole thing true.
        display = self._display_at(target, to_x, to_y) or self._displays.get(
            (target, self.active_display))
        if display:
            res_w, res_h = oriented_resolution(display)
            if side in ("left", "right"):
                push = max(PIN_OVERSHOOT, float(res_w) * 0.25)
                self._emit_move(target, -push if side == "left" else push, 0.0)
            else:
                push = max(PIN_OVERSHOOT, float(res_h) * 0.25)
                self._emit_move(target, 0.0, -push if side == "top" else push)
        pinned = self._display_at(target, to_x, to_y)
        self._last_seen[target] = (
            pinned["id"] if pinned else self.active_display, to_x, to_y)
        return True

    def _place(self, target, display, vx, vy):
        """The ONLY discontinuous assignment of the model position.

        If we already know where this device's pointer is, the jump is PAID FOR
        on the wire before it is recorded. If we do not -- the first entry of
        the session -- the model asserts and accepts one unverified placement,
        which then self-corrects the moment the pointer meets any real edge."""
        previous = self._last_seen.get(target)
        if previous and self._displays.get((target, previous[0])):
            self._warp(target, previous[1], previous[2], float(vx), float(vy))
        self.active_target = target
        self.active_display = display
        self.vx, self.vy = float(vx), float(vy)
        self._last_seen[target] = (display, self.vx, self.vy)

    def _switch_target(self, destination, to_side, along, from_side=None):
        target = destination.get("target")
        display = destination.get("display")
        old_target = self.active_target
        old_display = self.active_display
        pinned = False
        if old_target is not None and target != old_target \
                and from_side is not None:
            # Leaving this DEVICE, not just one of its screens: pin its pointer
            # on the way out, exactly as when handing control back to the PC.
            pinned = self._pin(old_target, from_side, along)
        if old_target is not None and not pinned:
            # Record UNCONDITIONALLY otherwise, including a same-device screen
            # handoff. Skipping those let a device's saved position go
            # arbitrarily stale while the user wandered across its other
            # screens. A pin already recorded a BETTER value -- a measured one.
            self._last_seen[old_target] = (old_display, self.vx, self.vy)
        if target != old_target:
            # One Windows hook broker, independent target channels. Release the
            # old HID lane before changing sockets so no modifier can stick.
            self.q.put((old_target, "k", 0, [], 0))
            self.q.put((old_target, "b", 0, 0, 0))
        self._place(target, display,
                    *self._position_inside(destination, to_side, along))
        # The exit portal must follow the surface we are ACTUALLY on. self.cur
        # was set once by enter() and never updated, so bailing out after a
        # device-to-device handoff dropped the real cursor back through the
        # portal we FIRST came in by -- on the wrong monitor.
        self.cur = self._portal_for(target, display) or self.cur
        self.entry_along = float(along)
        if target != old_target:
            self._emit_kbd()
            print(f"[portal] >>> direct handoff {old_target}/{old_display} "
                  f"-> {target}/{display}")

    def _route_motion(self, dx, dy):
        """Advance the virtual desk cursor; return True after exiting to PC."""
        display = self._displays.get(
            (self.active_target, self.active_display))
        if not display:
            # Version-1/single-target rollback compatibility: retain the proven
            # enter/reverse-exit model when no shared-layout display exists.
            prim = self.cur
            if not prim:
                return False
            move = dx if prim["axis"] == "x" else dy
            self.perp += move * prim["sign"]
            if prim["span_axis"] == "y":
                self.entry_along += dy
            else:
                self.entry_along += dx
            if self.perp < 0:
                self.leave()
                return True
            return False
        res_w, res_h = oriented_resolution(display)
        # points-per-HID-unit for THIS device. 1.0 is exact once the target's
        # pointer acceleration is off; bias LOW so the real cursor reaches an
        # edge first and pins there, letting the clamp re-converge both cursors.
        gain = float(self._device_gain.get(self.active_target, 1.0))
        scale_x = gain * float(display["w"]) / max(1.0, float(res_w))
        scale_y = gain * float(display["h"]) / max(1.0, float(res_h))
        nx = self.vx + dx * scale_x
        ny = self.vy + dy * scale_y
        x, y = float(display["x"]), float(display["y"])
        right = x + float(display["w"])
        bottom = y + float(display["h"])
        # The model takes the motion the wire ALREADY carried, before any
        # crossing is considered. leave() and _switch_target() record this
        # position, and it is the ORIGIN of the next warp -- one report stale
        # here is one report of error on every future entry to this device.
        self.vx, self.vy = nx, ny
        crossings = []
        # `along` is CLAMPED into the rectangle. Unclamped, a diagonal move into
        # a corner offered each crossing an along value that lay outside the
        # other edge's span, so both were rejected and the corner was a livelock.
        if nx < x:
            crossings.append((x - nx, "left", self._clamp(ny, y, bottom)))
        if nx > right:
            crossings.append((nx - right, "right", self._clamp(ny, y, bottom)))
        if ny < y:
            crossings.append((y - ny, "top", self._clamp(nx, x, right)))
        if ny > bottom:
            crossings.append((ny - bottom, "bottom", self._clamp(nx, x, right)))
        # No pressure accumulator and no cooldown. Both existed only because an
        # arrival used to land ON the trigger it arrived through; geometry does
        # that job now (ARRIVE_MARGIN, on both surfaces and both axes). Both
        # also DISCARDED motion the wire had already delivered -- 45 target
        # points per lean plus up to 300 ms of travel, gone from the model and
        # never recovered -- which is exactly the drift they were meant to stop.
        for _overshoot, side, along in sorted(crossings, reverse=True):
            link = self._matching_link(side, along)
            if not link:
                continue
            destination = link["destination"]
            if destination.get("kind") == "local":
                self.leave(exit_to=self._local_exit_point(
                    destination, link["to_side"], along),
                    pin_side=side, pin_along=along)
                return True
            # Never tear a drag across devices. The handoff re-arms as soon as
            # the physical button is released.
            if self.buttons:
                break
            self._switch_target(destination, link["to_side"], along,
                                from_side=side)
            return False
        # Nothing consumed the overshoot, so the target's own cursor is pinned
        # against a real screen edge -- and so is the model.
        self.vx = self._clamp(nx, x, right)
        self.vy = self._clamp(ny, y, bottom)
        return False

    def enter(self, portal, along):
        self.active = True
        self.cur = portal
        self.entry_along = along
        self.perp = ARRIVE_MARGIN
        # ENTER AT THE EDGE YOU CROSSED -- always, on every entry, through
        # whichever portal you actually used. The previous build restored the
        # position saved on the last exit instead, which meant crossing in from
        # the right could land you on the edge you had left by from the BOTTOM,
        # and that saved point sat exactly on a live exit trigger, so a small
        # push in the same direction threw you straight back to the PC.
        # Asserting is legitimate now because _place pays for the jump in HID
        # reports: the target's pointer really does arrive where the model says.
        self._place(portal.get("target"), portal.get("target_display"),
                    *self._entry_point(portal, along))
        user32.SetCursorPos(self.cx, self.cy)
        name = portal.get("target_name", self.active_target)
        print(f"[portal] >>> {name} mode ON via {portal['axis']}"
              f"={portal['line']}  (Esc x3 to bail)")
        # Announce whatever is ALREADY physically held. _kbd_proc keeps
        # self.mods current in every mode, but nothing told the device we just
        # entered -- so "hold Ctrl, cross the edge, scroll" arrived as a BARE
        # wheel. A device-to-device handoff already does this (_switch_target);
        # entry from the PC did not. Devices with the clipboard capability were
        # accidentally repaired ~0.35s later by the chord resync below, which
        # is why this only ever showed up on a device without it.
        self._emit_kbd()
        # UNIFIED CLIPBOARD, entry half: if the Windows clipboard changed
        # since the last sync, hand it to the device now (fires its
        # "Paste from PC" shortcut) -- so a plain Ctrl(=Cmd)+V there
        # always pastes the newest copy from EITHER machine
        seq = _clip_seq()
        if self._clipboard_devices.get(self.active_target)                 and seq != self._last_sync_seq:
            self._last_sync_seq = seq
            print("[portal] syncing the PC clipboard to the iPad")
            threading.Timer(
                0.3, lambda: self._send_chord(FKA_FETCH)).start()

    def leave(self, exit_to=None, pin_side=None, pin_along=None):
        if not self.active:
            return
        # THE LAST ACT: shove this device's pointer as far as it goes the way
        # you were going. Its own window server clamps it there, so where it is
        # stops being a belief. A bail (Esc x3, a dropped lane) has no direction
        # and skips this -- nothing moved the pointer, so the record still holds.
        pinned = False
        if pin_side is not None and self.active_target is not None:
            pinned = self._pin(self.active_target, pin_side, pin_along)
            if pinned:
                record = self._last_seen[self.active_target]
                self.active_display = record[0]
                self.vx, self.vy = record[1], record[2]
        self._rem_x = self._rem_y = 0.0
        self.active = False
        # NOTE: self.mods is the PHYSICAL modifier mirror, maintained in every
        # mode by _kbd_proc. Zeroing it here corrupted it while the key was
        # still DOWN -- the later key-UP then cleared an already-clear bit, so
        # after one out-and-back across the edge the modifier was dead until
        # you released and re-pressed it. The device is released explicitly by
        # the ("k", 0, [], 0) below; the mirror must keep telling the truth.
        self.raw_keys.clear(); self.buttons = 0
        # Release the iPad's held keys + mouse buttons, but do it THROUGH THE
        # QUEUE -- never call send() (blocking socket I/O) from here. leave()
        # runs inside the low-level mouse/keyboard hook procedure; if a
        # synchronous send() stalls (recv timeout, or _connect() looping when
        # the daemon hiccups) the hook proc overruns Windows'
        # LowLevelHooksTimeout (~300 ms) and Windows SILENTLY UNHOOKS it -- the
        # portal keeps running but goes deaf, and edge crossings stop working
        # until a restart reinstalls the hook. The sender thread owns the
        # socket and is the only place allowed to block on it.
        target = self.active_target
        # Where this device's pointer really is. Every bail path lands here --
        # a routed exit, Esc x3, Ctrl+Alt+Q/I, a lane dropping -- and none of
        # them moves the target's cursor, so the record is true in all of them.
        if target is not None and not pinned:
            vx, vy = self.vx, self.vy
            display = self._displays.get((target, self.active_display))
            if display:
                # The last report can carry the model past the edge it left by.
                # The target's own window server clamped ITS cursor at that
                # edge, so the edge is the truth -- recording the overshoot
                # would make the next entry's warp overshoot by the same amount.
                vx = self._clamp(vx, float(display["x"]),
                                 float(display["x"]) + float(display["w"]))
                vy = self._clamp(vy, float(display["y"]),
                                 float(display["y"]) + float(display["h"]))
            self._last_seen[target] = (self.active_display, vx, vy)
        self.q.put((target, "k", 0, [], 0))
        self.q.put((target, "b", 0, 0, 0))
        # drop the real cursor back just inside the monitor at the
        # position we entered from
        if exit_to is not None:
            user32.SetCursorPos(int(exit_to[0]), int(exit_to[1]))
        elif self.cur:
            ex, ey = self.cur["exit_to"]
            if ex is None:
                ex = int(self.entry_along)
            if ey is None:
                ey = int(self.entry_along)
            user32.SetCursorPos(int(ex), int(ey))
        self.cur = None
        name = self.active_target or "target"
        self.active_target = None
        self.active_display = None
        print(f"[portal] <<< {name} mode OFF (control back on PC)")

    def _hit_portal(self, x, y):
        for p in self.portals:
            if not self.target_ready.get(p.get("target"), False):
                continue
            if p["axis"] == "x" and abs(x - p["line"]) <= 1:
                lo, hi = p["span"]
                if lo <= y < hi:
                    return p, y
            elif p["axis"] == "y" and abs(y - p["line"]) <= 1:
                lo, hi = p["span"]
                if lo <= x < hi:
                    return p, x
        return None, None

    # ---- hooks ----
    def _mouse_proc(self, nCode, wParam, lParam):
        if nCode == 0:
            ms = ctypes.cast(lParam,
                             ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            if not self.active:
                if (wParam == WM_MOUSEMOVE and
                        not (ms.flags & LLMHF_INJECTED)):
                    p, along = self._hit_portal(ms.pt.x, ms.pt.y)
                    if p:
                        self.enter(p, along)
                        return 1
            else:
                if ms.flags & LLMHF_INJECTED:
                    return 1
                if wParam == WM_MOUSEMOVE:
                    # per-device sensitivity FIRST, so acceleration below and
                    # the virtual cursor both see the same corrected motion
                    _sens = MOUSE_SENS * self._device_sens.get(
                        self.active_target, 1.0)
                    fx = (ms.pt.x - self.cx) * _sens
                    fy = (ms.pt.y - self.cy) * _sens
                    # OUR acceleration, applied before anything else, so the
                    # identical value drives the device AND the virtual cursor.
                    accel = self._device_accel.get(self.active_target, 0.0)
                    if accel > 0.0:
                        mag = math.hypot(fx, fy)
                        if mag > 0.0:
                            factor = min(
                                ACCEL_MAX, 1.0 + accel * mag / ACCEL_PIVOT)
                            fx *= factor
                            fy *= factor
                    # carry the sub-unit remainder, otherwise slow, precise
                    # movement truncates to zero and the pointer feels sticky
                    fx += self._rem_x
                    fy += self._rem_y
                    if self._device_compensate.get(self.active_target):
                        # The TARGET accelerates and we cannot switch it off.
                        # Ask for a pixel distance and solve for the single
                        # report magnitude that Apple will turn into exactly
                        # that -- so the model advances by PIXELS while the
                        # wire carries COUNTS, and the two still agree.
                        want = math.hypot(fx, fy)
                        dx = dy = 0
                        mx = my = 0.0
                        if want > 0.0:
                            counts = apple_counts(want)
                            scale = counts / want
                            dx = max(-127, min(127, int(round(fx * scale))))
                            dy = max(-127, min(127, int(round(fy * scale))))
                            # Credit the model along the direction the WIRE
                            # actually carries. Rounding each axis separately
                            # changes the vector's direction, and crediting the
                            # pre-rounding direction handed the model motion no
                            # report contained: a long shallow drag put zero
                            # vertical counts on the wire while the model
                            # climbed steadily off course.
                            length = math.hypot(dx, dy)
                            if length > 0.0:
                                got = apple_pixels(length)
                                mx, my = dx / length * got, dy / length * got
                        if abs(dx) >= 127 or abs(dy) >= 127:
                            # One hook event this large is a flick. Banking its
                            # unsent excess re-fires it as a phantom lurch the
                            # next time the mouse is touched; drop it instead.
                            self._rem_x = self._rem_y = 0.0
                        else:
                            self._rem_x, self._rem_y = fx - mx, fy - my
                        if dx or dy:
                            self.q.put((self.active_target, "m", dx, dy, 0))
                            if self._route_motion(mx, my):
                                return 1
                        user32.SetCursorPos(self.cx, self.cy)
                        return 1
                    dx, dy = int(fx), int(fy)
                    self._rem_x, self._rem_y = fx - dx, fy - dy
                    if dx or dy:
                        self.q.put((self.active_target, "m", dx, dy, 0))
                        if self._route_motion(dx, dy):
                            return 1
                    user32.SetCursorPos(self.cx, self.cy)
                    return 1
                elif wParam in (WM_LBUTTONDOWN, WM_LBUTTONUP):
                    self.buttons = (self.buttons | 1) if \
                        wParam == WM_LBUTTONDOWN else (self.buttons & ~1)
                    self.q.put((self.active_target, "b", 0, 0, 0)); return 1
                elif wParam in (WM_RBUTTONDOWN, WM_RBUTTONUP):
                    self.buttons = (self.buttons | 2) if \
                        wParam == WM_RBUTTONDOWN else (self.buttons & ~2)
                    self.q.put((self.active_target, "b", 0, 0, 0)); return 1
                elif wParam in (WM_MBUTTONDOWN, WM_MBUTTONUP):
                    self.buttons = (self.buttons | 4) if \
                        wParam == WM_MBUTTONDOWN else (self.buttons & ~4)
                    self.q.put((self.active_target, "b", 0, 0, 0)); return 1
                elif wParam == WM_MOUSEWHEEL:
                    delta = ctypes.c_short(ms.mouseData >> 16).value // 120
                    if self._device_scroll_invert.get(
                            self.active_target, SCROLL_INVERT):
                        delta = -delta
                    self.q.put((self.active_target, "w", 0, 0, delta)); return 1
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _kbd_proc(self, nCode, wParam, lParam):
        if nCode == 0:
            kb = ctypes.cast(lParam,
                             ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = kb.vkCode
            down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            up = wParam in (WM_KEYUP, WM_SYSKEYUP)
            # ---- PANIC BAIL: Esc x3 in a row while captured --------------
            # Must work when EVERYTHING else is broken: no sockets, no other
            # thread, no reliance on our tracked-modifier state (which is
            # exactly what can be desynced in a bad state). Three plain Esc
            # presses within 2s always hand the mouse back to the PC.
            if down and self.active:
                if vk == 0x1B:                                 # Esc
                    now = time.time()
                    self._esc_hist = [t for t in self._esc_hist
                                      if now - t < 2.0] + [now]
                    if len(self._esc_hist) >= 3:
                        self._esc_hist.clear()
                        print("[portal] PANIC BAIL (Esc x3) — iPad mode OFF, "
                              "control back on PC")
                        self.leave()
                        return 1
                else:
                    self._esc_hist.clear()
            ctrl = self.mods & 0x11
            alt = self.mods & 0x44
            if down and vk == 0x51 and ctrl and alt:      # Ctrl+Alt+Q
                if self.active:                           # legacy bail, kept
                    self.leave()                          # as a backup
                return 1
            if down and vk == 0x49 and ctrl and alt:      # Ctrl+Alt+I
                if self.active:
                    self.leave()
                else:
                    ready = next(
                        (portal for portal in self.portals
                         if self.target_ready.get(
                             portal.get("target"), False)),
                        None)
                    if ready:
                        self.enter(ready, self.cy)
                return 1
            shift = self.mods & 0x22
            if up and vk in self._hot_down:
                self._hot_down.discard(vk)  # re-arm the hotkey on release
            if down and vk == 0x56 and ctrl and alt and shift:
                # Ctrl+Alt+Shift+V: tell the iPad to FETCH the PC clipboard
                # (runs its "Paste from PC" shortcut via the FKA chord)
                if self._clipboard_devices.get(self.active_target) and self._chord_armed(vk):
                    print("[portal] asking iPad to fetch the PC clipboard")
                    self._send_chord(FKA_FETCH)
                return 1
            if down and vk == 0x43 and ctrl and alt and shift:
                # Ctrl+Alt+Shift+C: tell the iPad to PUSH its clipboard to
                # the PC (runs its "Copy to PC" shortcut via the FKA chord)
                if self._clipboard_devices.get(self.active_target) and self._chord_armed(vk):
                    print("[portal] asking iPad to push its clipboard to "
                          "the PC")
                    self._send_chord(FKA_PUSH)
                return 1
            if down and vk == 0x56 and ctrl and alt:      # Ctrl+Alt+V
                text = get_clipboard_text()
                if text:
                    print(f"[portal] pasting {len(text)} chars to iPad")
                    # through the queue: the sender thread owns the socket,
                    # so nothing else may write it concurrently
                    self.q.put((self.active_target, "t", text, 0, 0))
                return 1
            # keep physical modifier byte current in every mode
            if vk in VK_MOD:
                if down:
                    self.mods |= VK_MOD[vk]
                elif up:
                    self.mods &= ~VK_MOD[vk]
            if self.active:
                if vk in VK_HID and vk not in VK_MOD:
                    if down:
                        self.raw_keys[vk] = VK_HID[vk]
                    elif up:
                        self.raw_keys.pop(vk, None)
                self._emit_kbd()
                # UNIFIED CLIPBOARD, exit half: a copy/cut on the iPad
                # (Ctrl+C/X, remapped to Cmd by the keymap) also pushes the
                # iPad clipboard back to the PC, so crossing back and
                # hitting Ctrl+V on Windows just works
                if self._clipboard_devices.get(self.active_target) and down \
                        and vk in (0x43, 0x58) and ctrl and not alt:
                    self._schedule_push()
                return 1
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _chord_armed(self, vk):
        """One chord per PHYSICAL keypress (typematic autorepeat delivers
        endless WM_KEYDOWNs for a held key), and never two chords within
        half a second (mashing must not spam the iPad shortcut)."""
        now = time.time()
        if vk in self._hot_down or now - self._last_chord < 0.5:
            return False
        self._hot_down.add(vk)
        self._last_chord = now
        return True

    def _schedule_push(self):
        """After a copy/cut on the iPad: give the app ~350ms to finish the
        copy, then fire the 'Copy to PC' shortcut so the PC clipboard
        matches. Overlapping copies collapse into the one pending push
        (the last copy is what's on the clipboard anyway), and a recent
        chord defers the push instead of dropping it — a dropped push
        would let the two clipboards silently diverge."""
        if self._push_pending:
            return  # the pending push will carry the newest copy
        self._push_pending = True

        def push():
            if time.time() - self._last_chord < 0.5:
                threading.Timer(0.4, push).start()  # wait out the gate
                return
            self._push_pending = False
            self._send_chord(FKA_PUSH)
            # the PC clipboard is about to change because WE change it --
            # that must not read as "new on Windows" at the next entry
            threading.Timer(2.5, self._mark_synced).start()
        threading.Timer(0.35, push).start()

    def _mark_synced(self):
        self._last_sync_seq = _clip_seq()

    def _send_chord(self, chord):
        """Send one HID chord press with a real hold time, then release —
        all through the queue (the sender thread owns the socket). A
        press+release in the same sender tick would be ~1ms apart, shorter
        than any physical keystroke iOS expects. While the chord is in
        flight, passthrough reports are suppressed (_emit_kbd checks
        _chord_until) so a physical key transition can't clobber it; in
        iPad mode a resync report restores the real held state afterwards.
        Chords SERIALIZE: one arriving while another is in flight defers
        until the first completes, so their press/release reports can
        never interleave. (Known 50ms window: if the portal is hard-killed
        mid-chord the release is lost and the iPad sees stuck keys until
        the next report — accepted, the window is two frames wide.)"""
        now = time.time()
        if now < self._chord_until:
            threading.Timer(self._chord_until - now + 0.02,
                            lambda: self._send_chord(chord)).start()
            return
        mods, usage = chord
        self._last_chord = now
        self._chord_until = now + FKA_HOLD + 0.05
        target = self.active_target
        self.q.put((target, "k", mods, [usage], 0))

        def finish():
            self.q.put((target, "k", 0, [], 0))
            if self.active and self.active_target == target:
                # resync the iPad with what is still physically held
                out_mods = 0
                for name in self._phys_mod_names():
                    tgt = self._active_remap().get(name, name)
                    out_mods |= IPAD_MOD_BIT.get(tgt, 0)
                self.q.put((target, "k", out_mods,
                            list(self.raw_keys.values())[:6], 0))
        threading.Timer(FKA_HOLD, finish).start()

    def _phys_mod_names(self):
        n = set()
        if self.mods & 0x11:
            n.add("ctrl")
        if self.mods & 0x22:
            n.add("shift")
        if self.mods & 0x44:
            n.add("alt")
        if self.mods & 0x88:
            n.add("win")
        return n

    def _active_remap(self):
        """Modifier remap for the device currently being driven. A per-device
        dict (even an empty one) overrides; None inherits the global keymap."""
        own = self._device_remap.get(self.active_target)
        return self.remap if own is None else own

    def _emit_kbd(self):
        if time.time() < self._chord_until:
            return  # an FKA chord is in flight; don't clobber it
        mod_names = self._phys_mod_names()
        key_usages = list(self.raw_keys.values())
        # 1) exact override match (single-key combos)
        if len(key_usages) <= 1:
            key_names = frozenset(USAGE_TO_NAME.get(u) for u in key_usages)
            fmods = frozenset(mod_names)
            for omods, okeys in ((o[2], o[3]) for o in self.overrides
                                 if o[0] == fmods and o[1] == key_names):
                self.q.put((self.active_target, "k", omods, okeys[:6], 0))
                return
        # 2) passthrough with modifier remap
        out_mods = 0
        for name in mod_names:
            tgt = self._active_remap().get(name, name)
            out_mods |= IPAD_MOD_BIT.get(tgt, 0)
        self.q.put((self.active_target, "k", out_mods, key_usages[:6], 0))

    def sender(self):
        period = 1.0 / SEND_HZ
        while True:
            time.sleep(period)
            batches = {}
            while True:
                try:
                    target, kind, a, b, c = self.q.get_nowait()
                except queue.Empty:
                    break
                target = target or self.active_target
                batch = batches.setdefault(target, {
                    "dx": 0, "dy": 0, "wheel": 0, "moves": [],
                    "button": False, "keys": [], "texts": [],
                })
                if kind == "m":
                    if self._device_compensate.get(target):
                        # Each report for a compensated device was solved
                        # individually against the target's own acceleration
                        # curve. Summing them inside this tick and re-splitting
                        # the total destroys that: the curve is steeper for a
                        # big report, so the merged pair travels much further
                        # than the two it replaced. Keep them intact.
                        batch["moves"].append((a, b))
                    else:
                        batch["dx"] += a
                        batch["dy"] += b
                elif kind == "w":
                    batch["wheel"] += c
                elif kind == "b":
                    batch["button"] = True
                elif kind == "k":
                    batch["keys"].append((a, b))
                elif kind == "t":
                    batch["texts"].append(a)
            if not batches:
                continue
            for target, batch in batches.items():
                for text in batch["texts"]:
                    self.send(target, {"cmd": "text", "text": text})
                for mods, keys in batch["keys"]:
                    self.send(
                        target, {"cmd": "kbd", "mods": mods, "keys": keys})
                for mdx, mdy in batch["moves"]:
                    self.send(
                        target,
                        {"cmd": "mouse", "dx": mdx, "dy": mdy,
                         "buttons": self.buttons, "wheel": 0})
                adx, ady, awheel = (
                    batch["dx"], batch["dy"], batch["wheel"])
                if adx or ady or awheel:
                    while adx or ady or awheel:
                        sx = max(-127, min(127, adx)); adx -= sx
                        sy = max(-127, min(127, ady)); ady -= sy
                        sw = max(-127, min(127, awheel)); awheel -= sw
                        self.send(
                            target,
                            {"cmd": "mouse", "dx": sx, "dy": sy,
                             "buttons": self.buttons, "wheel": sw})
                elif batch["button"] and not batch["moves"]:
                    self.send(
                        target,
                        {"cmd": "mouse", "dx": 0, "dy": 0,
                         "buttons": self.buttons, "wheel": 0})

    def run(self):
        if not self.portals:
            print("[portal] WARNING: no portals in config — only "
                  "Ctrl+Alt+I toggle will work. Run openspan_setup.py.")
        threading.Thread(target=self.sender, daemon=True).start()
        threading.Thread(target=_scroll_watcher, daemon=True).start()
        threading.Thread(target=self._status_watcher, daemon=True).start()
        self.mouse_hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._mcb, kernel32.GetModuleHandleW(None), 0)
        self.kbd_hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._kcb, kernel32.GetModuleHandleW(None), 0)
        if not self.mouse_hook or not self.kbd_hook:
            print("[portal] ERROR: failed to install hooks")
            return
        print(f"[portal] ready — {len(self.portals)} portal(s) loaded. "
              "Cross an edge to control its device; tap Esc 3x to bail.")
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


if __name__ == "__main__":
    Portal().run()
