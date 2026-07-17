#!/usr/bin/env python3
"""OpenSpan Portal — Input-Director-style edge crossing to the iPad.

Reads openspan_config.json (produced by openspan_setup.py) to learn
where the iPad sits among your real monitors. When your mouse crosses
the shared border (the "portal"), keyboard + mouse are captured and
streamed to the iPad over BLE; cross back to return control to the PC.

  Cross the portal edge   -> control the iPad
  Move back across it, or -> control the PC
  press Ctrl+Alt+Q        -> panic exit
  press Ctrl+Alt+I        -> toggle manually (ignores geometry)

Pure ctypes; closing this console unhooks everything (safety net).
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import socket
import sys
import threading
import time
import queue

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

if getattr(sys, "frozen", False):  # OpenSpan.exe --portal: data sits at
    _ROOT = os.path.dirname(os.path.abspath(sys.executable))  # the exe
else:
    _ROOT = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".."))
CONFIG_PATH = os.path.join(_ROOT, "openspan_config.json")

SEND_HZ = 120
ENTER_MARGIN = 40          # perpendicular units "into" the iPad on entry

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
DAEMON = (_SETTINGS.get("daemon_host", "127.0.0.1"),
          int(_SETTINGS.get("daemon_port", 9955)))

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
    ip = cfg["ipad"]
    mons = {m["name"]: m for m in cfg["monitors"]}
    portals = []
    for p in cfg.get("portals", []):
        m = mons[p["monitor"]]
        edge, lo, hi = p["edge"], p["lo"], p["hi"]
        if edge == "ipad-left":       # iPad right of monitor
            portals.append(dict(axis="x", line=m["x"] + m["w"] - 1,
                                span=(lo, hi), span_axis="y", sign=+1,
                                exit_to=(m["x"] + m["w"] - 3, None)))
        elif edge == "ipad-right":    # iPad left of monitor
            portals.append(dict(axis="x", line=m["x"],
                                span=(lo, hi), span_axis="y", sign=-1,
                                exit_to=(m["x"] + 3, None)))
        elif edge == "ipad-top":      # iPad below monitor
            portals.append(dict(axis="y", line=m["y"] + m["h"] - 1,
                                span=(lo, hi), span_axis="x", sign=+1,
                                exit_to=(None, m["y"] + m["h"] - 3)))
        elif edge == "ipad-bottom":   # iPad above monitor
            portals.append(dict(axis="y", line=m["y"],
                                span=(lo, hi), span_axis="x", sign=-1,
                                exit_to=(None, m["y"] + 3)))
    return cfg, portals


class Portal:
    def __init__(self):
        self.cfg, self.portals = load_portals()
        # primary-screen center for relative-capture re-centering
        prim = next((m for m in self.cfg["monitors"] if m["primary"]),
                    self.cfg["monitors"][0])
        self.cx = prim["x"] + prim["w"] // 2
        self.cy = prim["y"] + prim["h"] // 2
        self.active = False
        self.cur = None        # active portal
        self.entry_along = 0   # position along the edge at entry
        self.perp = 0          # perpendicular displacement into iPad
        self.raw_keys = {}     # vk -> hid usage (held non-modifier keys)
        self.mods = 0          # physical modifier byte (L/R bits)
        self.buttons = 0
        self.remap, self.overrides = self._load_keymap()
        self._chord_until = 0.0   # passthrough reports are dropped until
        #                           then, so they can't clobber an FKA chord
        self._hot_down = set()    # clipboard-hotkey VKs currently held
        #                           (typematic autorepeat must not re-fire)
        self._last_chord = 0.0    # plus a hard min-interval between chords
        self._last_sync_seq = None  # Windows clipboard seq at last iPad sync
        #   (None -> the first portal entry always hands the clipboard over)
        self._push_pending = False  # collapse overlapping copy-pushes
        self.q = queue.Queue()
        self.sock = None
        self._connect()
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

    def _connect(self):
        while True:
            try:
                self.sock = socket.create_connection(DAEMON, 3)
                self.sock.settimeout(2)
                print(f"[portal] connected to daemon {DAEMON[0]}:{DAEMON[1]}")
                return
            except OSError as e:
                print(f"[portal] daemon unreachable ({e}); retrying...")
                time.sleep(2)

    def send(self, obj):
        try:
            self.sock.sendall((json.dumps(obj) + "\n").encode())
            try:
                self.sock.recv(64)
            except socket.timeout:
                pass
        except OSError:
            print("[portal] link lost; reconnecting...")
            self._connect()

    # ---- mode switch ----
    def enter(self, portal, along):
        self.active = True
        self.cur = portal
        self.entry_along = along
        self.perp = ENTER_MARGIN
        user32.SetCursorPos(self.cx, self.cy)
        print(f"[portal] >>> iPad mode ON via {portal['axis']}"
              f"={portal['line']}  (Ctrl+Alt+Q to exit)")
        # UNIFIED CLIPBOARD, entry half: if the Windows clipboard changed
        # since the last sync, hand it to the iPad now (fires the iPad's
        # "Paste from PC" shortcut) -- so a plain Ctrl(=Cmd)+V on the iPad
        # always pastes the newest copy from EITHER machine
        seq = _clip_seq()
        if seq != self._last_sync_seq:
            self._last_sync_seq = seq
            print("[portal] syncing the PC clipboard to the iPad")
            threading.Timer(
                0.3, lambda: self._send_chord(FKA_FETCH)).start()

    def leave(self):
        if not self.active:
            return
        self.active = False
        self.raw_keys.clear(); self.mods = 0; self.buttons = 0
        # Release the iPad's held keys + mouse buttons, but do it THROUGH THE
        # QUEUE -- never call send() (blocking socket I/O) from here. leave()
        # runs inside the low-level mouse/keyboard hook procedure; if a
        # synchronous send() stalls (recv timeout, or _connect() looping when
        # the daemon hiccups) the hook proc overruns Windows'
        # LowLevelHooksTimeout (~300 ms) and Windows SILENTLY UNHOOKS it -- the
        # portal keeps running but goes deaf, and edge crossings stop working
        # until a restart reinstalls the hook. The sender thread owns the
        # socket and is the only place allowed to block on it.
        self.q.put(("k", 0, [], 0))
        self.q.put(("b", 0, 0, 0))
        # drop the real cursor back just inside the monitor at the
        # position we entered from
        if self.cur:
            ex, ey = self.cur["exit_to"]
            if ex is None:
                ex = int(self.entry_along)
            if ey is None:
                ey = int(self.entry_along)
            user32.SetCursorPos(int(ex), int(ey))
        self.cur = None
        print("[portal] <<< iPad mode OFF (control back on PC)")

    def _hit_portal(self, x, y):
        for p in self.portals:
            if p["axis"] == "x" and abs(x - p["line"]) <= 1:
                lo, hi = p["span"]
                if lo <= y <= hi:
                    return p, y
            elif p["axis"] == "y" and abs(y - p["line"]) <= 1:
                lo, hi = p["span"]
                if lo <= x <= hi:
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
                    dx = int((ms.pt.x - self.cx) * MOUSE_SENS)
                    dy = int((ms.pt.y - self.cy) * MOUSE_SENS)
                    if dx or dy:
                        self.q.put(("m", dx, dy, 0))
                        # perpendicular progress into the iPad
                        prim = self.cur
                        move = dx if prim["axis"] == "x" else dy
                        self.perp += move * prim["sign"]
                        # track entry-along for the exit drop point
                        if prim["span_axis"] == "y":
                            self.entry_along += dy
                        else:
                            self.entry_along += dx
                        if self.perp < 0:
                            self.leave()
                            return 1
                    user32.SetCursorPos(self.cx, self.cy)
                    return 1
                elif wParam in (WM_LBUTTONDOWN, WM_LBUTTONUP):
                    self.buttons = (self.buttons | 1) if \
                        wParam == WM_LBUTTONDOWN else (self.buttons & ~1)
                    self.q.put(("b", 0, 0, 0)); return 1
                elif wParam in (WM_RBUTTONDOWN, WM_RBUTTONUP):
                    self.buttons = (self.buttons | 2) if \
                        wParam == WM_RBUTTONDOWN else (self.buttons & ~2)
                    self.q.put(("b", 0, 0, 0)); return 1
                elif wParam in (WM_MBUTTONDOWN, WM_MBUTTONUP):
                    self.buttons = (self.buttons | 4) if \
                        wParam == WM_MBUTTONDOWN else (self.buttons & ~4)
                    self.q.put(("b", 0, 0, 0)); return 1
                elif wParam == WM_MOUSEWHEEL:
                    delta = ctypes.c_short(ms.mouseData >> 16).value // 120
                    if SCROLL_INVERT:
                        delta = -delta
                    self.q.put(("w", 0, 0, delta)); return 1
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _kbd_proc(self, nCode, wParam, lParam):
        if nCode == 0:
            kb = ctypes.cast(lParam,
                             ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = kb.vkCode
            down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            up = wParam in (WM_KEYUP, WM_SYSKEYUP)
            ctrl = self.mods & 0x11
            alt = self.mods & 0x44
            if down and vk == 0x51 and ctrl and alt:      # Ctrl+Alt+Q
                if self.active:
                    self.leave()
                return 1
            if down and vk == 0x49 and ctrl and alt:      # Ctrl+Alt+I
                if self.active:
                    self.leave()
                elif self.portals:
                    self.enter(self.portals[0], self.cy)
                return 1
            shift = self.mods & 0x22
            if up and vk in self._hot_down:
                self._hot_down.discard(vk)  # re-arm the hotkey on release
            if down and vk == 0x56 and ctrl and alt and shift:
                # Ctrl+Alt+Shift+V: tell the iPad to FETCH the PC clipboard
                # (runs its "Paste from PC" shortcut via the FKA chord)
                if self._chord_armed(vk):
                    print("[portal] asking iPad to fetch the PC clipboard")
                    self._send_chord(FKA_FETCH)
                return 1
            if down and vk == 0x43 and ctrl and alt and shift:
                # Ctrl+Alt+Shift+C: tell the iPad to PUSH its clipboard to
                # the PC (runs its "Copy to PC" shortcut via the FKA chord)
                if self._chord_armed(vk):
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
                    self.q.put(("t", text, 0, 0))
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
                if down and vk in (0x43, 0x58) and ctrl and not alt:
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
        self.q.put(("k", mods, [usage], 0))

        def finish():
            self.q.put(("k", 0, [], 0))
            if self.active:
                # resync the iPad with what is still physically held
                out_mods = 0
                for name in self._phys_mod_names():
                    tgt = self.remap.get(name, name)
                    out_mods |= IPAD_MOD_BIT.get(tgt, 0)
                self.q.put(("k", out_mods,
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
                self.q.put(("k", omods, okeys[:6], 0))
                return
        # 2) passthrough with modifier remap
        out_mods = 0
        for name in mod_names:
            tgt = self.remap.get(name, name)
            out_mods |= IPAD_MOD_BIT.get(tgt, 0)
        self.q.put(("k", out_mods, key_usages[:6], 0))

    def sender(self):
        period = 1.0 / SEND_HZ
        while True:
            time.sleep(period)
            adx = ady = awheel = 0
            btn_dirty = False
            keymsgs = []
            texts = []
            drained = False
            while True:
                try:
                    kind, a, b, c = self.q.get_nowait()
                except queue.Empty:
                    break
                drained = True
                if kind == "m":
                    adx += a; ady += b
                elif kind == "w":
                    awheel += c
                elif kind == "b":
                    btn_dirty = True
                elif kind == "k":
                    keymsgs.append((a, b))
                elif kind == "t":
                    texts.append(a)
            if not drained:
                continue
            for text in texts:
                self.send({"cmd": "text", "text": text})
            for mods, keys in keymsgs:
                self.send({"cmd": "kbd", "mods": mods, "keys": keys})
            if adx or ady or awheel:
                while adx or ady or awheel:
                    sx = max(-127, min(127, adx)); adx -= sx
                    sy = max(-127, min(127, ady)); ady -= sy
                    sw = max(-127, min(127, awheel)); awheel -= sw
                    self.send({"cmd": "mouse", "dx": sx, "dy": sy,
                               "buttons": self.buttons, "wheel": sw})
            elif btn_dirty:
                self.send({"cmd": "mouse", "dx": 0, "dy": 0,
                           "buttons": self.buttons, "wheel": 0})

    def run(self):
        if not self.portals:
            print("[portal] WARNING: no portals in config — only "
                  "Ctrl+Alt+I toggle will work. Run openspan_setup.py.")
        threading.Thread(target=self.sender, daemon=True).start()
        threading.Thread(target=_scroll_watcher, daemon=True).start()
        self.mouse_hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._mcb, kernel32.GetModuleHandleW(None), 0)
        self.kbd_hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._kcb, kernel32.GetModuleHandleW(None), 0)
        if not self.mouse_hook or not self.kbd_hook:
            print("[portal] ERROR: failed to install hooks")
            return
        print(f"[portal] ready — {len(self.portals)} portal(s) loaded. "
              "Cross the edge to control the iPad; Ctrl+Alt+Q to bail.")
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


if __name__ == "__main__":
    Portal().run()
