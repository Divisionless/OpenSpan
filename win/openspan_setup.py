#!/usr/bin/env python3
"""OpenSpan Setup — arrange the iPad among your real monitors.

Shows your Windows monitors exactly where Windows places them, plus a
draggable iPad screen. Drop the iPad against whichever monitor edge it
physically sits at; it snaps flush, and that shared border becomes the
"portal" your mouse crosses to control the iPad. Save writes
openspan_config.json, which the input router (openspan_portal.py) reads.

Pure standard library (tkinter + ctypes) — no dependencies.
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import tkinter as tk
from tkinter import ttk

if getattr(sys, "frozen", False):  # frozen exe: data lives at the exe
    CONFIG_PATH = os.path.join(
        os.path.dirname(os.path.abspath(sys.executable)),
        "openspan_config.json")
else:
    CONFIG_PATH = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..",
        "openspan_config.json"))

# iPad 7th gen (10.2"): 2160x1620 px = 1080x810 points. Editable in UI.
IPAD_PRESETS = {
    "iPad 10.2\" (7th–9th gen)": (1080, 810),
    "iPad Air / Pro 11\"": (1194, 834),
    "iPad Pro 12.9\"": (1366, 1024),
    "iPad mini": (1133, 744),
}


# ---- enumerate monitors -----------------------------------------------
class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wt.DWORD),
                ("szDevice", ctypes.c_wchar * 32)]


CCHDEVICENAME = 32
CCHFORMNAME = 32
ENUM_CURRENT_SETTINGS = -1


class DEVMODEW(ctypes.Structure):
    """The display mode Windows is running an adapter at.

    Only dmDisplayFrequency is read here, but the WHOLE structure has to be
    declared: EnumDisplaySettingsW is handed dmSize and fills the buffer to that
    length, and the frequency sits two thirds of the way down. A short struct
    would be written past.

    The printer half of DEVMODE is a union with the display half; this is the
    display arm, which is the documented layout for EnumDisplaySettingsW.
    """

    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * CCHDEVICENAME),
        ("dmSpecVersion", wt.WORD),
        ("dmDriverVersion", wt.WORD),
        ("dmSize", wt.WORD),
        ("dmDriverExtra", wt.WORD),
        ("dmFields", wt.DWORD),
        ("dmPositionX", ctypes.c_long),
        ("dmPositionY", ctypes.c_long),
        ("dmDisplayOrientation", wt.DWORD),
        ("dmDisplayFixedOutput", wt.DWORD),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * CCHFORMNAME),
        ("dmLogPixels", wt.WORD),
        ("dmBitsPerPel", wt.DWORD),
        ("dmPelsWidth", wt.DWORD),
        ("dmPelsHeight", wt.DWORD),
        ("dmDisplayFlags", wt.DWORD),
        ("dmDisplayFrequency", wt.DWORD),
        ("dmICMMethod", wt.DWORD),
        ("dmICMIntent", wt.DWORD),
        ("dmMediaType", wt.DWORD),
        ("dmDitherType", wt.DWORD),
        ("dmReserved1", wt.DWORD),
        ("dmReserved2", wt.DWORD),
        ("dmPanningWidth", wt.DWORD),
        ("dmPanningHeight", wt.DWORD),
    ]


def display_refresh_hz(device_name):
    """The refresh rate Windows is CURRENTLY driving this adapter at, or None.

    Windows knows this number exactly, so nothing on the Windows side should
    ever guess it. It was guessed: every local monitor was stamped 60 Hz by
    _normalize_monitor and nothing ever wrote a real value, so the arrangement
    asserted "@ 60 Hz" about a 144 Hz panel with total confidence.

    0 and 1 are the documented "adapter default" sentinels, not rates. They come
    back as None -- an unknown refresh is reported as unknown rather than
    printed as "@ 1 Hz".
    """
    try:
        mode = DEVMODEW()
        mode.dmSize = ctypes.sizeof(DEVMODEW)
        ok = ctypes.windll.user32.EnumDisplaySettingsW(
            ctypes.c_wchar_p(str(device_name)), ENUM_CURRENT_SETTINGS,
            ctypes.byref(mode))
        if not ok:
            return None
        hz = int(mode.dmDisplayFrequency)
    except (OSError, ValueError, AttributeError):
        return None
    return float(hz) if hz > 1 else None


# The monitors Windows composes the desktop from -- every one of them.
#
# Doug, 2026-08-15: "Mac 2k lost its third display." It was never lost, and
# it was not stale config either. This function was DROPPING it, on every
# call, for as long as the process lived:
#
#     HMONITOR 0x0000000000010001   DISPLAY5   fits in an int32
#     HMONITOR 0x0000000000010005   DISPLAY1   fits in an int32
#     HMONITOR 0xffffffff8fc70da1   DISPLAY4   does not
#
# GetMonitorInfoW was called with no argtypes, so ctypes squeezed the handle
# into a 32-bit C int. The third one overflowed, GetMonitorInfoW raised
# ArgumentError INSIDE the enumeration callback, and ctypes' rule for an
# exception in a callback is to print it to stderr and carry on -- which in a
# --noconsole build is to say nothing at all. Two monitors came back, every
# reader downstream (adopt, merge, the watcher, the canvas) behaved correctly
# on that wrong input, and the arrangement quietly described a desk with one
# screen fewer than the desk had. Which screen drew the wide handle differed
# between sessions, which is why one saved profile had the third monitor and
# another did not.
#
# Every other GetMonitorInfoW call in this tree already declares its argtypes
# (monitor_identity, screen_zoom, window_tiling). This was the oldest one and
# the only one that fed the arrangement.
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(RECT), wt.LPARAM)
_u32.EnumDisplayMonitors.restype = wt.BOOL
_u32.EnumDisplayMonitors.argtypes = [
    wt.HDC, ctypes.c_void_p, _MONITORENUMPROC, wt.LPARAM]
_u32.GetMonitorInfoW.restype = wt.BOOL
_u32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]


# Monitors the last enum_monitors() call could NOT describe, as
# "0x<handle>: <why>" strings. Empty after a healthy call. A reader that
# wants to say "Windows has a screen this arrangement cannot see" reads this;
# the returned list itself stays clean, because a phantom 0x0 rectangle in
# the model would be a second bug in payment for the first.
LAST_ENUM_ERRORS = []


def enum_monitors():
    """Every monitor on the desktop, in Windows' own coordinates.

    A monitor this cannot describe is REPORTED in LAST_ENUM_ERRORS, never
    silently omitted -- which is precisely what used to happen.
    """
    monitors = []
    del LAST_ENUM_ERRORS[:]

    def _cb(hmon, hdc, lprc, data):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        try:
            ok = _u32.GetMonitorInfoW(hmon, ctypes.byref(info))
        except Exception as exc:  # noqa: BLE001 -- must never escape a callback
            ok, why = 0, f"{type(exc).__name__}: {exc}"
        else:
            why = "GetMonitorInfoW returned FALSE"
        if not ok:
            LAST_ENUM_ERRORS.append(f"0x{int(hmon or 0):x}: {why}")
            print(f"[enum_monitors] could not read monitor {LAST_ENUM_ERRORS[-1]}",
                  file=sys.stderr)
            return True
        r = info.rcMonitor
        row = {
            "name": info.szDevice,
            "x": r.left, "y": r.top,
            "w": r.right - r.left, "h": r.bottom - r.top,
            "primary": bool(info.dwFlags & 1),
        }
        # Present ONLY when it is really known. An absent key is what tells
        # every reader downstream to say nothing about refresh rate.
        hz = display_refresh_hz(info.szDevice)
        if hz:
            row["refresh_hz"] = hz
        monitors.append(row)
        return True

    _u32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(_cb), 0)
    return monitors


class SetupApp:
    def __init__(self, root):
        self.root = root
        root.title("OpenSpan — Arrange your iPad")
        self.monitors = enum_monitors()
        self.ipad = self._default_ipad()
        self._load_existing()

        top = ttk.Frame(root, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="iPad model:").pack(side="left")
        self.model_var = tk.StringVar(value=list(IPAD_PRESETS)[0])
        cb = ttk.Combobox(top, textvariable=self.model_var, width=26,
                          values=list(IPAD_PRESETS), state="readonly")
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", self._apply_preset)
        self.orient_var = tk.StringVar(value="landscape")
        ttk.Button(top, text="Rotate iPad",
                   command=self._rotate).pack(side="left", padx=4)
        ttk.Button(top, text="Save",
                   command=self._save).pack(side="right")
        self.status = ttk.Label(top, text="", foreground="#B28DFF")
        self.status.pack(side="right", padx=10)

        self.canvas = tk.Canvas(root, width=960, height=600, bg="#070A10",
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._redraw())
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)

        hint = ("Drag the green iPad against the edge of the monitor it "
                "sits next to — it snaps flush. That shared border is the "
                "portal your mouse crosses.")
        ttk.Label(root, text=hint, padding=6,
                  wraplength=940, foreground="#A6A1B0").pack(fill="x")

        self.dragging = False
        self.drag_off = (0, 0)
        self._world_bounds()
        self._redraw()

    def _default_ipad(self):
        prim = next((m for m in self.monitors if m["primary"]),
                    self.monitors[0])
        return {"x": prim["x"] + prim["w"], "y": prim["y"],
                "w": 1080, "h": 810, "res_w": 1080, "res_h": 810}

    def _load_existing(self):
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
            if "ipad" in cfg:
                self.ipad.update(cfg["ipad"])
        except (OSError, ValueError):
            pass

    def _apply_preset(self, *_):
        w, h = IPAD_PRESETS[self.model_var.get()]
        if self.orient_var.get() == "portrait":
            w, h = h, w
        self.ipad.update(w=w, h=h, res_w=w, res_h=h)
        self._redraw()

    def _rotate(self):
        self.orient_var.set("portrait"
                            if self.orient_var.get() == "landscape"
                            else "landscape")
        self.ipad["w"], self.ipad["h"] = self.ipad["h"], self.ipad["w"]
        self.ipad["res_w"], self.ipad["res_h"] = \
            self.ipad["res_h"], self.ipad["res_w"]
        self._redraw()

    # ---- world<->canvas transforms ----
    def _world_bounds(self):
        xs = [m["x"] for m in self.monitors] + \
             [m["x"] + m["w"] for m in self.monitors]
        ys = [m["y"] for m in self.monitors] + \
             [m["y"] + m["h"] for m in self.monitors]
        mw = max(m["w"] for m in self.monitors)
        mh = max(m["h"] for m in self.monitors)
        self.wx0, self.wx1 = min(xs) - mw, max(xs) + mw
        self.wy0, self.wy1 = min(ys) - mh, max(ys) + mh

    def _scale(self):
        cw = max(self.canvas.winfo_width(), 100)
        ch = max(self.canvas.winfo_height(), 100)
        sx = cw / (self.wx1 - self.wx0)
        sy = ch / (self.wy1 - self.wy0)
        s = min(sx, sy) * 0.92
        ox = (cw - (self.wx1 - self.wx0) * s) / 2
        oy = (ch - (self.wy1 - self.wy0) * s) / 2
        return s, ox, oy

    def w2c(self, x, y):
        s, ox, oy = self._scale()
        return (x - self.wx0) * s + ox, (y - self.wy0) * s + oy

    def c2w(self, cx, cy):
        s, ox, oy = self._scale()
        return (cx - ox) / s + self.wx0, (cy - oy) / s + self.wy0

    # ---- drawing ----
    def _redraw(self):
        c = self.canvas
        c.delete("all")
        for m in self.monitors:
            x0, y0 = self.w2c(m["x"], m["y"])
            x1, y1 = self.w2c(m["x"] + m["w"], m["y"] + m["h"])
            c.create_rectangle(x0, y0, x1, y1, fill="#1C182E",
                               outline="#6B5CA8", width=2)
            tag = "PRIMARY\n" if m["primary"] else ""
            c.create_text((x0 + x1) / 2, (y0 + y1) / 2,
                          text=f"{tag}{m['w']}x{m['h']}",
                          fill="#F5F5F8", justify="center",
                          font=("Segoe UI", 10, "bold"))
        ix0, iy0 = self.w2c(self.ipad["x"], self.ipad["y"])
        ix1, iy1 = self.w2c(self.ipad["x"] + self.ipad["w"],
                            self.ipad["y"] + self.ipad["h"])
        self.ipad_rect = c.create_rectangle(
            ix0, iy0, ix1, iy1, fill="#5F3DC4", outline="#8A5CFF", width=3)
        c.create_text((ix0 + ix1) / 2, (iy0 + iy1) / 2,
                      text=f"iPad\n{self.ipad['w']}x{self.ipad['h']}",
                      fill="#D8C8FF", justify="center",
                      font=("Segoe UI", 10, "bold"))
        self._show_portals()

    def _portals(self):
        """Shared borders between the iPad rect and any monitor."""
        ip = self.ipad
        out = []
        for m in self.monitors:
            # iPad left touches monitor right
            if abs(ip["x"] - (m["x"] + m["w"])) <= 2:
                lo = max(ip["y"], m["y"]); hi = min(ip["y"] + ip["h"],
                                                    m["y"] + m["h"])
                if hi - lo > 20:
                    out.append(("ipad-left", m, lo, hi))
            if abs((ip["x"] + ip["w"]) - m["x"]) <= 2:
                lo = max(ip["y"], m["y"]); hi = min(ip["y"] + ip["h"],
                                                    m["y"] + m["h"])
                if hi - lo > 20:
                    out.append(("ipad-right", m, lo, hi))
            if abs(ip["y"] - (m["y"] + m["h"])) <= 2:
                lo = max(ip["x"], m["x"]); hi = min(ip["x"] + ip["w"],
                                                    m["x"] + m["w"])
                if hi - lo > 20:
                    out.append(("ipad-top", m, lo, hi))
            if abs((ip["y"] + ip["h"]) - m["y"]) <= 2:
                lo = max(ip["x"], m["x"]); hi = min(ip["x"] + ip["w"],
                                                    m["x"] + m["w"])
                if hi - lo > 20:
                    out.append(("ipad-bottom", m, lo, hi))
        return out

    def _show_portals(self):
        portals = self._portals()
        for edge, m, lo, hi in portals:
            if edge in ("ipad-left", "ipad-right"):
                wx = self.ipad["x"] if edge == "ipad-left" \
                    else self.ipad["x"] + self.ipad["w"]
                x0, y0 = self.w2c(wx, lo); x1, y1 = self.w2c(wx, hi)
            else:
                wy = self.ipad["y"] if edge == "ipad-top" \
                    else self.ipad["y"] + self.ipad["h"]
                x0, y0 = self.w2c(lo, wy); x1, y1 = self.w2c(hi, wy)
            self.canvas.create_line(x0, y0, x1, y1, fill="#ffd43b", width=5)
        self.portal_edges = portals
        if portals:
            self.status.config(
                text=f"Portal set ({len(portals)} edge)",
                foreground="#B28DFF")
        else:
            self.status.config(
                text="Not touching any monitor — drag iPad to an edge",
                foreground="#E04A92")

    # ---- dragging with snap ----
    def _press(self, e):
        wx, wy = self.c2w(e.x, e.y)
        if (self.ipad["x"] <= wx <= self.ipad["x"] + self.ipad["w"] and
                self.ipad["y"] <= wy <= self.ipad["y"] + self.ipad["h"]):
            self.dragging = True
            self.drag_off = (wx - self.ipad["x"], wy - self.ipad["y"])

    def _drag(self, e):
        if not self.dragging:
            return
        wx, wy = self.c2w(e.x, e.y)
        self.ipad["x"] = int(wx - self.drag_off[0])
        self.ipad["y"] = int(wy - self.drag_off[1])
        self._redraw()

    def _release(self, e):
        if not self.dragging:
            return
        self.dragging = False
        self._snap()
        self._redraw()

    def _snap(self):
        ip = self.ipad
        TH = max(m["w"] for m in self.monitors) * 0.25  # snap distance
        best = None
        for m in self.monitors:
            # candidate: iPad to the right of monitor
            cands = [
                (m["x"] + m["w"], None, "x"),   # left edge to mon right
                (m["x"] - ip["w"], None, "x"),  # right edge to mon left
                (None, m["y"] + m["h"], "y"),   # top to mon bottom
                (None, m["y"] - ip["h"], "y"),  # bottom to mon top
            ]
            for cx, cy, axis in cands:
                if axis == "x":
                    d = abs(ip["x"] - cx)
                    if d < TH and (best is None or d < best[0]):
                        ny = self._align(ip["y"], ip["h"], m["y"], m["h"])
                        best = (d, cx, ny)
                else:
                    d = abs(ip["y"] - cy)
                    if d < TH and (best is None or d < best[0]):
                        nx = self._align(ip["x"], ip["w"], m["x"], m["w"])
                        best = (d, nx, cy)
        if best:
            _, nx, ny = best
            ip["x"], ip["y"] = int(nx), int(ny)

    @staticmethod
    def _align(pos, size, mpos, msize):
        # keep overlap; clamp so the iPad stays within the monitor span
        pos = max(mpos - size + 40, min(pos, mpos + msize - 40))
        return pos

    def _save(self):
        if not self._portals():
            # imported here, not at module scope: openspan imports THIS
            # module, so the other direction only works once it is loaded --
            # which it is by the time anyone can click Save.
            from openspan import dark_confirm
            if not dark_confirm(
                    self.root, "No portal",
                    "The iPad isn't touching any monitor, so there's no "
                    "edge to cross. Save anyway?"):
                return
        cfg = {
            "monitors": self.monitors,
            "ipad": self.ipad,
            "portals": [
                {"edge": e, "monitor": m["name"], "lo": lo, "hi": hi}
                for (e, m, lo, hi) in self._portals()
            ],
        }
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
        self.status.config(text=f"Saved → {os.path.basename(CONFIG_PATH)}",
                           foreground="#B28DFF")
        from openspan import dark_alert
        dark_alert(self.root, "Saved",
                   f"Arrangement saved to:\n{CONFIG_PATH}\n\n"
                   "Now run openspan_portal.py to use it.")


if __name__ == "__main__":
    root = tk.Tk()
    SetupApp(root)
    root.mainloop()
