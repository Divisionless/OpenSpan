#!/usr/bin/env python3
"""OpenSpan — one dark-mode window that runs the whole PC → iPad bridge.

Everything in one place: the live screen arrangement (drag the iPad to
where it sits), start/stop the bridge VM and input portal, broadcast for
pairing, hand off the Bluetooth radio, and edit the keymap.

Pure standard library (tkinter + ctypes). No dependencies.
"""

import copy
import json
import math
import os
import queue
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

# reuse the monitor enumeration + presets from the setup module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openspan_setup import enum_monitors, IPAD_PRESETS  # noqa: E402
from openspan_targets import (  # noqa: E402
    BASE_PORT, DESK_UNITS_PER_INCH, add_device, compute_adjacencies,
    compute_portals, device_by_id, physical_size,
    dedupe_display_ids, normalize_config, oriented_resolution,
    portal_signature, refresh_geometry, remove_device,
    rotate_display, set_layout_width, snap_rect_to_neighbors,
    validate_mac_displays,
)

# Frozen (OpenSpan.exe) or plain-Python: either way the data files live in
# a fixed layout — ROOT holds the configs/keys, ROOT\win the scripts. In the
# frozen dist folder the exe sits AT ROOT and __file__ points inside the
# bundle, so anchor on the executable there.
if getattr(sys, "frozen", False):
    ROOT = os.path.dirname(os.path.abspath(sys.executable))
    HERE = os.path.join(ROOT, "win")
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
    ROOT = os.path.abspath(os.path.join(HERE, ".."))
VBOX = r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
SETTINGS = os.path.join(ROOT, "openspan_settings.json")


def _load_boot_settings():
    """Read process-wide routing before any VM/daemon helpers are defined."""
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


_BOOT_SETTINGS = _load_boot_settings()
VM = str(_BOOT_SETTINGS.get("vm_name", "OpenSpan")).strip() or "OpenSpan"
APP_LABEL = (str(_BOOT_SETTINGS.get("app_label", "OpenSpan")).strip()
             or "OpenSpan")
DAEMON = (
    str(_BOOT_SETTINGS.get("daemon_host", "127.0.0.1")).strip()
    or "127.0.0.1",
    int(_BOOT_SETTINGS.get("daemon_port", 9955)),
)
def target_daemons(config):
    """{device_id: (host, port)} built from the CONFIG -- every device owns its
    own entrance point. Nothing is keyed by a device type, and no port is
    reserved: whatever the user created is what gets a lane."""
    return {
        device["id"]: (DAEMON[0], int(device.get("port", BASE_PORT)))
        for device in (config or {}).get("devices", [])
        if device.get("enabled", True)
    }


_DEVICE_ENDPOINTS = {}


def live_config():
    """Read the config from disk. Module-level helpers (VM start, daemon
    probes) run without the App instance, so the file is the shared truth."""
    try:
        with open(CONFIG, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def set_device_endpoints(config):
    """Publish the current device->port map for the module-level helpers."""
    global _DEVICE_ENDPOINTS
    _DEVICE_ENDPOINTS = target_daemons(config)
    return _DEVICE_ENDPOINTS


def _device_endpoint(device_id):
    endpoint = _DEVICE_ENDPOINTS.get(device_id)
    if endpoint:
        return endpoint
    for device in live_config().get("devices", []):
        if device.get("id") == device_id:
            return (DAEMON[0], int(device.get("port", BASE_PORT)))
    return DAEMON


def first_device_id(config=None):
    """The first enabled device, or None. Used where a call has no explicit
    device -- never assumes a particular one exists."""
    devices = (config or live_config()).get("devices", [])
    for device in devices:
        if device.get("enabled", True):
            return device.get("id")
    return None
KEY = os.path.join(ROOT, "id_openspan")
KEYMAP = os.path.join(ROOT, "openspan_keymap.json")
CONFIG = os.path.join(ROOT, "openspan_config.json")
LOG = os.path.join(ROOT, "portal.log")
AUDIO_SEND = os.path.join(HERE, "win_audio_send.py")
AUDIO_LOG = os.path.join(ROOT, "audio_send.log")
BT_PREFS = os.path.join(ROOT, "bt_prefs.json")
ICON = os.path.join(ROOT, "openspan.ico")

PYW = sys.executable
if PYW.lower().endswith("python.exe"):
    _c = PYW[:-len("python.exe")] + "pythonw.exe"
    if os.path.exists(_c):
        PYW = _c
# the portal and audio sender are separate PROCESSES: scripts under plain
# Python, role flags of the same exe when frozen (see openspan_launcher.py)
if getattr(sys, "frozen", False):
    PORTAL_CMD = [sys.executable, "--portal"]
    AUDIO_CMD = [sys.executable, "--audio"]
else:
    PORTAL_CMD = [PYW, os.path.join(HERE, "openspan_portal.py")]
    AUDIO_CMD = [PYW, AUDIO_SEND]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ---- dark theme palette ----
BG = "#14161c"
PANEL = "#1d212b"
CARD = "#232936"
FG = "#dfe4ee"
MUTED = "#8b93a7"
ACCENT = "#3fdc8a"
ACCENT_DIM = "#1f6f43"
WARN = "#f5c451"   # amber: connected but idle (portal off)
MON_FILL = "#26324c"
MON_LINE = "#4a6ea8"
IPAD_FILL = "#1f6f43"
IPAD_LINE = "#3fdc8a"
IPAD_OFF_FILL = "#2b313d"   # iPad box when NOT connected -> muted grey
IPAD_OFF_LINE = "#4a5468"
IPAD_IDLE_FILL = "#413615"  # connected but portal OFF -> amber (idle/paused)
IPAD_IDLE_LINE = "#f5c451"
PORTAL = "#a78bfa"       # crossing edges -- violet, so they read as routes
HOVER_FILL = "#1b1f2b"   # the detail card that appears on mouseover
HOVER_LINE = "#3a4358"
DANGER = "#e06c68"
SCRIM = "#0a0b0e"   # near-black overlay behind an in-frame modal
BORDER = "#39435a"  # card edge for the in-frame modal


# ---- themed dialogs ---------------------------------------------------------
# The native tk messagebox renders in the OS (light) theme, which clashes badly
# with the dark app. These are drop-in dark replacements that live INSIDE the
# app's look: dark background, themed buttons, a dark title bar, modal, centered
# over the parent. dark_confirm mirrors messagebox.askyesno (returns bool);
# dark_alert mirrors a single-button showwarning/showinfo.
def _paint_dark_titlebar(win):
    """Paint a window's Windows title bar dark (DWM immersive dark mode)."""
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(1)
        for attr in (20, 19):  # 20 = Win11/20H1+, 19 = older Win10
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:  # noqa: BLE001
        pass


class FrameModal(tk.Frame):
    """A modal that lives INSIDE the window, wearing a Toplevel's API.

    Nothing here may open a separate OS window. Windows places a new window, not
    the user, so on a multi-monitor desk the thing you just clicked for appears
    on a screen you were not looking at.

    It IS a Frame, so `tk.Label(modal, ...)` and `.pack()` behave exactly as they
    did inside a Toplevel; the window-manager calls a dialog makes -- title,
    transient, geometry, grab_set, protocol, destroy -- are answered here, so
    each dialog needed one line changed rather than a rewrite.

    Two of those calls do real work rather than nothing:

    * `bind` is installed on the WINDOW, not on this frame. A Toplevel sees
      events from its children; an intermediate frame does not. Left on the
      frame, `win.bind("<Return>", ok)` would silently stop firing the moment
      focus sat in the dialog's own entry box -- which is the only place focus
      ever is. It is taken back off at close, restoring whatever was bound
      before.
    * `geometry("900x420")` sizes the card, clamped to the window it now lives
      in. Position (`"+x+y"`) is discarded: a card in the middle of the window
      has no screen coordinate to be moved to.
    """

    def __init__(self, parent, **kw):
        kw.setdefault("bg", BG)
        self._host = parent.winfo_toplevel()
        self._scrim = tk.Frame(self._host, bg=SCRIM)
        self._scrim.place(x=-20, y=-20, relwidth=1, relheight=1,
                          width=40, height=40)
        self._scrim.lift()
        # A click outside the card does NOT close it. The Toplevels these
        # replaced had no click-outside gesture at all, and three of the four
        # hold typed values -- one stray click in the dimmed area would discard
        # a display table just filled in, with no undo and no warning. Escape
        # and the dialog's own Cancel are the ways out.
        self._scrim.bind("<Button-1>", lambda _e: "break")
        self._card = tk.Frame(self._scrim, bg=kw["bg"],
                              highlightbackground=BORDER, highlightthickness=1)
        self._card.place(relx=0.5, rely=0.5, anchor="center")
        self._card.bind("<Button-1>", lambda _e: "break")
        super().__init__(self._card, **kw)
        self.pack(fill="both", expand=True)
        self._title = ""
        self._on_close = None
        self._closed = False
        self._binds = {}
        self._prev_grab = None
        self._want = (0, 0)
        self.bind("<Escape>", lambda _e: self._dismiss())

    # ---- the window-manager surface the dialogs already call --------------
    def title(self, text=None):
        if text is not None:
            self._title = str(text)
        return self._title

    def protocol(self, _name, func=None):
        self._on_close = func

    def transient(self, *_a):
        return None

    def resizable(self, *_a):
        return None

    def minsize(self, *_a):
        return None

    def withdraw(self):
        return None

    def deiconify(self):
        return None

    def iconify(self):
        return None

    def attributes(self, *_a):
        return None

    def configure(self, cnf=None, **kw):
        # the card is the visible panel; a dialog recolouring itself must not
        # leave a rim of the old colour around its own edge
        colour = kw.get("bg", kw.get("background"))
        if colour:
            self._card.configure(bg=colour)
        return super().configure(cnf, **kw)

    config = configure

    def geometry(self, spec=None):
        # Remembered, not applied: a Toplevel's geometry was a STARTING size on
        # a window the user could then drag bigger. A card cannot be dragged, so
        # the number is treated as a floor and the real size is settled in
        # _fit(), once the dialog has actually been built.
        if spec:
            want = re.match(r"^(\d+)x(\d+)", str(spec))
            if want:            # "+x+y" is a screen position we do not have
                self._want = (int(want.group(1)), int(want.group(2)))
        return ""

    def _fit(self):
        """Big enough for everything in it; never bigger than the window.

        Pinning the requested height instead of measuring cost the display
        editor its buttons: seven screens of rows pushed Save and Cancel past
        420px, and with the window gone there was nothing left to drag bigger.
        Contents first, window as the ceiling.
        """
        self._card.update_idletasks()
        self._host.update_idletasks()
        self._card.place_configure(
            width=min(max(self._want[0], self._card.winfo_reqwidth()),
                      max(240, self._host.winfo_width() - 40)),
            height=min(max(self._want[1], self._card.winfo_reqheight()),
                       max(200, self._host.winfo_height() - 40)))

    def bind(self, sequence=None, func=None, add=None):
        if sequence is None or func is None or add:
            return super().bind(sequence, func, add)
        if sequence not in self._binds:
            self._binds[sequence] = self._host.bind(sequence)
        self._host.bind(sequence, func)
        return ""

    def grab_set(self):
        # every one of these dialogs grabs as its last build step, which makes
        # this the one moment when the card is complete and can be measured
        self._fit()
        try:
            self._prev_grab = self._host.grab_current()
            self._scrim.grab_set()
        except tk.TclError:
            pass

    def grab_release(self):
        try:
            self._scrim.grab_release()
            if self._prev_grab is not None:
                self._prev_grab.grab_set()   # a modal opened OVER a modal
        except tk.TclError:
            pass
        self._prev_grab = None

    def focus_force(self):
        try:
            self.focus_set()
        except tk.TclError:
            pass

    def _dismiss(self):
        if self._on_close:
            self._on_close()
        else:
            self.destroy()

    def destroy(self):
        if self._closed:
            return
        self._closed = True
        for sequence, previous in self._binds.items():
            try:
                self._host.unbind(sequence)
                if previous:
                    self._host.bind(sequence, previous)
            except tk.TclError:
                pass
        self._binds.clear()
        self.grab_release()
        try:
            self._scrim.destroy()
        except tk.TclError:
            pass


def _dialog(parent, title, message, buttons):
    """Show a dark modal dialog INSIDE the app window — an in-frame overlay, not
    a separate OS window — and block until a button is chosen. `buttons` is a
    list of (text, value, style), primary first; returns the chosen value (the
    last button's value on Escape or a click outside the card). Enter = first
    button. Keeps a synchronous return so callers stay unchanged."""
    top = parent.winfo_toplevel()
    # re-entrancy guard: a second trigger while a modal is open (e.g. the title
    # X hit twice) must not stack a second overlay
    if getattr(top, "_os_modal_open", False):
        return buttons[-1][1]
    top._os_modal_open = True

    result = {"v": buttons[-1][1]}
    done_var = tk.StringVar(master=top, value="")

    def done(v):
        if done_var.get():
            return  # first click wins; ignore the rest
        result["v"] = v
        done_var.set("1")

    # full-window scrim: dims the app and swallows clicks (modal within frame).
    # Overhang every edge by 20px (relwidth=1 + width=40, offset -20) so no
    # sliver of the app can peek past it regardless of borders/geometry.
    scrim = tk.Frame(top, bg=SCRIM)
    scrim.place(x=-20, y=-20, relwidth=1, relheight=1, width=40, height=40)
    scrim.lift()
    scrim.bind("<Button-1>", lambda e: done(buttons[-1][1]))

    # centered card (same interior look as the app: BG panel, themed buttons)
    card = tk.Frame(scrim, bg=BG, highlightbackground=BORDER,
                    highlightthickness=1)
    card.place(relx=0.5, rely=0.44, anchor="center")
    card.bind("<Button-1>", lambda e: "break")  # a card click is not a cancel
    inner = tk.Frame(card, bg=BG)
    inner.pack(padx=26, pady=22)
    tk.Label(inner, text=title, bg=BG, fg=FG, justify="left",
             font=("Segoe UI Semibold", 13)).pack(anchor="w")
    if message:
        try:  # wrap to the window so a small/compact window still fits
            wl = max(240, min(400, top.winfo_width() - 80))
        except tk.TclError:
            wl = 360
        tk.Label(inner, text=message, bg=BG, fg=MUTED, justify="left",
                 wraplength=wl, font=("Segoe UI", 10)).pack(anchor="w",
                                                            pady=(10, 0))
    bar = tk.Frame(inner, bg=BG)
    bar.pack(anchor="e", pady=(20, 0))
    focus_btn = None
    for i, (text, value, style) in enumerate(buttons):
        b = ttk.Button(bar, text=text, style=style,
                       command=lambda v=value: done(v))
        b.pack(side="left", padx=(0, 8) if i < len(buttons) - 1 else 0)
        if i == 0:
            focus_btn = b

    # A confirm can open OVER a FrameModal (the display editor asks one). Take
    # note of what is holding the grab so it can be given back -- releasing
    # without restoring leaves the dialog underneath looking modal while the
    # keyboard is free to wander behind it.
    prev_grab = top.grab_current()
    prev_ret, prev_esc = top.bind("<Return>"), top.bind("<Escape>")
    top.bind("<Return>", lambda e: done(buttons[0][1]))
    top.bind("<Escape>", lambda e: done(buttons[-1][1]))
    try:
        scrim.grab_set()  # keyboard modality; the scrim already blocks the mouse
    except tk.TclError:
        pass
    if focus_btn is not None:
        focus_btn.focus_set()
    try:
        top.wait_variable(done_var)
    finally:
        try:
            scrim.grab_release()
            if prev_grab is not None:
                prev_grab.grab_set()
        except tk.TclError:
            pass
        top.unbind("<Return>")
        top.unbind("<Escape>")
        if prev_ret:
            top.bind("<Return>", prev_ret)
        if prev_esc:
            top.bind("<Escape>", prev_esc)
        scrim.destroy()
        top._os_modal_open = False
    return result["v"]


def dark_confirm(parent, title, message, yes="Yes", no="No"):
    """Dark drop-in for messagebox.askyesno — returns True on yes, else False."""
    return _dialog(parent, title, message,
                   [(yes, True, "Accent.TButton"), (no, False, "TButton")])


def dark_alert(parent, title, message, ok="OK"):
    """Dark drop-in for messagebox.showwarning/showinfo — single OK button."""
    _dialog(parent, title, message, [(ok, True, "Accent.TButton")])


PROFILE_DIR = os.path.join(ROOT, "profiles")
# What a profile deliberately does NOT carry. These follow the HARDWARE, not the
# situation: a radio is a physical dongle, a port is a lane on the guest, and the
# bonds behind them live on the guest per radio. Two arrangements of the same
# desk must not fight over one lane.
MACHINE_FIELDS = ("radio", "port")


def profile_name(name):
    """The name an arrangement is actually known by.

    The file is named after the arrangement, so a name that is not a legal
    filename produces a file whose stem no longer matches it -- and every
    comparison between the two goes quietly wrong at once. "Mac 4K (day)" lands
    in "Mac 4K _day_.json", after which the write-through stops firing (its
    guard asks whether the name is in list_profiles(), which returns stems),
    Delete does nothing, and "Desk 2.0" and "Desk 2 0" overwrite each other.
    Every edit made after that is lost at the next switch, silently.

    So the name is sanitised ONCE, here, and the sanitised form IS the name from
    that moment on -- shown in the box, stored in the config, written into the
    file. There is only one string.
    """
    return ("".join(c if c.isalnum() or c in " -_" else "_"
                    for c in str(name)).strip() or "unnamed")


def _profile_path(name):
    return os.path.join(PROFILE_DIR, profile_name(name) + ".json")


def list_profiles():
    try:
        return sorted(f[:-5] for f in os.listdir(PROFILE_DIR)
                      if f.endswith(".json"))
    except OSError:
        return []


def save_profile(config, name):
    """Snapshot the arrangement under a name. Machine fields are dropped.

    Returns the name it was actually saved as, which is the only one that
    should be used afterwards."""
    name = profile_name(name)
    snapshot = copy.deepcopy(config)
    snapshot.pop("portals", None)      # derived, and recomputed on load
    # `links` deliberately KEPT. normalize_config reads its absence as "this
    # config predates the adjacency graph" and re-runs a one-time snap that
    # nudges screens toward their neighbours -- so stripping it would let a
    # saved arrangement move the very screens it was saved to preserve.
    for device in snapshot.get("devices", []):
        for field in MACHINE_FIELDS:
            device.pop(field, None)
    snapshot["profile"] = name
    os.makedirs(PROFILE_DIR, exist_ok=True)
    with open(_profile_path(name) + ".new", "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, indent=2)
    os.replace(_profile_path(name) + ".new", _profile_path(name))
    return name


def load_profile(name, current):
    """A saved arrangement, wearing THIS machine's radios and ports.

    Which dongle drives which device is a fact about the desk, not about the
    arrangement -- so it is carried over from what is running now, matched by
    device id, and never restored from the file."""
    with open(_profile_path(name), encoding="utf-8") as handle:
        loaded = json.load(handle)
    hardware = {device.get("id"): device
                for device in current.get("devices", [])}
    for device in loaded.get("devices", []):
        live = hardware.get(device.get("id"), {})
        for field in MACHINE_FIELDS:
            if field in live:
                device[field] = live[field]
    loaded["profile"] = profile_name(name)
    return loaded


def delete_profile(name):
    try:
        os.remove(_profile_path(name))
        return True
    except OSError:
        return False


def dark_prompt(parent, title, message, default=""):
    """Dark single-line text prompt. Returns the string, or None on cancel.
    Native dialogs are light-themed, so this stays in the app's own look."""
    win = FrameModal(parent)
    win.title(title)
    win.configure(bg=CARD)
    win.transient(parent)
    win.resizable(False, False)

    tk.Label(win, text=title, bg=CARD, fg=FG,
             font=("Segoe UI Semibold", 11)).pack(
        anchor="w", padx=18, pady=(16, 2))
    tk.Label(win, text=message, bg=CARD, fg=MUTED, font=("Segoe UI", 9),
             wraplength=380, justify="left").pack(anchor="w", padx=18)
    var = tk.StringVar(value=str(default or ""))
    entry = ttk.Entry(win, textvariable=var, width=40)
    entry.pack(fill="x", padx=18, pady=(10, 4))
    result = {"v": None}
    buttons = tk.Frame(win, bg=CARD)
    buttons.pack(anchor="e", padx=18, pady=(6, 16))

    def ok(*_a):
        result["v"] = var.get()
        win.destroy()

    ttk.Button(buttons, text="OK", style="Accent.TButton",
               command=ok).pack(side="left", padx=(0, 6))
    ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="left")
    win.bind("<Return>", ok)
    win.bind("<Escape>", lambda *_a: win.destroy())
    win.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_width()) // 2
    y = parent.winfo_rooty() + 120
    win.geometry(f"+{max(0, x)}+{max(0, y)}")
    win.deiconify()
    entry.focus_set()
    entry.select_range(0, "end")
    win.grab_set()
    parent.wait_window(win)
    return result["v"]


def _theme_startup_buttons():
    """Style the small pre-flight window without constructing the full app."""
    st = ttk.Style()
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    st.configure("TButton", background=CARD, foreground=FG,
                 bordercolor=CARD, focuscolor=CARD, relief="flat",
                 padding=8, font=("Segoe UI", 10))
    st.map("TButton", background=[("active", "#2d3444")])
    st.configure("Accent.TButton", background=ACCENT_DIM,
                 foreground="#eafff3", font=("Segoe UI Semibold", 10))
    st.map("Accent.TButton", background=[("active", "#2a8f5c")])
    st.configure("Danger.TButton", background="#53292a",
                 foreground="#ffd9d6", font=("Segoe UI Semibold", 10))
    st.map("Danger.TButton", background=[("active", "#6e3335")])


def _elevation_gate():
    """Ask what to do before keys, Bluetooth, audio, or the VM are touched."""
    root = tk.Tk()
    try:
        root.title(f"{APP_LABEL} — startup choice")
        root.geometry("700x320")
        root.minsize(640, 280)
        root.configure(bg=BG)
        try:
            root.iconbitmap(ICON)
        except Exception:  # noqa: BLE001
            pass
        _theme_startup_buttons()
        root.update_idletasks()
        _paint_dark_titlebar(root)
        root.lift()
        root.focus_force()
        # Close is deliberately last: Escape or clicking outside the card must
        # be fail-closed and must never count as permission to boot the bridge.
        return _dialog(
            root,
            "OpenSpan is not running as administrator",
            "Administrator mode keeps keyboard and mouse bridging alive while "
            "an elevated window is focused.\n\n"
            "Choose Restart as administrator to continue normally. Close "
            "program exits without starting the VM or touching Bluetooth. "
            "Ignore starts OpenSpan in normal mode for this run only.",
            [("Restart as administrator", "restart", "Accent.TButton"),
             ("Ignore", "ignore", "TButton"),
             ("Close program", "close", "Danger.TButton")])
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def _elevated_launch_spec():
    """Return (program, parameters) for the same app in an elevated process."""
    if getattr(sys, "frozen", False):
        program = os.path.abspath(sys.executable)
        args = list(sys.argv[1:])
    else:
        program = os.path.abspath(sys.executable)
        args = [os.path.abspath(__file__), *sys.argv[1:]]
    return program, subprocess.list2cmdline(args)


def _independent_frozen_env():
    """Give independently launched frozen roles their own one-file runtime.

    PyInstaller 6.9+ assumes a child invocation of the same executable is a
    worker that can reuse its parent's _MEI directory.  OpenSpan's audio,
    portal, and elevated replacement are independent processes; sharing that
    directory lets an exiting role race the parent's cleanup and can surface a
    "Failed to remove temporary directory" warning.
    """
    if not getattr(sys, "frozen", False):
        return None
    env = os.environ.copy()
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def _terminate_role_process(proc, timeout=4):
    """Stop a spawned role and its PyInstaller one-file child.

    Terminating only the Popen handle kills the one-file parent but can leave
    the extracted --portal/--audio child alive. Repeated display saves then
    stack low-level hook processes. Use Windows' process-tree termination for
    frozen roles and retain the ordinary fallback for tests/plain Python.
    """
    if not proc or proc.poll() is not None:
        return
    pid = getattr(proc, "pid", None)
    if os.name == "nt" and pid:
        taskkill = os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"),
            "System32", "taskkill.exe")
        try:
            subprocess.run(
                [taskkill, "/PID", str(int(pid)), "/T", "/F"],
                capture_output=True, text=True, timeout=timeout,
                creationflags=NO_WINDOW)
            try:
                proc.wait(timeout=timeout)
            except Exception:  # noqa: BLE001
                pass
            return
        except Exception:  # noqa: BLE001
            pass
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        pass


def _launch_elevated():
    """Request UAC elevation. The caller has already released the app mutex."""
    reset_name = "PYINSTALLER_RESET_ENVIRONMENT"
    previous = os.environ.get(reset_name)
    try:
        import ctypes
        program, parameters = _elevated_launch_spec()
        if getattr(sys, "frozen", False):
            os.environ[reset_name] = "1"
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", program, parameters, ROOT, 1)
        return int(result) > 32
    except Exception:  # noqa: BLE001
        return False
    finally:
        if previous is None:
            os.environ.pop(reset_name, None)
        else:
            os.environ[reset_name] = previous


def _show_elevation_launch_failed():
    """Report a cancelled/failed UAC request without starting the full app."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None,
            "OpenSpan was not started. The administrator request was cancelled "
            "or could not be opened.",
            APP_LABEL,
            0x10)
    except Exception:  # noqa: BLE001
        pass


# ---- console log sink -------------------------------------------------------
# Every command the app runs (VBoxManage + ssh into the VM) is mirrored to the
# right-hand console panel. The App installs the sink once its console exists;
# until then _emit is a no-op. Routine health polls pass quiet=True so the 3s
# tick never floods the console -- only meaningful commands show.
_LOG_SINK = None


def set_log_sink(fn):
    global _LOG_SINK
    _LOG_SINK = fn


def _emit(kind, text):
    fn = _LOG_SINK
    if fn and text:
        try:
            fn(kind, str(text))
        except Exception:  # noqa: BLE001
            pass


def vbox(*args, quiet=False):
    if not quiet:
        _emit("cmd", "VBoxManage " + " ".join(str(a) for a in args))
    try:
        r = subprocess.run([VBOX, *args], capture_output=True, text=True,
                           timeout=30, creationflags=NO_WINDOW)
        if not quiet:
            out = (r.stderr or r.stdout or "").strip()
            _emit("err" if r.returncode else "ok", out[:240] or "ok")
        return r
    except Exception as e:  # noqa: BLE001
        if not quiet:
            _emit("err", str(e)[:240])

        class R:
            returncode = 1
            stdout = ""
            stderr = str(e)
        return R()


# ---- the radios the VM is supposed to own -----------------------------------
# A Bluetooth dongle is a USB device the guest owns by passthrough, and Windows
# will happily take it back. Unplug one and plug it in again and it lands with a
# Windows driver bound to it -- "Busy" -- and VirtualBox only auto-captures a
# filtered device at the moment it ARRIVES. So the dongle is sitting right there,
# visible, matching an active filter, and the guest cannot see it at all.
#
# From inside the app that looked exactly like a device that would not connect.
# There was no way to tell the difference, because nothing here had ever looked
# at the host's USB list. These four functions are that look.


def parse_usb_host(text):
    """`VBoxManage list usbhost` -> a record per device on this machine.

    Blank-line separated stanzas of "Key: value". Only the fields that decide
    whether a device is one of ours and whether the guest can have it.
    """
    devices, current = [], {}
    for line in (text or "").splitlines():
        if not line.strip():
            if current.get("uuid"):
                devices.append(current)
            current = {}
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "uuid":
            current["uuid"] = value
        elif key == "vendorid":
            current["vendor"] = value.split()[0].lower()
        elif key == "productid":
            current["product_id"] = value.split()[0].lower()
        elif key == "product":
            current["name"] = value
        elif key == "manufacturer":
            current["maker"] = value
        elif key == "serialnumber":
            current["serial"] = value
        elif key == "port":
            current["port"] = value
        elif key == "current state":
            current["state"] = value
    if current.get("uuid"):
        devices.append(current)
    return devices


def parse_usb_filters(info, keep_serial=False):
    """The VM's USB filters, from `showvminfo --machinereadable`.

    A filter is the machine's own statement of "this device belongs to me", which
    makes it the right definition of a radio to reclaim -- better than a list of
    vendor ids hardcoded here, because the user edits the filters and never edits
    this file.
    """
    rows = {}
    for line in (info or "").splitlines():
        key, _, value = line.partition("=")
        value = value.strip().strip('"')
        for field, name in (("USBFilterName", "name"),
                            ("USBFilterVendorId", "vendor"),
                            ("USBFilterProductId", "product_id"),
                            ("USBFilterSerialNumber", "serial"),
                            ("USBFilterActive", "active")):
            if key.startswith(field) and key[len(field):].isdigit():
                rows.setdefault(key[len(field):], {})[name] = value
    filters = []
    for row in rows.values():
        if row.get("active", "on") == "off":
            continue
        vendor = (row.get("vendor") or "").lower()
        product = (row.get("product_id") or "").lower()
        if not vendor:
            continue
        spec = {
            "name": row.get("name", ""),
            "vendor": vendor if vendor.startswith("0x") else "0x" + vendor,
            "product_id": (product if product.startswith("0x")
                           else "0x" + product) if product else "",
        }
        if keep_serial:
            spec["serial"] = (row.get("serial") or "").strip()
        filters.append(spec)
    return filters


def parse_usb_attached(info):
    """The UUIDs the VM currently holds, from `showvminfo --machinereadable`."""
    held = set()
    for line in (info or "").splitlines():
        key, _, value = line.partition("=")
        if key.startswith("USBAttachActive") and key[15:].isdigit():
            uuid = value.strip().strip('"')
            if uuid:
                held.add(uuid.lower())
    return held


def radio_report(usbhost, info):
    """Which of this machine's radios the VM has, and which it has lost.

    "lost" means: a device on the host that matches one of the VM's own active
    USB filters, and that the VM is not holding. That is precisely the state a
    replugged dongle lands in, and precisely the state that used to be
    indistinguishable from a device that just would not connect.
    """
    filters = parse_usb_filters(info)
    held = parse_usb_attached(info)
    mine, lost = [], []
    for device in parse_usb_host(usbhost):
        match = next(
            (f for f in filters
             if f["vendor"] == device.get("vendor")
             and (not f["product_id"]
                  or f["product_id"] == device.get("product_id"))),
            None)
        if not match:
            continue
        device = dict(device, filter=match["name"])
        mine.append(device)
        if device["uuid"].lower() not in held:
            lost.append(device)
    return {"mine": mine, "lost": lost, "held": held, "filters": filters}


def serial_to_radio(serial):
    """A Bluetooth dongle's USB serial number IS its adapter address.

    Both TP-Link dongles on this desk report `ACA7F1299FCB` and `3C6AD23CD44E`,
    which are exactly the two radio addresses in the config with the colons
    taken out. That is what makes a dongle identifiable from the HOST, with the
    VM down and the guest unreachable -- the one moment identifying it matters.

    Returns "" for anything that is not twelve hex digits, because this is a
    convention and not a guarantee; a dongle that does not follow it is still
    handled, just not named after the device it serves.
    """
    text = str(serial or "").replace(":", "").replace("-", "").strip().upper()
    if len(text) != 12:
        return ""
    try:
        int(text, 16)
    except ValueError:
        return ""
    return ":".join(text[i:i + 2] for i in range(0, 12, 2))


def usb_label(device, config=None):
    """How to name a dongle to someone who has to go and find it.

    "TP-Link Bluetooth USB Adapter" is not something you can pick out of two
    identical dongles in the back of a machine. "the dongle for Managed Laptop"
    is. So the device it serves is used when the serial identifies it, and the
    product string only when nothing better exists.
    """
    name = (device.get("name") or device.get("maker") or "").strip()
    name = name or f"{device.get('vendor', '?')}:{device.get('product_id', '?')}"
    radio = serial_to_radio(device.get("serial"))
    if radio and config:
        for target in config.get("devices", []):
            if str(target.get("radio", "")).upper() == radio:
                return f"{target.get('name') or target.get('id')}’s dongle"
    return name


def radio_filter_plan(config, usbhost, info):
    """Which radios need their filter pinned to one specific dongle.

    THE ROOT CAUSE of this whole mess. Both TP-Link filters matched
    `2357:0604` and nothing else, so two identical dongles arriving together
    raced two identical filters -- which is why replugging both recovered one and
    left the other captured-away-from-Windows-but-never-delivered, a state no
    VBoxManage verb can undo (`usbdetach` answers "not attached to this
    machine").

    A filter carrying the dongle's serial number matches exactly one device, so
    there is nothing left to race. `usbfilter modify` accepts it on a RUNNING VM,
    which makes this a repair the app can simply do.

    Returns [{index, name, serial, label}] -- `index` is 0-based, as the
    `usbfilter` command wants, while `--machinereadable` numbers from 1.
    """
    filters = parse_usb_filters(info, keep_serial=True)
    ours = [d for d in parse_usb_host(usbhost) if d.get("serial")]
    # Ambiguity only exists WITHIN a group of filters that match on the same
    # thing, so group first. This is an assignment, not a match: two filters and
    # two identical dongles have to be paired off one-to-one, and a per-filter
    # "which device does this match" question has two answers and no way to
    # choose -- which is the same ambiguity VirtualBox itself is losing to.
    groups = {}
    for slot, spec in enumerate(filters):
        groups.setdefault((spec["vendor"], spec["product_id"]), []).append(
            (slot, spec))
    plan = []
    for (vendor, product), members in groups.items():
        if len(members) < 2:
            continue            # a lone filter has nothing to be confused with
        spoken_for = {m[1]["serial"].upper()
                      for m in members if m[1].get("serial")}
        free = sorted(
            (d for d in ours
             if d.get("vendor") == vendor
             and (not product or d.get("product_id") == product)
             and d["serial"].upper() not in spoken_for),
            key=lambda d: d["serial"].upper())
        for slot, spec in sorted(members, key=lambda m: m[0]):
            if spec.get("serial") or not free:
                continue
            device = free.pop(0)
            plan.append({"index": slot, "name": spec["name"],
                         "serial": device["serial"],
                         "label": usb_label(device, config)})
    return plan


def pin_radio_filters(plan):
    """Pin each planned filter to its dongle. Returns (pinned, failed)."""
    pinned, failed = [], []
    for step in plan:
        result = vbox("usbfilter", "modify", str(step["index"]),
                      "--target", VM, "--serialnumber", step["serial"])
        if result.returncode:
            failed.append((step["name"],
                           (result.stderr or result.stdout or "").strip()[-160:]))
        else:
            pinned.append(f"{step['name']} → {step['label']}")
    return pinned, failed


def ensure_ssh_key():
    """Generate the host<->VM SSH key on first run if it's missing, so a
    fresh clone can reach its own bridge (the private key is gitignored and
    must never ship). ed25519, no passphrase -- this is an unattended
    loopback to a local VM. Returns True if a key exists/was made.

    The PUBLIC half (id_openspan.pub) still has to land in the VM's
    /root/.ssh/authorized_keys; that's the VM provisioner's job
    (guest/install-authorized-key.sh) -- this only guarantees the host has
    a key to offer. Never regenerates an existing key."""
    if os.path.exists(KEY):
        return harden_ssh_key_acl()
    try:
        r = subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", KEY,
             "-C", "openspan-host"],
            capture_output=True, text=True, timeout=30,
            creationflags=NO_WINDOW)
        if r.returncode == 0 and os.path.exists(KEY):
            if not harden_ssh_key_acl():
                return False
            _emit("event", "generated a new bridge SSH key "
                           f"({os.path.basename(KEY)}). Install its .pub in "
                           "the VM (the provisioner does this).")
            return True
        _emit("err", "couldn't generate the SSH key: "
                     + (r.stderr or "ssh-keygen failed")[:180])
    except FileNotFoundError:
        _emit("err", "ssh-keygen not found — install the Windows OpenSSH "
                     "client, or drop an id_openspan key in the app folder.")
    except Exception as e:  # noqa: BLE001
        _emit("err", f"SSH key generation failed: {e}")
    return False


def harden_ssh_key_acl():
    """Make the private key acceptable to Windows OpenSSH.

    Copying a project tree onto NTFS can make the key inherit broad
    Authenticated Users/Users permissions. OpenSSH then ignores the key and,
    without BatchMode, sits invisibly at a password prompt until our subprocess
    timeout. Keep only the launching user, SYSTEM, and Administrators.
    """
    if os.name != "nt" or not os.path.exists(KEY):
        return os.path.exists(KEY)
    domain = os.environ.get("USERDOMAIN", "").strip()
    username = os.environ.get("USERNAME", "").strip()
    principal = f"{domain}\\{username}" if domain and username else username
    if not principal:
        return False
    icacls = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32", "icacls.exe")
    flags = {"capture_output": True, "text": True, "timeout": 15,
             "creationflags": NO_WINDOW}
    try:
        steps = [
            [icacls, KEY, "/inheritance:r"],
            [icacls, KEY, "/remove:g", "*S-1-5-11", "*S-1-5-32-545"],
            [icacls, KEY, "/grant:r", f"{principal}:(M)",
             "*S-1-5-18:(F)", "*S-1-5-32-544:(F)"],
        ]
        results = [subprocess.run(step, **flags) for step in steps]
        return results[0].returncode == 0 and results[-1].returncode == 0
    except Exception as exc:  # noqa: BLE001
        _emit("err", f"couldn't secure the bridge SSH key: {exc}")
        return False


def _ssh_argv(cmd):
    """One non-interactive SSH contract for every guest operation."""
    return [
        "ssh", "-p", "2222", "-i", KEY,
        "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes",
        "-o", "NumberOfPasswordPrompts=0",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=6",
        "root@127.0.0.1", cmd,
    ]


def ssh_guest(cmd, timeout=20, quiet=False, show_result=True):
    if not quiet:
        _emit("cmd", "ssh: " + " ".join(cmd.split())[:240])
    try:
        r = subprocess.run(
            _ssh_argv(cmd),
            capture_output=True, text=True, timeout=timeout,
            creationflags=NO_WINDOW)
        if not quiet and show_result:
            out = (r.stdout or r.stderr or "").strip()
            if out:
                _emit("err" if r.returncode else "ok", out[:240])
        return r
    except Exception as e:  # noqa: BLE001
        if not quiet:
            _emit("err", str(e)[:240])

        class R:
            returncode = 1
            stdout = ""
            stderr = str(e)
        return R()


def read_radio_state():
    """One report on the radios, from two read-only VBoxManage calls."""
    return radio_report(
        vbox("list", "usbhost", quiet=True).stdout,
        vbox("showvminfo", VM, "--machinereadable", quiet=True).stdout)


# What VirtualBox says when its host-side device object still has an unfinished
# request against it. Retrying cannot clear it -- the device has to be re-created,
# which means a physical replug or a VM restart.
_USB_WEDGED = "busy with a previous request"
WEDGED_ADVICE = (
    "VirtualBox still holds an unfinished request for it — what a dongle "
    "unplugged while the VM had it leaves behind. No command can clear it: even "
    "usbdetach refuses, because as far as the VM is concerned the device was "
    "never attached. "
)
ATTACH_SETTLE = 1.5     # VirtualBox moves a device between owners asynchronously


REPLUG_ADVICE = (
    "Unplug it and plug it back in — the VM is running, so its filter catches "
    "it as it arrives. If both need it, do them ONE AT A TIME and wait for each "
    "to appear: two identical dongles arriving together is what wedged this in "
    "the first place."
)


def repair_radios(config=None, settle=None):
    """Everything the app can do about a missing radio, cheapest first.

    Written for someone who does not have the person who wrote it sitting next
    to them. Each rung either fixes it or hands back a sentence naming the one
    physical thing left to do — never "it failed".

      1. PIN THE FILTERS. Free, no restart, and it is the actual cause: two
         filters matching the same vendor:product cannot tell two identical
         dongles apart, so arrival capture races and loses one.
      2. ATTACH what the VM has lost, and verify the VM took it.
      3. Anything still missing gets named by the DEVICE it serves, with the
         replug instruction. A captured-but-not-delivered dongle cannot be
         rescued by any command — `usbdetach` refuses it as "not attached to
         this machine" — so at that point hands are the only remaining tool and
         saying so plainly is the whole job.

    Returns a dict the UI turns into one line and a few log entries.
    """
    config = config or {}
    info = vbox("showvminfo", VM, "--machinereadable", quiet=True).stdout
    host = vbox("list", "usbhost", quiet=True).stdout
    plan = radio_filter_plan(config, host, info)
    pinned, pin_failed = pin_radio_filters(plan) if plan else ([], [])
    recovered, failed = reclaim_radios(
        config=config, **({} if settle is None else {"settle": settle}))
    state = read_radio_state()
    return {
        "pinned": pinned, "pin_failed": pin_failed,
        "recovered": recovered, "failed": failed,
        "total": len(state["mine"]), "still_lost": [
            usb_label(d, config) for d in state["lost"]],
    }


def reclaim_radios(settle=ATTACH_SETTLE, attempts=2, verify=None, config=None):
    """Hand every lost radio back to the VM. Returns (recovered, failed).

    **Success is the VM holding the device, not VBoxManage returning zero.**
    The first version of this believed the exit code, and the exit code is about
    whether the *request* was accepted. On Doug's desk the request was accepted,
    the dongles were taken off Windows, the handoff to the guest never completed,
    and the app reported that it had attached them -- so the panel went on saying
    "1 of 3" while claiming success, and the honest reading of that from outside
    is "nothing happened". Which is what he said.

    The transfer is asynchronous, so it is given time and then checked, and what
    is checked is the VM's own list of attached devices.

    Nothing here scans, pairs or connects. It puts the USB device where the guest
    can see it, and then says to press Connect.
    """
    verify = verify or (lambda: parse_usb_attached(
        vbox("showvminfo", VM, "--machinereadable", quiet=True).stdout))
    recovered, failed = [], []
    for device in read_radio_state()["lost"]:
        label, uuid = usb_label(device, config), device["uuid"].lower()
        reason = "not attempted"
        for attempt in range(max(1, attempts)):
            result = vbox("controlvm", VM, "usbattach", device["uuid"],
                          quiet=attempt > 0)
            text = ((result.stderr or "") + " "
                    + (result.stdout or "")).strip().lower()
            if _USB_WEDGED in text:
                reason = WEDGED_ADVICE + REPLUG_ADVICE
                break                      # retrying provably cannot help
            if result.returncode:
                reason = ((result.stderr or result.stdout or "").strip()[-200:]
                          or "VBoxManage would not attach it")
                continue
            time.sleep(settle)
            if uuid in verify():
                reason = ""
                break
            reason = ("VirtualBox accepted the request but the VM never took "
                      "the device. " + REPLUG_ADVICE)
        if reason:
            failed.append((label, reason))
        else:
            recovered.append(label)
    return recovered, failed


def vm_running():
    return f'"{VM}"' in (vbox("list", "runningvms", quiet=True).stdout or "")


def _has_nat_forward(info, host_port, guest_port):
    """Return True when a VirtualBox machine-readable NAT rule maps the ports."""
    for line in (info or "").splitlines():
        if not line.startswith("Forwarding(") or '="' not in line:
            continue
        value = line.split('="', 1)[1].rstrip('"')
        fields = value.split(",")
        if len(fields) >= 6 and fields[1].lower() == "tcp":
            if fields[3] == str(host_port) and fields[5] == str(guest_port):
                return True
    return False


def ensure_device_forwards(config, info=None):
    """Expose EVERY device's daemon port through this VM's NAT adapter.

    One rule per device -- each is an independent entrance point. Added live
    when the VM is running, or persisted before the next start when it is
    powered off. Existing rules are left untouched.
    """
    ok = True
    if info is None:
        info = vbox("showvminfo", VM, "--machinereadable",
                    quiet=True).stdout or ""
    for device_id, (host, port) in target_daemons(config).items():
        if host not in ("127.0.0.1", "localhost"):
            continue
        if not _ensure_one_forward(device_id, port, info):
            ok = False
    return ok


def _ensure_one_forward(device_id, port, info):
    if _has_nat_forward(info, port, port):
        return True
    # rule names must be unique per VM and stable across restarts
    rule = f"osp-{device_id},tcp,127.0.0.1,{port},,{port}"
    if 'VMState="running"' in info:
        result = vbox("controlvm", VM, "natpf1", rule, quiet=True)
    else:
        result = vbox("modifyvm", VM, "--natpf1", rule, quiet=True)
    if result.returncode == 0:
        _emit("ok", f"device control port {port} is ready.")
        return True
    # A concurrent startup path may have installed it between our check and
    # command. Re-read before reporting a failure.
    refreshed = vbox(
        "showvminfo", VM, "--machinereadable", quiet=True).stdout or ""
    if _has_nat_forward(refreshed, port, port):
        return True
    detail = (result.stderr or result.stdout or "").strip()
    _emit("err", f"couldn't expose device control port {port}: "
                 + detail[-180:])
    return False


def _migrate_radio_assignments(config):
    """One-time: move the old fixed hid_radio/mac_radio slots onto the devices
    that inherited those lanes. After this the DEVICE owns its radio and the
    legacy slots are only read, never required."""
    devices = config.get("devices", [])
    if not devices or any(d.get("radio") for d in devices):
        return config
    prefs = load_bt_prefs()
    legacy = {"ipad": str(prefs.get("hid_radio", "") or "").upper(),
              "mac": str(prefs.get("mac_radio", "") or "").upper()}
    changed = False
    for device in devices:
        radio = legacy.get(device.get("id"), "")
        if radio:
            device["radio"] = radio
            changed = True
    if changed:
        _emit("event", "migrated radio assignments onto the devices "
                       "(each device now owns its own radio).")
    return config


def load_bt_prefs():
    """Local, persistent Bluetooth prefs: custom names (survive re-pairing) and
    a blacklist of devices that never show in scans. Multi-radio fields are
    deliberately opt-in; an old/missing prefs file stays on the original
    single-radio path."""
    defaults = {
        "renames": {},
        "blacklist": set(),
        "radio_mode": "single",
        "radio_assignments": {},
        "hid_radio": "",
        "mac_radio": "",
        "scan_radio": "",
        "radio_labels": {},
    }
    try:
        with open(BT_PREFS) as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return defaults
        mode = d.get("radio_mode", "single")
        return {
            "renames": dict(d.get("renames", {})),
            "blacklist": set(d.get("blacklist", [])),
            "radio_mode": "multi" if mode == "multi" else "single",
            "radio_assignments": {
                str(device).upper(): str(controller).upper()
                for device, controller
                in dict(d.get("radio_assignments", {})).items()
            },
            "hid_radio": str(d.get("hid_radio", "")).upper(),
            "mac_radio": str(d.get("mac_radio", "")).upper(),
            "scan_radio": str(d.get("scan_radio", "")).upper(),
            "radio_labels": {
                str(controller).upper(): str(label)
                for controller, label
                in dict(d.get("radio_labels", {})).items()
            },
        }
    except (OSError, ValueError):
        return defaults


def save_bt_prefs(prefs):
    try:
        with open(BT_PREFS, "w") as f:
            json.dump({"renames": prefs["renames"],
                       "blacklist": sorted(prefs["blacklist"]),
                       "radio_mode": prefs.get("radio_mode", "single"),
                       "radio_assignments":
                           prefs.get("radio_assignments", {}),
                       "hid_radio": prefs.get("hid_radio", ""),
                       "mac_radio": prefs.get("mac_radio", ""),
                       "scan_radio": prefs.get("scan_radio", ""),
                       "radio_labels": prefs.get("radio_labels", {})},
                      f, indent=2)
    except OSError:
        pass


def multi_radio_enabled(prefs=None):
    prefs = prefs if prefs is not None else load_bt_prefs()
    return prefs.get("radio_mode") == "multi"


def _active_usb_filter_ids(info):
    """Return VID/PID tokens for active VirtualBox USB filters.

    The machine-readable output is stable across VirtualBox releases and lets
    us match the VM's intended radios without hard-coding TP-Link hardware.
    """
    filters = {}
    for line in (info or "").splitlines():
        match = re.match(
            r'USBFilter(Active|VendorId|ProductId)(\d+)="([^"]*)"$',
            line.strip(), re.IGNORECASE)
        if not match:
            continue
        field, index, value = match.groups()
        filters.setdefault(index, {})[field.lower()] = value
    result = set()
    for row in filters.values():
        vendor = row.get("vendorid", "").strip().upper()
        product = row.get("productid", "").strip().upper()
        if row.get("active", "").lower() == "on" and vendor and product:
            result.add(f"VID_{vendor}&PID_{product}")
    return result


def _shared_filtered_radio_hubs(info, pnp_lines):
    """Find external hubs holding 2+ radios filtered into this VM.

    Re-enumerating a root hub would be far too broad.  A shared external hub is
    narrow enough for recovery and is the one Windows/VirtualBox edge case we
    need to handle: identical radios can remain stuck in CapturingForVM until
    their parent hub hot-plugs them after the VM is listening.
    """
    filter_ids = _active_usb_filter_ids(info)
    by_parent = {}
    for line in (pnp_lines or "").splitlines():
        if "|" not in line:
            continue
        instance, parent = (part.strip() for part in line.split("|", 1))
        upper_instance = instance.upper()
        upper_parent = parent.upper()
        if not any(token in upper_instance for token in filter_ids):
            continue
        if not upper_parent.startswith("USB\\VID_") \
                or "ROOT_HUB" in upper_parent:
            continue
        by_parent.setdefault(parent, set()).add(upper_instance)
    return sorted(
        parent for parent, children in by_parent.items()
        if len(children) >= 2)


def _multi_radio_rearm_hubs(info):
    """Read the parent hubs before VirtualBox captures the filtered radios."""
    if os.name != "nt" or not multi_radio_enabled():
        return []
    powershell = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    command = (
        "Get-PnpDevice -PresentOnly -Class Bluetooth "
        "-ErrorAction SilentlyContinue | "
        "Where-Object {$_.InstanceId -like 'USB\\VID_*&PID_*'} | "
        "ForEach-Object {"
        "$p=(Get-PnpDeviceProperty -InstanceId $_.InstanceId "
        "-KeyName DEVPKEY_Device_Parent "
        "-ErrorAction SilentlyContinue).Data;"
        "if($p){Write-Output ($_.InstanceId+'|'+$p)}}")
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=15,
            creationflags=NO_WINDOW)
        if result.returncode:
            return []
        return _shared_filtered_radio_hubs(info, result.stdout)
    except Exception:  # noqa: BLE001
        return []


def _rearm_multi_radio_hubs(parents):
    """Hot-plug narrow shared radio hubs after the VM's filters are active."""
    if not parents:
        return
    pnputil = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32", "pnputil.exe")
    # VirtualBox needs a moment to arm the per-VM capture filters.
    threading.Event().wait(4)
    for parent in parents:
        _emit("event", "re-arming the shared USB radio hub so every assigned "
                       "adapter reaches the bridge VM...")
        try:
            result = subprocess.run(
                [pnputil, "/restart-device", parent],
                capture_output=True, text=True, timeout=20,
                creationflags=NO_WINDOW)
            if result.returncode:
                detail = (result.stderr or result.stdout or "").strip()
                _emit("err", "USB radio hub re-arm failed: " + detail[-180:])
            else:
                _emit("ok", "shared USB radio hub re-armed.")
        except Exception as exc:  # noqa: BLE001
            _emit("err", f"USB radio hub re-arm failed: {exc}")
    threading.Event().wait(5)


def start_vm_clean():
    """Start the VM headless with a guaranteed COLD boot. VirtualBox saves the
    VM state on host shutdown; resuming a saved state skips the kernel cmdline
    (USB autosuspend off) and can leave the passed-through Bluetooth radio
    wedged. Discarding any saved state first forces a clean boot + a clean
    radio re-enumeration on xHCI."""
    _emit("event", "starting the bridge VM (clean cold boot)…")
    info = vbox("showvminfo", VM, "--machinereadable", quiet=True).stdout or ""
    rearm_hubs = _multi_radio_rearm_hubs(info)
    if 'VMState="saved"' in info:
        vbox("discardstate", VM)
        info = None
    ensure_device_forwards(live_config(), info)
    result = vbox("startvm", VM, "--type", "headless")
    if result.returncode == 0:
        _rearm_multi_radio_hubs(rearm_hubs)


def target_daemon_status(target=None):
    try:
        endpoint = _device_endpoint(target)
        s = socket.create_connection(endpoint, 2)
        s.sendall(b'{"cmd":"status"}\n')
        s.settimeout(2)
        # read to the newline the daemon terminates replies with -- a single
        # recv can legally return a partial JSON line
        data = b""
        while b"\n" not in data and len(data) < 4096:
            chunk = s.recv(512)
            if not chunk:
                break
            data += chunk
        s.close()
        return json.loads(data.split(b"\n", 1)[0].decode())
    except Exception:  # noqa: BLE001
        return None


def daemon_status():
    """Status of the FIRST configured device's daemon -- used only for the
    global 'is the bridge up' banner. No device id is assumed; per-device state
    comes from _poll_device_status()."""
    first = first_device_id()
    return target_daemon_status(first) if first else None


def target_daemon_cmd(target, obj, timeout=2):
    """Send one command to the daemon and return its reply (or None)."""
    try:
        endpoint = _device_endpoint(target)
        s = socket.create_connection(endpoint, 2)
        s.sendall((json.dumps(obj) + "\n").encode())
        s.settimeout(timeout)
        data = b""
        while b"\n" not in data and len(data) < 4096:
            chunk = s.recv(512)
            if not chunk:
                break
            data += chunk
        s.close()
        return json.loads(data.split(b"\n", 1)[0].decode())
    except Exception:  # noqa: BLE001
        return None


def daemon_cmd(obj, timeout=2):
    """Backward-compatible iPad daemon command."""
    return target_daemon_cmd("ipad", obj, timeout=timeout)


def set_target_advertising(target, on):
    """Broadcasting is OPT-IN. The daemon no longer advertises at boot, so this
    is the ONLY thing that makes the machine visible as a Bluetooth keyboard --
    and it is called only from Pair/Broadcast. It is switched back off the
    moment the iPad is in, so the PC is never left beaconing and a bonded iPad
    cannot silently reconnect on its own."""
    # Advertisement operations complete asynchronously inside BlueZ. The
    # daemon now waits for BlueZ's completion callback, so allow longer than
    # the normal status-command timeout and require the confirmed state to
    # match the request.
    r = target_daemon_cmd(
        target, {"cmd": "adv", "on": bool(on)}, timeout=8)
    return bool(r and r.get("ok")
                and bool(r.get("advertising")) == bool(on))


def set_advertising(on):
    """Backward-compatible iPad advertising command."""
    return set_target_advertising("ipad", on)


_ELEVATED = None


def is_elevated():
    """True if OpenSpan is running with administrator rights.

    THIS MATTERS FAR MORE THAN IT LOOKS. Windows UIPI: a NON-elevated process's
    low-level input hooks receive NOTHING while an ELEVATED window has focus.
    So if you run anything as admin (an admin terminal, say), the portal goes
    silently deaf the instant that window is focused -- the mouse just stops
    crossing the border. No error, no exception, nothing in any log, and the
    hooks still report as successfully installed. It cost days to find.

    Rule: OpenSpan must run at least as elevated as the apps you use."""
    global _ELEVATED
    if _ELEVATED is None:
        try:
            import ctypes
            _ELEVATED = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # noqa: BLE001
            _ELEVATED = False
    return _ELEVATED


# ---- clipboard relay (two-way clipboard with the iPad) ---------------------
# See CLIPBOARD_DESIGN.md. The iPad's Shortcuts app calls these endpoints
# (triggered by FKA key combos the portal sends through the HID keyboard):
#   GET  /clip  -> the Windows clipboard text        ("Paste from PC")
#   POST /clip  -> sets the Windows clipboard        ("Copy to PC")
# Token-gated: the clipboard carries passwords, and any LAN device can
# reach the port. Text only (CF_UNICODETEXT), UTF-8 on the wire.
def load_setting(key, default=None):
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f).get(key, default)
    except (OSError, ValueError):
        return default


def save_setting(key, value):
    """Persist one key into openspan_settings.json atomically, without
    destroying the rest of the user's settings."""
    cfg = {}
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        pass
    cfg[key] = value
    try:
        tmp = SETTINGS + ".new"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, SETTINGS)
    except OSError:
        pass
CLIP_MAX = 10 * 1024 * 1024  # 10 MB cap on inbound clipboard payloads
BAL_FILE = os.path.join(ROOT, "audio_balance.txt")  # -1 (L) .. +1 (R); the
#   audio sender polls this every 150ms and applies it as channel gains
GAIN_FILE = os.path.join(ROOT, "audio_gain.txt")  # 0..1 OpenSpan gain; read
#   by the audio role so the UI never needs Core Audio COM inside the main app


# Core Audio COM belongs only to the isolated --audio role. Re-acquiring
# comtypes endpoint objects in the long-running main process caused repeatable
# native _ctypes.pyd access violations with no Python traceback.


def clipboard_config():
    """Token + port for the relay, persisted in openspan_settings.json
    (token is generated once; the iPad shortcuts carry it in a header).
    NEVER destroys the user's settings: an unparseable file is backed up to
    .bad instead of being silently rewritten, and the write is atomic
    (tmp + os.replace) so a crash can't manufacture a corrupt file."""
    cfg = {}
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        try:
            os.replace(SETTINGS, SETTINGS + ".bad")
            _emit("err", "openspan_settings.json was unreadable — moved to "
                         ".bad and rebuilt (check your custom settings).")
        except OSError:
            pass
    changed = False
    if not cfg.get("clipboard_token"):
        import uuid
        cfg["clipboard_token"] = uuid.uuid4().hex
        changed = True
    if not cfg.get("clipboard_port"):
        cfg["clipboard_port"] = 9966
        changed = True
    if changed:
        try:
            tmp = SETTINGS + ".new"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            os.replace(tmp, SETTINGS)
        except OSError:
            pass
    return cfg["clipboard_token"], int(cfg["clipboard_port"])


def lan_ip():
    """This PC's LAN address (no packets are sent by a UDP connect)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))  # TEST-NET: never actually routed to
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def get_clipboard_text():
    """Read Unicode text from the Windows clipboard (stdlib ctypes)."""
    import ctypes
    CF_UNICODETEXT = 13
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    # HANDLE-returning calls MUST be prototyped: ctypes defaults restype to
    # 32-bit int, silently truncating 64-bit clipboard handles -> GlobalLock
    # on the mangled handle returns NULL and reads come back empty
    u.OpenClipboard.argtypes = [ctypes.c_void_p]
    u.GetClipboardData.restype = ctypes.c_void_p
    u.GetClipboardData.argtypes = [ctypes.c_uint]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [ctypes.c_void_p]
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    for _ in range(5):  # clipboard is a contended global -- retry briefly
        if u.OpenClipboard(None):
            break
        time.sleep(0.02)
    else:
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


def set_clipboard_text(text):
    """Put Unicode text on the Windows clipboard (stdlib ctypes)."""
    import ctypes
    CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    u.OpenClipboard.argtypes = [ctypes.c_void_p]
    k.GlobalAlloc.restype = ctypes.c_void_p
    k.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [ctypes.c_void_p]
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    k.GlobalFree.argtypes = [ctypes.c_void_p]
    u.SetClipboardData.restype = ctypes.c_void_p
    u.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    buf = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(buf)
    for _ in range(5):
        if u.OpenClipboard(None):
            break
        time.sleep(0.02)
    else:
        return False
    try:
        u.EmptyClipboard()
        h = k.GlobalAlloc(GMEM_MOVEABLE, size)
        if not h:
            return False
        p = k.GlobalLock(h)
        if not p:
            k.GlobalFree(h)
            return False
        ctypes.memmove(p, buf, size)
        k.GlobalUnlock(h)
        if not u.SetClipboardData(CF_UNICODETEXT, h):
            k.GlobalFree(h)  # ownership passes only on SUCCESS
            return False
        return True
    finally:
        u.CloseClipboard()


class ClipboardServer:
    """LAN HTTP relay for the iPad clipboard shortcuts. Daemon-threaded;
    dies with the app. bind_host is 0.0.0.0 in production (the iPad must
    reach it) and 127.0.0.1 in test harnesses."""

    def __init__(self, token, port, bind_host="0.0.0.0"):
        self.token = token
        self.port = port
        self.bind_host = bind_host
        self.httpd = None

    def start(self):
        import http.server
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            # a stalled/slowloris connection must never pin a thread forever
            # (handle_one_request treats a socket timeout as close)
            timeout = 30

            def log_message(self, *a):  # no stderr chatter
                pass

            def _plain(self, code, body=b""):
                if code != 200:
                    # error paths may leave request bytes unread; never let
                    # keep-alive parse leftovers as a pipelined request
                    self.close_connection = True
                self.send_response(code)
                if body:
                    self.send_header("Content-Type",
                                     "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _authed(self):
                # constant-time compare over BYTES: the str form raises
                # TypeError on non-ASCII header bytes (latin-1 decoded)
                import hmac
                tok = self.headers.get("X-OpenSpan-Token", "")
                if hmac.compare_digest(tok.encode("utf-8", "replace"),
                                       outer.token.encode("utf-8")):
                    return True
                # visible on the PC, and an explanatory body on the iPad
                # (Shortcuts copies the response body even on a 403 — an
                # empty one would silently blank the iPad clipboard)
                _emit("err", "clipboard request REJECTED (bad token) from "
                             f"{self.client_address[0]}")
                self._plain(403, b"OpenSpan relay: bad or missing token "
                                 b"(check the shortcut's header)")
                return False

            def do_GET(self):
                try:
                    self._get()
                except Exception as e:  # noqa: BLE001 -- a handler crash
                    #  under pythonw is an invisible dead connection
                    try:
                        self._plain(500)
                    except Exception:  # noqa: BLE001
                        pass
                    _emit("err", f"clipboard GET failed: {e}")

            def do_POST(self):
                try:
                    self._post()
                except Exception as e:  # noqa: BLE001
                    try:
                        self._plain(500)
                    except Exception:  # noqa: BLE001
                        pass
                    _emit("err", f"clipboard POST failed: {e}")

            def _get(self):
                if self.path != "/clip":
                    self._plain(404)
                    return
                if not self._authed():
                    return
                # errors="replace": a lone UTF-16 surrogate on the clipboard
                # must not abort the request
                data = get_clipboard_text().encode("utf-8", "replace")
                self._plain(200, data)
                _emit("event",
                      f"clipboard served to the iPad ({len(data)} bytes)")

            def _post(self):
                if self.path != "/clip":
                    self._plain(404)
                    return
                if not self._authed():
                    return
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = 0
                if n <= 0 or n > CLIP_MAX:
                    self._plain(413 if n > CLIP_MAX else 400)
                    return
                body = self.rfile.read(n).decode("utf-8", "replace")
                # Shortcuts' JSON body arrives as {"text": ...}; a raw
                # text body works too
                if "json" in (self.headers.get("Content-Type") or "").lower():
                    try:
                        body = json.loads(body).get("text")
                    except (ValueError, AttributeError):
                        pass  # not valid JSON -> treat as raw text
                    if not isinstance(body, str):
                        # missing/null/non-string "text": reject rather
                        # than silently blanking the Windows clipboard
                        self._plain(400, b"OpenSpan relay: JSON body needs "
                                         b'a string "text" field')
                        return
                if set_clipboard_text(body):
                    self._plain(200, b"ok")
                    _emit("event", "clipboard received from the iPad "
                                   f"({len(body)} chars)")
                else:
                    self._plain(500)
                    _emit("err", "clipboard write failed (clipboard busy?)")

        try:
            self.httpd = http.server.ThreadingHTTPServer(
                (self.bind_host, self.port), Handler)
        except OSError as e:
            _emit("err", f"clipboard relay couldn't bind :{self.port} ({e})")
            return False
        threading.Thread(target=self.httpd.serve_forever,
                         daemon=True).start()
        return True

    def stop(self):
        """Close the listener so the clipboard is not remotely reachable
        during app teardown."""
        try:
            if self.httpd:
                self.httpd.shutdown()
                self.httpd.server_close()
                self.httpd = None
        except Exception:  # noqa: BLE001
            pass


# ---- radio-ownership mode (Windows vs Station), switched via reboot ----
MODE_FILE = os.path.join(ROOT, "mode.txt")
BOOT_TASK = "OpenSpanBoot"
INSTALL_TASK = os.path.join(ROOT, "install-boot-task.ps1")


def current_mode():
    try:
        with open(MODE_FILE) as f:
            m = f.read().strip().lower()
        return "station" if m == "station" else "windows"
    except OSError:
        return "windows"


def set_mode(mode):
    with open(MODE_FILE, "w") as f:
        f.write(mode + "\n")


def boot_task_exists():
    r = subprocess.run(["schtasks", "/Query", "/TN", BOOT_TASK],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    return r.returncode == 0


def ensure_boot_task():
    """Install the SYSTEM startup task (one-time, elevates via UAC)."""
    if boot_task_exists():
        return True
    # elevate PowerShell to run the installer, wait for it
    ps = ("Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
          f"-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',"
          f"'{INSTALL_TASK}'")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   creationflags=NO_WINDOW)
    return boot_task_exists()


# The tray window class and its WNDPROC thunk are registered ONCE per process
# and are IMMORTAL: unregistering (or letting the thunk be garbage-collected
# after a failed init) leaves lpfnWndProc pointing at freed memory, and the
# next tray attempt crashes natively (0xC0000005) inside CreateWindowExW.
_TRAY = {"registered": False, "proc": None, "cls": None, "nid_cls": None,
         "active": None, "taskbar_created": 0}


class TrayIcon:
    """Minimal Windows system-tray icon — pure ctypes, no dependencies.
    A hidden (real, NOT message-only) window receives the callbacks; it
    lives on the Tk thread, so Tk's mainloop pumps its messages. Left-click
    (or double-click) the icon -> on_restore(). A real window is required
    because explorer.exe restarts wipe all tray icons and announce it with
    the broadcast "TaskbarCreated" — which message-only windows never
    receive; on that message the icon is re-added automatically."""
    _WM_TRAY = 0x8001  # WM_APP + 1

    def __init__(self, tip, icon_path, on_restore, on_menu=None):
        import ctypes
        import ctypes.wintypes as wt
        self._ct = ctypes
        self.on_restore = on_restore
        self.on_menu = on_menu   # right-click -> app posts a themed Tk menu
        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        sh = ctypes.windll.shell32
        self._u32, self._sh = u32, sh
        self._register_once(ctypes, wt, u32, k32)

        hinst = k32.GetModuleHandleW(None)
        self.hwnd = u32.CreateWindowExW(
            0, "OpenSpanTrayWnd", "OpenSpanTray", 0, 0, 0, 0, 0,
            None, None, hinst, None)  # top-level, never shown
        if not self.hwnd:
            raise OSError("tray: CreateWindowExW failed")

        hicon = u32.LoadImageW(None, icon_path, 1, 16, 16,
                               0x10)  # IMAGE_ICON, LR_LOADFROMFILE
        if not hicon:
            hicon = u32.LoadIconW(None, 32512)  # IDI_APPLICATION fallback

        NID = _TRAY["nid_cls"]
        self._nid = NID()
        self._nid.cbSize = ctypes.sizeof(NID)
        self._nid.hWnd = self.hwnd
        self._nid.uID = 1
        self._nid.uFlags = 0x07  # NIF_MESSAGE | NIF_ICON | NIF_TIP
        self._nid.uCallbackMessage = self._WM_TRAY
        self._nid.hIcon = hicon
        self._nid.szTip = tip[:127]
        _TRAY["active"] = self  # before NIM_ADD: callbacks may fire at once
        if not sh.Shell_NotifyIconW(0, ctypes.byref(self._nid)):  # NIM_ADD
            _TRAY["active"] = None
            u32.DestroyWindow(self.hwnd)  # class/thunk stay: immortal
            raise OSError("tray: Shell_NotifyIconW failed")

    @staticmethod
    def _register_once(ctypes, wt, u32, k32):
        if _TRAY["registered"]:
            return
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, ctypes.c_uint,
                                     wt.WPARAM, wt.LPARAM)
        # 64-bit-correct prototypes (defaults truncate handles to 32-bit)
        u32.DefWindowProcW.restype = ctypes.c_ssize_t
        u32.DefWindowProcW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM,
                                       wt.LPARAM]
        u32.CreateWindowExW.restype = wt.HWND
        u32.CreateWindowExW.argtypes = [
            wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
        u32.DestroyWindow.argtypes = [wt.HWND]
        u32.LoadImageW.restype = ctypes.c_void_p
        u32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, ctypes.c_uint,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        k32.GetModuleHandleW.restype = ctypes.c_void_p
        k32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
        u32.RegisterWindowMessageW.restype = ctypes.c_uint
        u32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
        _TRAY["taskbar_created"] = u32.RegisterWindowMessageW("TaskbarCreated")

        def proc(hwnd, msg, w, l):
            # This is a Win32 callback: an exception must NEVER escape it. In a
            # windowed frozen build ctypes would try to print the traceback to
            # a None stderr and hard-crash the process (0xc000041d in
            # _ctypes.pyd). Swallow everything; fall back to DefWindowProc.
            try:
                t = _TRAY["active"]
                if t is not None:
                    if msg == TrayIcon._WM_TRAY and l in (0x0202, 0x0203):
                        # WM_LBUTTONUP / WM_LBUTTONDBLCLK on the icon
                        try:
                            t.on_restore()
                        except Exception:  # noqa: BLE001
                            pass
                        return 0
                    if msg == TrayIcon._WM_TRAY and l == 0x0205 \
                            and t.on_menu:            # WM_RBUTTONUP on the icon
                        try:
                            t.on_menu()
                        except Exception:  # noqa: BLE001
                            pass
                        return 0
                    if msg == _TRAY["taskbar_created"]:
                        # explorer restarted and forgot every tray icon: re-add
                        try:
                            t._sh.Shell_NotifyIconW(0, ctypes.byref(t._nid))
                        except Exception:  # noqa: BLE001
                            pass
                        return 0
            except BaseException:  # noqa: BLE001 -- a callback must not raise
                pass
            try:
                return u32.DefWindowProcW(hwnd, msg, w, l)
            except BaseException:  # noqa: BLE001
                return 0
        _TRAY["proc"] = WNDPROC(proc)  # immortal: keeps the thunk alive

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", WNDPROC),
                        ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                        ("hCursor", ctypes.c_void_p),
                        ("hbrBackground", ctypes.c_void_p),
                        ("lpszMenuName", wt.LPCWSTR),
                        ("lpszClassName", wt.LPCWSTR)]
        _TRAY["cls"] = WNDCLASSW(0, _TRAY["proc"], 0, 0,
                                 k32.GetModuleHandleW(None), None, None,
                                 None, None, "OpenSpanTrayWnd")
        if not u32.RegisterClassW(ctypes.byref(_TRAY["cls"])):
            _TRAY["proc"] = None
            _TRAY["cls"] = None
            raise OSError("tray: RegisterClassW failed")

        class NOTIFYICONDATAW(ctypes.Structure):  # V2 layout
            _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND),
                        ("uID", ctypes.c_uint), ("uFlags", ctypes.c_uint),
                        ("uCallbackMessage", ctypes.c_uint),
                        ("hIcon", wt.HICON), ("szTip", ctypes.c_wchar * 128),
                        ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
                        ("szInfo", ctypes.c_wchar * 256),
                        ("uVersion", ctypes.c_uint),
                        ("szInfoTitle", ctypes.c_wchar * 64),
                        ("dwInfoFlags", wt.DWORD)]
        ctypes.windll.shell32.Shell_NotifyIconW.restype = wt.BOOL
        ctypes.windll.shell32.Shell_NotifyIconW.argtypes = [
            wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        _TRAY["nid_cls"] = NOTIFYICONDATAW
        _TRAY["registered"] = True

    def ensure(self):
        """True if the icon is (still) in the tray; re-adds it if the shell
        lost it. Polled while the window is hidden so the app can never be
        stranded icon-less."""
        try:
            if self._sh.Shell_NotifyIconW(1, self._ct.byref(self._nid)):
                return True  # NIM_MODIFY succeeded -> icon exists
            return bool(
                self._sh.Shell_NotifyIconW(0, self._ct.byref(self._nid)))
        except Exception:  # noqa: BLE001
            return False

    def destroy(self):
        if _TRAY["active"] is self:
            _TRAY["active"] = None
        try:
            self._sh.Shell_NotifyIconW(2, self._ct.byref(self._nid))
        except Exception:  # noqa: BLE001
            pass
        try:
            self._u32.DestroyWindow(self.hwnd)
            # the window class + WNDPROC thunk are deliberately NOT
            # unregistered -- see the _TRAY comment above
        except Exception:  # noqa: BLE001
            pass


class ArrangeCanvas(tk.Canvas):
    """Always-visible screen arrangement; drag the iPad, it snaps + saves."""

    def __init__(self, master, on_change=None, **kw):
        super().__init__(master, bg=PANEL, highlightthickness=0, **kw)
        self.on_change = on_change
        self.monitors = enum_monitors()
        self.ipad = self._default_ipad()
        self._load()
        self.dragging = False
        self.drag_off = (0, 0)
        self.ipad_state = "off"  # off=grey / idle=amber / live=green (by poll)
        self._world_bounds()
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)

    def _default_ipad(self):
        p = next((m for m in self.monitors if m["primary"]), self.monitors[0])
        return {"x": p["x"] + p["w"], "y": p["y"], "w": 1080, "h": 810,
                "res_w": 1080, "res_h": 810}

    def _load(self):
        try:
            with open(CONFIG) as f:
                cfg = json.load(f)
            if "ipad" in cfg:
                self.ipad.update(cfg["ipad"])
        except (OSError, ValueError):
            pass

    def set_ipad_size(self, w, h):
        self.ipad.update(w=w, h=h, res_w=w, res_h=h)
        self.redraw()

    def set_ipad_state(self, live, paired):
        """iPad link state from _apply_poll: GREEN when live (connected AND
        routing input), AMBER when merely paired (bonded, not driving), GREY
        when not paired. Redraw only on change."""
        state = "live" if live else ("idle" if paired else "off")
        if state != self.ipad_state:
            self.ipad_state = state
            self.redraw()

    def rotate(self):
        i = self.ipad
        i["w"], i["h"] = i["h"], i["w"]
        i["res_w"], i["res_h"] = i["res_h"], i["res_w"]
        self.redraw()
        self.save()

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
        cw, ch = max(self.winfo_width(), 100), max(self.winfo_height(), 100)
        s = min(cw / (self.wx1 - self.wx0),
                ch / (self.wy1 - self.wy0)) * 0.90
        ox = (cw - (self.wx1 - self.wx0) * s) / 2
        oy = (ch - (self.wy1 - self.wy0) * s) / 2
        return s, ox, oy

    def w2c(self, x, y):
        s, ox, oy = self._scale()
        return (x - self.wx0) * s + ox, (y - self.wy0) * s + oy

    def c2w(self, cx, cy):
        s, ox, oy = self._scale()
        return (cx - ox) / s + self.wx0, (cy - oy) / s + self.wy0

    def redraw(self):
        self.delete("all")
        for m in self.monitors:
            x0, y0 = self.w2c(m["x"], m["y"])
            x1, y1 = self.w2c(m["x"] + m["w"], m["y"] + m["h"])
            self.create_rectangle(x0, y0, x1, y1, fill=MON_FILL,
                                  outline=MON_LINE, width=2)
            tag = "PRIMARY\n" if m["primary"] else ""
            self.create_text((x0 + x1) / 2, (y0 + y1) / 2,
                             text=f"{tag}{m['w']}x{m['h']}", fill="#c9d4ec",
                             justify="center", font=("Segoe UI", 9, "bold"))
        ix0, iy0 = self.w2c(self.ipad["x"], self.ipad["y"])
        ix1, iy1 = self.w2c(self.ipad["x"] + self.ipad["w"],
                            self.ipad["y"] + self.ipad["h"])
        # Colour reflects the real link: GREEN when live (connected + routing),
        # AMBER when paired but not driving, GREY when not paired. Kept in sync
        # by _apply_poll -> set_ipad_state.
        if self.ipad_state == "live":
            _fill, _line, _txt = IPAD_FILL, IPAD_LINE, "#d6ffe9"
        elif self.ipad_state == "idle":
            _fill, _line, _txt = IPAD_IDLE_FILL, IPAD_IDLE_LINE, "#ffe9b0"
        else:
            _fill, _line, _txt = IPAD_OFF_FILL, IPAD_OFF_LINE, MUTED
        self.create_rectangle(ix0, iy0, ix1, iy1, fill=_fill,
                              outline=_line, width=3)
        self.create_text((ix0 + ix1) / 2, (iy0 + iy1) / 2,
                         text=f"iPad\n{self.ipad['w']}x{self.ipad['h']}",
                         fill=_txt, justify="center",
                         font=("Segoe UI", 9, "bold"))
        for edge, m, lo, hi in self._portals():
            if edge in ("ipad-left", "ipad-right"):
                wx = self.ipad["x"] if edge == "ipad-left" \
                    else self.ipad["x"] + self.ipad["w"]
                a, b = self.w2c(wx, lo), self.w2c(wx, hi)
            else:
                wy = self.ipad["y"] if edge == "ipad-top" \
                    else self.ipad["y"] + self.ipad["h"]
                a, b = self.w2c(lo, wy), self.w2c(hi, wy)
            self.create_line(a[0], a[1], b[0], b[1], fill=PORTAL, width=5)

    def _portals(self):
        ip, out = self.ipad, []
        for m in self.monitors:
            if abs(ip["x"] - (m["x"] + m["w"])) <= 2:
                lo, hi = max(ip["y"], m["y"]), min(ip["y"] + ip["h"],
                                                   m["y"] + m["h"])
                if hi - lo > 20:
                    out.append(("ipad-left", m, lo, hi))
            if abs((ip["x"] + ip["w"]) - m["x"]) <= 2:
                lo, hi = max(ip["y"], m["y"]), min(ip["y"] + ip["h"],
                                                   m["y"] + m["h"])
                if hi - lo > 20:
                    out.append(("ipad-right", m, lo, hi))
            if abs(ip["y"] - (m["y"] + m["h"])) <= 2:
                lo, hi = max(ip["x"], m["x"]), min(ip["x"] + ip["w"],
                                                   m["x"] + m["w"])
                if hi - lo > 20:
                    out.append(("ipad-top", m, lo, hi))
            if abs((ip["y"] + ip["h"]) - m["y"]) <= 2:
                lo, hi = max(ip["x"], m["x"]), min(ip["x"] + ip["w"],
                                                   m["x"] + m["w"])
                if hi - lo > 20:
                    out.append(("ipad-bottom", m, lo, hi))
        return out

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
        self.redraw()

    def _release(self, e):
        if not self.dragging:
            return
        self.dragging = False
        self._snap()
        self.redraw()
        self.save()

    def _snap(self):
        ip = self.ipad
        TH = max(m["w"] for m in self.monitors) * 0.25
        best = None
        for m in self.monitors:
            for cx, cy, axis in [
                    (m["x"] + m["w"], None, "x"), (m["x"] - ip["w"], None, "x"),
                    (None, m["y"] + m["h"], "y"), (None, m["y"] - ip["h"], "y")]:
                if axis == "x" and abs(ip["x"] - cx) < TH and \
                        (best is None or abs(ip["x"] - cx) < best[0]):
                    ny = max(m["y"] - ip["h"] + 40,
                             min(ip["y"], m["y"] + m["h"] - 40))
                    best = (abs(ip["x"] - cx), cx, ny)
                elif axis == "y" and abs(ip["y"] - cy) < TH and \
                        (best is None or abs(ip["y"] - cy) < best[0]):
                    nx = max(m["x"] - ip["w"] + 40,
                             min(ip["x"], m["x"] + m["w"] - 40))
                    best = (abs(ip["y"] - cy), nx, cy)
        if best:
            self.ipad["x"], self.ipad["y"] = int(best[1]), int(best[2])

    def save(self):
        cfg = {"monitors": self.monitors, "ipad": self.ipad,
               "portals": [{"edge": e, "monitor": m["name"], "lo": lo,
                            "hi": hi} for (e, m, lo, hi) in self._portals()]}
        with open(CONFIG, "w") as f:
            json.dump(cfg, f, indent=2)
        if self.on_change:
            self.on_change(bool(self._portals()))


class MultiArrangeCanvas(tk.Canvas):
    """Desk-layout canvas for Windows screens, iPad, and managed Mac displays.

    Layout width/height are physical/visual geometry. Pixel resolution,
    rotation, and refresh rate are separate fields, so corner-resizing a
    rectangle cannot alter the target's display mode.
    """

    # Single source of truth -- the schema sizes screens with this same
    # constant, so the drawing and the labels can never disagree again.
    UNITS_PER_INCH = DESK_UNITS_PER_INCH

    def __init__(self, master, on_change=None, **kw):
        super().__init__(master, bg=PANEL, highlightthickness=0, **kw)
        self.on_change = on_change
        live = enum_monitors()
        raw = {}
        try:
            with open(CONFIG, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            pass
        # What the portal will read when it starts. save() compares against
        # this, so the first cosmetic save of a session does not bounce a
        # perfectly good portal.
        self._told_portal = None
        self.target_states = {}
        self.adopt(raw, live)
        self._told_portal = portal_signature(self.config)
        self.action = None
        self.drag_off = (0, 0)
        self._resize_anchor = None
        self.bind("<Configure>", lambda _event: self.redraw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        self._hover = None
        self._hover_item = None
        self.bind("<Motion>", self._on_hover)
        self.bind("<Leave>", lambda _e: (setattr(self, "_hover", None),
                                         self.delete("hovercard")))
        self.redraw()
        # Persist the v1->v2 migration immediately. A Mac can be paired before
        # the user drags anything, and the portal process must already know its
        # three displays/independent daemon port when that connect edge lands.
        self._persist()

    def adopt(self, raw, live=None):
        """Make `raw` the arrangement this canvas is showing.

        Every derived handle the canvas keeps is rebuilt from the config here
        and NOWHERE else, so switching arrangements cannot leave one of them
        pointing into the previous desk. That is not hypothetical: `ipad` and
        `selected` hold references to specific display dicts, and a stale one
        survives every redraw looking perfectly valid.

        Connection state is keyed by device id and deliberately kept across the
        swap -- a device that is live right now is still live a moment later; a
        different picture of the desk does not disconnect anything.
        """
        raw = raw if isinstance(raw, dict) else {}
        self.config = normalize_config(raw, live or enum_monitors())
        # normalize_config builds its result from a whitelist -- version,
        # monitors, devices, plus the derived portals/links -- so ANYTHING else
        # the app keeps at the top level is dropped by it.
        #
        # That already had teeth before arrangements existed: the two
        # side-button crossing settings live up here, so every launch quietly
        # discarded them and the next save wrote the config back without them.
        # The checkboxes went on reading the same config and showing whatever
        # was left, which is why it never looked broken.
        #
        # Carried across by DIFFERENCE rather than by name. Listing the keys
        # would work exactly until the next setting is added at the top level
        # and nobody remembers this line exists.
        for key, value in raw.items():
            if key not in self.config:
                self.config[key] = value
        _migrate_radio_assignments(self.config)
        self.monitors = self.config["monitors"]
        self.targets = self.config["devices"]
        # No device is privileged. `ipad` here is simply "the first display of
        # the first device" -- a convenience handle for the legacy single-device
        # helpers below; it is None when the user has not added a device yet.
        self.ipad = None
        self.selected = None
        if self.targets and self.targets[0].get("displays"):
            self.ipad = self.targets[0]["displays"][0]
            self.selected = ("target", self.targets[0]["id"], self.ipad["id"])
        known = dict(self.target_states)
        self.target_states = {device["id"]: known.get(device["id"], "off")
                              for device in self.targets}
        self.ipad_state = "off"  # compatibility for existing tests/callers

    def target(self, target_id):
        return device_by_id(self.config, target_id)

    def devices(self):
        return self.config.setdefault("devices", [])

    def add_device(self, name=None):
        device = add_device(self.config, name, self.monitors)
        self.target_states.setdefault(device["id"], "off")
        self.redraw()
        self.save()
        return device

    def remove_device(self, device_id):
        removed = remove_device(self.config, device_id)
        self.target_states.pop(device_id, None)
        if self.selected and self.selected[1] == device_id:
            self.selected = None
        self.targets = self.config["devices"]
        if self.ipad is not None and not any(
                self.ipad is d for t in self.targets
                for d in t.get("displays", [])):
            self.ipad = (self.targets[0]["displays"][0]
                         if self.targets and self.targets[0].get("displays")
                         else None)
        self.redraw()
        self.save()
        return removed

    def set_ipad_size(self, width, height):
        if self.ipad is None:
            return
        self.ipad["res_w"] = int(width)
        self.ipad["res_h"] = int(height)
        self.ipad["rotation"] = 0
        set_layout_width(self.ipad, self.ipad["w"])
        self.redraw()
        self.save()

    def set_target_state(self, target_id, live, paired):
        state = "live" if live else ("idle" if paired else "off")
        if self.target_states.get(target_id) != state:
            self.target_states[target_id] = state
            if target_id == "ipad":
                self.ipad_state = state
            self.redraw()

    def set_ipad_state(self, live, paired):
        self.set_target_state("ipad", live, paired)

    def rotate(self):
        if self.ipad is None:
            return
        rotate_display(self.ipad)
        self.redraw()
        self.save()

    def mac_displays(self, device_id=None):
        target = self.target(device_id) if device_id else (
            self.targets[0] if self.targets else None)
        return target["displays"] if target else []

    def replace_mac_displays(self, rows, device_id=None):
        rows = validate_mac_displays(rows, device_id)
        target = self.target(device_id) if device_id else (
            self.targets[0] if self.targets else None)
        if target is None:
            return
        old = list(target.get("displays", []))
        primary = next(
            (m for m in self.monitors if m.get("primary")), self.monitors[0])
        base_x = int(primary.get("layout_x", primary["x"]))
        base_y = max(
            int(m.get("layout_y", m["y"]))
            + int(m.get("layout_h", m["h"]))
            for m in self.monitors)
        displays = []
        next_x = base_x
        for index, spec in enumerate(rows):
            prior = old[index] if index < len(old) else None
            display = dict(prior or {})
            display.update({
                "id": spec["id"],
                "name": spec["name"],
                "res_w": spec["res_w"],
                "res_h": spec["res_h"],
                "refresh_hz": spec["refresh_hz"],
                "rotation": spec["rotation"],
            })
            # The inches field is the DIAGONAL. Size is derived from it and
            # the aspect the resolution/rotation imply, exactly as
            # "Screen sizes..." does -- so the two can never disagree.
            diagonal = float(spec["physical_width"])
            display["diagonal_in"] = diagonal
            display["w"], display["h"] = physical_size(
                diagonal, display["res_w"], display["res_h"],
                display.get("rotation", 0))
            if prior:
                display["x"] = int(prior["x"])
                display["y"] = int(prior["y"])
            else:
                display["x"] = int(next_x)
                display["y"] = int(base_y)
            next_x = display["x"] + display["w"]
            displays.append(display)
        target["displays"] = displays
        if self.selected and self.selected[1] == target["id"]:
            self.selected = (
                ("target", target["id"], displays[0]["id"])
                if displays else None)
        self.redraw()
        self.save()

    @staticmethod
    def _monitor_rect(monitor):
        return (
            int(monitor.get("layout_x", monitor["x"])),
            int(monitor.get("layout_y", monitor["y"])),
            int(monitor.get("layout_w", monitor["w"])),
            int(monitor.get("layout_h", monitor["h"])),
        )

    @staticmethod
    def _display_rect(display):
        return (
            int(display["x"]), int(display["y"]),
            int(display["w"]), int(display["h"]))

    def _items(self):
        for monitor in self.monitors:
            yield ("local", "windows", monitor["name"]), monitor
        for target in self.targets:
            if not target.get("enabled", True):
                continue
            for display in target.get("displays", []):
                yield ("target", target["id"], display["id"]), display

    def _lookup(self, key):
        if not key:
            return None
        if key[0] == "local":
            return next(
                (m for m in self.monitors if m["name"] == key[2]), None)
        target = self.target(key[1])
        if not target:
            return None
        return next(
            (d for d in target["displays"] if d["id"] == key[2]), None)

    def _rect(self, key, item):
        return (self._monitor_rect(item) if key[0] == "local"
                else self._display_rect(item))

    def _set_position(self, key, item, x, y):
        if key[0] == "local":
            item["layout_x"], item["layout_y"] = int(x), int(y)
        else:
            item["x"], item["y"] = int(x), int(y)

    def _set_width(self, key, item, width):
        if key[0] == "local":
            width = max(120, int(width))
            ratio = item["h"] / max(1, item["w"])
            item["layout_w"] = width
            item["layout_h"] = max(120, int(round(width * ratio)))
        else:
            set_layout_width(item, width)

    def _world_bounds(self):
        rects = [self._rect(key, item) for key, item in self._items()]
        if not rects:
            self.wx0, self.wy0, self.wx1, self.wy1 = 0, 0, 100, 100
            return
        min_x = min(x for x, _y, _w, _h in rects)
        min_y = min(y for _x, y, _w, _h in rects)
        max_x = max(x + w for x, _y, w, _h in rects)
        max_y = max(y + h for _x, y, _w, h in rects)
        pad = max(180, int(max(max_x - min_x, max_y - min_y) * 0.12))
        self.wx0, self.wy0 = min_x - pad, min_y - pad
        self.wx1, self.wy1 = max_x + pad, max_y + pad

    def _scale(self):
        self._world_bounds()
        width = max(self.winfo_width(), 100)
        height = max(self.winfo_height(), 100)
        scale = min(
            width / max(1, self.wx1 - self.wx0),
            height / max(1, self.wy1 - self.wy0)) * 0.94
        ox = (width - (self.wx1 - self.wx0) * scale) / 2
        oy = (height - (self.wy1 - self.wy0) * scale) / 2
        return scale, ox, oy

    def w2c(self, x, y):
        scale, ox, oy = self._scale()
        return (
            (x - self.wx0) * scale + ox,
            (y - self.wy0) * scale + oy)

    def c2w(self, x, y):
        scale, ox, oy = self._scale()
        return (
            (x - ox) / scale + self.wx0,
            (y - oy) / scale + self.wy0)

    def _colors(self, key):
        if key[0] == "local":
            return MON_FILL, MON_LINE, "#c9d4ec"
        state = self.target_states.get(key[1], "off")
        if state == "live":
            return IPAD_FILL, IPAD_LINE, "#d6ffe9"
        if state == "idle":
            return IPAD_IDLE_FILL, IPAD_IDLE_LINE, "#ffe9b0"
        if key[1] == "mac":
            return "#2b2940", "#756bb1", "#c8c1ef"
        return IPAD_OFF_FILL, IPAD_OFF_LINE, MUTED

    def redraw(self):
        self.delete("all")
        for key, item in self._items():
            x, y, width, height = self._rect(key, item)
            x0, y0 = self.w2c(x, y)
            x1, y1 = self.w2c(x + width, y + height)
            fill, line, text_color = self._colors(key)
            chosen = key == self.selected
            self.create_rectangle(
                x0, y0, x1, y1, fill=fill,
                outline="#ffffff" if chosen else line,
                width=3 if chosen or key[0] == "target" else 2)
            if key[0] == "local":
                name = "This PC" + (" · PRIMARY" if item["primary"] else "")
                res_w, res_h = item["w"], item["h"]
                refresh = item.get("refresh_hz", 60)
                rotation = 0
            else:
                target = self.target(key[1])
                name = f"{target['name']} · {item['name']}"
                res_w, res_h = oriented_resolution(item)
                refresh = item.get("refresh_hz", 60)
                rotation = item.get("rotation", 0)
            # ONE SHORT LINE. Resolution, refresh, rotation and size used to
            # be stamped on every rectangle, which on a desk of portrait panels
            # meant four lines of text in a box narrower than the text. The
            # arrangement is a picture of where things ARE; the numbers belong
            # where they are asked for, which is the hover card.
            self.create_text(
                (x0 + x1) / 2, (y0 + y1) / 2, text=self._short_label(key, item),
                fill=text_color, justify="center", width=max(40, x1 - x0 - 10),
                font=("Segoe UI", 9, "bold"))
            if chosen:
                self.create_rectangle(
                    x1 - 10, y1 - 10, x1 + 2, y1 + 2,
                    fill=PORTAL, outline=PORTAL)
        self._draw_portals()
        self._draw_hover()

    def _short_label(self, key, item):
        """What a rectangle says when nobody is asking for detail."""
        if key[0] == "local":
            return "This PC" + ("\nPRIMARY" if item["primary"] else "")
        target = self.target(key[1])
        # A device with one screen is named by the DEVICE -- "iPad" says more
        # than "Display 1". A device with several is named by the screen, since
        # the device is obvious from the group they form.
        if len(target.get("displays", [])) <= 1:
            return target["name"]
        return item.get("name") or target["name"]

    def _detail_lines(self, key, item):
        """Everything about one surface, for the hover card."""
        if key[0] == "local":
            title = "This PC" + ("  ·  primary" if item["primary"] else "")
            res_w, res_h, rotation = item["w"], item["h"], 0
        else:
            target = self.target(key[1])
            screen = item.get("name") or ""
            title = (target["name"] if screen in ("", target["name"])
                     else f"{target['name']}  ·  {screen}")
            res_w, res_h = oriented_resolution(item)
            rotation = int(item.get("rotation", 0))
        refresh = item.get("refresh_hz", 60)
        hz = int(refresh) if float(refresh).is_integer() else refresh
        lines = [f"{res_w} × {res_h} @ {hz} Hz"]
        if rotation:
            lines.append(f"rotated {rotation}°")
        diagonal = item.get("diagonal_in")
        if diagonal:
            lines.append(f"{float(diagonal):g}\" diagonal")
        x, y, width, height = self._rect(key, item)
        lines.append(f"desk  x {x} → {x + width}    y {y} → {y + height}")
        return title, lines

    def _hit_key(self, event):
        world_x, world_y = self.c2w(event.x, event.y)
        for key, item in reversed(list(self._items())):
            x, y, width, height = self._rect(key, item)
            if x <= world_x <= x + width and y <= world_y <= y + height:
                return key, item
        return None, None

    def _on_hover(self, event):
        if self.action:                       # mid-drag: stay out of the way
            return
        key, item = self._hit_key(event)
        if key == self._hover:
            return
        self._hover = key
        self._hover_item = item
        self._draw_hover()

    def _draw_hover(self):
        self.delete("hovercard")
        if not self._hover or not self._hover_item:
            return
        title, lines = self._detail_lines(self._hover, self._hover_item)
        pad, lead = 9, 15
        left, bottom = 10, int(self.winfo_height()) - 10
        text_id = self.create_text(
            left + pad, bottom - pad, anchor="sw", justify="left",
            text=title + "\n" + "\n".join(lines),
            fill=FG, font=("Segoe UI", 8), tags="hovercard")
        bx0, by0, bx1, by1 = self.bbox(text_id)
        self.create_rectangle(
            bx0 - pad, by0 - pad, bx1 + pad, by1 + pad,
            fill=HOVER_FILL, outline=HOVER_LINE, width=1, tags="hovercard")
        self.tag_raise(text_id)

    def _draw_portals(self):
        for portal in compute_portals(self.config):
            display = next(
                (d for d in self.target(portal["target"])["displays"]
                 if d["id"] == portal["target_display"]), None)
            monitor = next(
                (m for m in self.monitors
                 if m["name"] == portal["monitor"]), None)
            if not display or not monitor:
                continue
            tx, ty, tw, th = self._display_rect(display)
            mx, my, mw, mh = self._monitor_rect(monitor)
            edge = portal["edge"]
            if edge in ("target-left", "target-right"):
                x = tx if edge == "target-left" else tx + tw
                lo, hi = max(ty, my), min(ty + th, my + mh)
                a, b = self.w2c(x, lo), self.w2c(x, hi)
            else:
                y = ty if edge == "target-top" else ty + th
                lo, hi = max(tx, mx), min(tx + tw, mx + mw)
                a, b = self.w2c(lo, y), self.w2c(hi, y)
            self.create_line(a[0], a[1], b[0], b[1],
                             fill=PORTAL, width=5)
        # PC↔target edges above are real Windows entry triggers. Also show each
        # target↔target edge from the shared adjacency graph; those are direct
        # handoff routes inside the running portal broker.
        drawn = set()
        for link in compute_adjacencies(self.config):
            source, destination = link["source"], link["destination"]
            if source["kind"] != "target" \
                    or destination["kind"] != "target":
                continue
            key = (
                link["axis"], link["line"], tuple(link["span"]),
                tuple(sorted((
                    f"{source['target']}:{source['display']}",
                    f"{destination['target']}:{destination['display']}",
                ))),
            )
            if key in drawn:
                continue
            drawn.add(key)
            lo, hi = link["span"]
            if link["axis"] == "x":
                a = self.w2c(link["line"], lo)
                b = self.w2c(link["line"], hi)
            else:
                a = self.w2c(lo, link["line"])
                b = self.w2c(hi, link["line"])
            self.create_line(a[0], a[1], b[0], b[1],
                             fill=PORTAL, width=5)

    def _press(self, event):
        world_x, world_y = self.c2w(event.x, event.y)
        scale = self._scale()[0]
        hit = None
        # Target displays are drawn over local screens, so hit-test in reverse.
        for key, item in reversed(list(self._items())):
            x, y, width, height = self._rect(key, item)
            if x <= world_x <= x + width and y <= world_y <= y + height:
                hit = (key, item, x, y, width, height)
                break
        if not hit:
            self.selected = None
            self.redraw()
            return
        key, item, x, y, width, height = hit
        self.selected = key
        # No corner resize. A screen's size is its real DIAGONAL in inches
        # (set in "Screen sizes..."), so dragging a corner could only make the
        # drawing disagree with the stated hardware. Dragging moves only.
        self.action = "move"
        self.drag_off = (world_x - x, world_y - y)
        # Remember the rect at press so _release can tell a real drag from a
        # bare select-click. A click that moves nothing must not snap, must not
        # save, and must not restart the portal.
        self._press_rect = (x, y, width, height)
        self.redraw()

    def _drag(self, event):
        if not self.action or not self.selected:
            return
        item = self._lookup(self.selected)
        if not item:
            return
        world_x, world_y = self.c2w(event.x, event.y)
        self._set_position(
            self.selected, item,
            world_x - self.drag_off[0], world_y - self.drag_off[1])
        self.redraw()

    @staticmethod
    def _align(pos, size, other_pos, other_size):
        return max(
            other_pos - size + 24,
            min(pos, other_pos + other_size - 24))

    def _snap_selected(self):
        item = self._lookup(self.selected)
        if not item:
            return
        x, y, width, height = self._rect(self.selected, item)
        neighbors = [
            self._rect(key, other)
            for key, other in self._items()
            if key != self.selected
        ]
        nx, ny = snap_rect_to_neighbors(
            (x, y, width, height), neighbors)
        self._set_position(self.selected, item, nx, ny)

    def _release(self, _event):
        if not self.action:
            return
        # A select-click (press+release with no movement) is not an edit: skip
        # the snap and the save entirely. Saving here used to tear down and
        # respawn the portal process -- dropping its hooks and sockets, and
        # blocking the Tk thread on taskkill -- on every click, and _snap_
        # selected could silently relocate a screen the user only meant to select.
        item = self._lookup(self.selected) if self.selected else None
        moved = True
        if item is not None and getattr(self, "_press_rect", None):
            x, y, width, height = self._rect(self.selected, item)
            moved = (x, y, width, height) != self._press_rect
        self._press_rect = None
        if not moved:
            self.action = None
            self._resize_anchor = None
            self.redraw()
            return
        if self.action == "move":
            self._snap_selected()
        self.action = None
        self._resize_anchor = None
        self.redraw()
        self.save()

    def save(self):
        self.config["portals"] = compute_portals(self.config)
        self.config["links"] = compute_adjacencies(self.config)
        self._persist()
        # THE single restart trigger: reload the portal whenever anything it
        # READS has changed -- a device, a screen, a resolution, a rotation, a
        # position, an input setting.
        #
        # Compared against WHAT THE PORTAL WAS LAST TOLD, not against the
        # config as it stood a moment ago. Every caller mutates the config and
        # then calls save(), so a "before" snapshot taken here is already the
        # after: adding a whole device compared equal to itself and the portal
        # was never reloaded. It went on routing for two devices while a third
        # sat there paired, healthy, and unreachable.
        signature = portal_signature(self.config)
        if signature != getattr(self, "_told_portal", None):
            self._told_portal = signature
            if self.on_change:
                self.on_change(bool(self.config["portals"]))

    def _persist(self):
        try:
            with open(CONFIG + ".new", "w", encoding="utf-8") as handle:
                json.dump(self.config, handle, indent=2)
            os.replace(CONFIG + ".new", CONFIG)
        except OSError:
            pass
        # An arrangement that is SELECTED is the one being used, so every edit
        # belongs to it. Without this there would be a saved copy and a live
        # copy drifting apart, and switching away -- the whole point of having
        # arrangements -- would silently throw away everything done since.
        # There is no unsaved state to lose because there is no unsaved state.
        name = str(self.config.get("profile") or "")
        if name and name in list_profiles():
            try:
                save_profile(self.config, name)
            except OSError:
                pass


class MacDisplayEditor:
    """Dark, modal editor for a DEVICE's displays (count/resolution/rotation/
    Hz/physical size). Works on any device -- `device_id` selects which."""

    @staticmethod
    def _device_label(canvas, device_id):
        for device in canvas.config.get("devices", []):
            if device.get("id") == device_id:
                return device.get("name") or device_id
        return "Device"

    def __init__(self, parent, canvas, device_id=None):
        self.parent = parent
        self.canvas = canvas
        self.device_id = device_id
        self.rows = []
        self.top = FrameModal(parent)
        self.top.title(f"{self._device_label(canvas, device_id)} displays")
        self.top.geometry("900x420")
        self.top.minsize(820, 340)
        self.top.configure(bg=BG)
        self.top.transient(parent)

        tk.Label(
            self.top,
            text=f"{self._device_label(canvas, device_id)} "
                 f"display configuration",
            bg=BG, fg=FG, font=("Segoe UI Semibold", 15)).pack(
                anchor="w", padx=18, pady=(16, 2))
        tk.Label(
            self.top,
            text="Resolution, rotation, and refresh rate are independent from "
                 "the physical width used on the drag canvas.",
            bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(
                anchor="w", padx=18, pady=(0, 12))
        # The button bar is packed BEFORE the table and anchored to the
        # bottom. pack hands out space in the order it is asked for, so a device
        # with seven screens now squeezes the ROWS rather than pushing Save and
        # Cancel out of the dialog -- which, with no window left to drag bigger,
        # made the editor a dead end.
        bar = tk.Frame(self.top, bg=BG)
        bar.pack(side="bottom", fill="x", padx=18, pady=14)
        self.body = tk.Frame(self.top, bg=BG)
        self.body.pack(fill="both", expand=True, padx=18)
        headers = [
            ("Display", 18), ("Width px", 9), ("Height px", 9),
            ("Rotation", 9), ("Refresh Hz", 10), ("Physical width", 13),
        ]
        head = tk.Frame(self.body, bg=BG)
        head.pack(fill="x")
        for text, width in headers:
            tk.Label(head, text=text, width=width, anchor="w",
                     bg=BG, fg=MUTED,
                     font=("Segoe UI", 8, "bold")).pack(
                         side="left", padx=(0, 5))
        for display in canvas.mac_displays(device_id):
            self._add_row(display)
        ttk.Button(bar, text="+ Add display", command=self._add_row).pack(
            side="left")
        ttk.Button(bar, text="Cancel", command=self.top.destroy).pack(
            side="right")
        ttk.Button(bar, text="Save displays", style="Accent.TButton",
                   command=self._save).pack(side="right", padx=8)
        self.top.protocol("WM_DELETE_WINDOW", self.top.destroy)
        self.top.grab_set()
        self.top.focus_force()

    def _fresh_display_id(self):
        """An id belonging to THIS device.

        Adding a screen used to mint "mac-N" whatever device you were editing --
        the last hardcoded remnant of the two-device model, living in the one
        dialog that is used for every device. A third device's second screen
        therefore came out as "mac-2", colliding with the Mac's own."""
        base = self.device_id or "display"
        used = {row["id"] for row in self.rows}
        index = len(self.rows) + 1
        while f"{base}-{index}" in used:
            index += 1
        return f"{base}-{index}"

    def _add_row(self, display=None):
        if len(self.rows) >= 8:
            return
        display = dict(display or {})
        frame = tk.Frame(self.body, bg=BG)
        frame.pack(fill="x", pady=3)
        values = {
            "id": display.get("id", self._fresh_display_id()),
            "name": tk.StringVar(
                value=display.get("name", f"Screen {len(self.rows) + 1}")),
            "res_w": tk.StringVar(value=str(display.get("res_w", 3840))),
            "res_h": tk.StringVar(value=str(display.get("res_h", 2160))),
            "rotation": tk.StringVar(
                value=f"{int(display.get('rotation', 0))}°"),
            "refresh_hz": tk.StringVar(
                value=str(display.get("refresh_hz", 60))),
            "physical_width": tk.StringVar(value=(
                f"{float(display.get('diagonal_in') or 24):g}"
            )),
        }
        specs = [
            (values["name"], 18), (values["res_w"], 9),
            (values["res_h"], 9),
        ]
        for variable, width in specs:
            tk.Entry(
                frame, textvariable=variable, width=width, bg=CARD, fg=FG,
                insertbackground=FG, relief="flat",
                font=("Segoe UI", 9)).pack(side="left", padx=(0, 5), ipady=5)
        ttk.Combobox(
            frame, textvariable=values["rotation"], width=7,
            values=("0°", "90°", "180°", "270°"),
            state="readonly").pack(side="left", padx=(0, 5))
        tk.Entry(
            frame, textvariable=values["refresh_hz"], width=10,
            bg=CARD, fg=FG, insertbackground=FG, relief="flat",
            font=("Segoe UI", 9)).pack(side="left", padx=(0, 5), ipady=5)
        tk.Entry(
            frame, textvariable=values["physical_width"], width=13,
            bg=CARD, fg=FG, insertbackground=FG, relief="flat",
            font=("Segoe UI", 9)).pack(side="left", padx=(0, 5), ipady=5)
        tk.Label(frame, text="in", bg=BG, fg=MUTED).pack(side="left")
        ttk.Button(
            frame, text="Remove",
            command=lambda row=values, holder=frame:
                self._remove_row(row, holder)).pack(side="right")
        values["_frame"] = frame
        self.rows.append(values)

    def _remove_row(self, row, frame):
        if len(self.rows) <= 1:
            return
        self.rows.remove(row)
        frame.destroy()

    def _save(self):
        raw = []
        for row in self.rows:
            raw.append({
                "id": row["id"],
                "name": row["name"].get(),
                "res_w": row["res_w"].get(),
                "res_h": row["res_h"].get(),
                "rotation": row["rotation"].get().replace("°", ""),
                "refresh_hz": row["refresh_hz"].get(),
                "physical_width": row["physical_width"].get(),
            })
        try:
            self.canvas.replace_mac_displays(raw, self.device_id)
        except ValueError as exc:
            dark_alert(self.top, "Check the display values", str(exc))
            return
        self.top.destroy()


class BtPanel(tk.Frame):
    """Bluetooth & headphones, embedded in the main window (a notebook tab).
    Right-click any device for its actions. Custom names and a blacklist are
    saved locally (bt_prefs.json), so a rename survives re-pairing and
    blacklisted devices never appear in scans."""

    def __init__(self, master, app=None):
        super().__init__(master, bg=BG)
        self.app = app
        self._refreshing = False
        self._refresh_pending = False  # trailing rerun, never a swallow
        self._conn_busy = False  # one connect-retry loop at a time
        self._connected = set()
        self._connected_names = []  # display names, for the compact view
        self._seen = {}  # mac -> (name, icon, controller), seen this session
        #                  kept in the list even after BlueZ purges an un-bonded
        #                  device, so a failed Connect never drops it from view.
        self.prefs = load_bt_prefs()
        self._radios = []
        self._device_radios = {}
        self._radio_choices = {}
        self.show_blk = tk.BooleanVar(value=False)
        self.radio_mode = tk.StringVar(value=(
            "Multiple radios" if multi_radio_enabled(self.prefs)
            else "Single radio (recommended)"))
        self.hid_radio = tk.StringVar(value="")
        self.mac_radio = tk.StringVar(value="")
        self.scan_radio = tk.StringVar(value="")

        tk.Label(self, text="Put headphones in pairing mode, Scan, then "
                            "RIGHT-CLICK a device: Connect, Rename, Blacklist, "
                            "Forget. Renames + blacklist are saved and survive "
                            "re-pairing.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 9), wraplength=500,
                 justify="left").pack(anchor="w", padx=12, pady=(10, 0))

        options = ttk.LabelFrame(self, text="Radio options", padding=7)
        options.pack(fill="x", padx=12, pady=(7, 2))
        mode_row = tk.Frame(options, bg=BG)
        mode_row.pack(fill="x")
        tk.Label(mode_row, text="Setup", bg=BG, fg=FG,
                 font=("Segoe UI", 9)).pack(side="left")
        self.mode_combo = ttk.Combobox(
            mode_row, textvariable=self.radio_mode, state="readonly",
            values=("Single radio (recommended)", "Multiple radios"),
            width=25)
        self.mode_combo.pack(side="right")
        self.mode_combo.bind("<<ComboboxSelected>>", self._on_mode_changed)

        assign_row = tk.Frame(options, bg=BG)
        assign_row.pack(fill="x", pady=(6, 0))
        tk.Label(assign_row, text="iPad keyboard", bg=BG, fg=FG,
                 font=("Segoe UI", 9)).pack(side="left")
        self.hid_combo = ttk.Combobox(
            assign_row, textvariable=self.hid_radio, state="disabled",
            width=25)
        self.hid_combo.pack(side="right")
        self.hid_combo.bind("<<ComboboxSelected>>", self._on_hid_radio)

        mac_row = tk.Frame(options, bg=BG)
        mac_row.pack(fill="x", pady=(4, 0))
        tk.Label(mac_row, text="Managed Mac", bg=BG, fg=FG,
                 font=("Segoe UI", 9)).pack(side="left")
        self.mac_combo = ttk.Combobox(
            mac_row, textvariable=self.mac_radio, state="disabled",
            width=25)
        self.mac_combo.pack(side="right")
        self.mac_combo.bind("<<ComboboxSelected>>", self._on_mac_radio)

        scan_row = tk.Frame(options, bg=BG)
        scan_row.pack(fill="x", pady=(4, 0))
        tk.Label(scan_row, text="Scan from", bg=BG, fg=FG,
                 font=("Segoe UI", 9)).pack(side="left")
        self.scan_combo = ttk.Combobox(
            scan_row, textvariable=self.scan_radio, state="disabled",
            width=25)
        self.scan_combo.pack(side="right")
        self.scan_combo.bind("<<ComboboxSelected>>", self._on_scan_radio)
        self.radio_note = tk.StringVar(
            value="Single-radio compatibility is active.")
        tk.Label(options, textvariable=self.radio_note, bg=BG, fg=MUTED,
                 font=("Segoe UI", 8), anchor="w", justify="left",
                 wraplength=470).pack(fill="x", pady=(5, 0))
        # ---- which radios the VM actually holds -------------------------
        # A dongle that has been unplugged and plugged back in is claimed by
        # Windows, and VirtualBox only auto-captures at the moment a device
        # ARRIVES. From in here that was indistinguishable from a device that
        # simply would not connect -- nothing in the app had ever looked at the
        # host's USB list. Now it says so, and offers the one action that fixes
        # it. Nothing about this scans, pairs or connects anything.
        self.radio_usb = tk.StringVar(value="Checking which radios the VM "
                                            "holds…")
        tk.Label(options, textvariable=self.radio_usb, bg=BG, fg=MUTED,
                 font=("Segoe UI", 8), anchor="w", justify="left",
                 wraplength=470).pack(fill="x", pady=(6, 0))
        button_row = tk.Frame(options, bg=BG)
        button_row.pack(fill="x", pady=(5, 0))
        self.reclaim_btn = ttk.Button(
            button_row, text="Repair radios", command=self._reclaim_radios)
        self.reclaim_btn.pack(side="left")
        self.recommended_btn = ttk.Button(
            button_row, text="Use recommended 3-radio layout",
            command=self._use_recommended_radios)
        self.recommended_btn.pack(side="right")

        self.info = tk.StringVar(value="")
        tk.Label(self, textvariable=self.info, bg=BG, fg=ACCENT,
                 font=("Consolas", 9), anchor="w").pack(fill="x", padx=12,
                                                        pady=(4, 0))

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=6)
        self.tree = ttk.Treeview(body, columns=("name", "status", "type",
                                                "radio", "addr"),
                                 show="headings", selectmode="browse",
                                 height=8)
        self.tree.heading("name", text="Device")
        self.tree.heading("status", text="Status")
        self.tree.heading("type", text="Type")
        self.tree.heading("radio", text="Radio")
        self.tree.heading("addr", text="Address")
        self.tree.column("name", width=155, anchor="w")
        self.tree.column("status", width=105, anchor="w")
        self.tree.column("type", width=75, anchor="w")
        self.tree.column("radio", width=105, anchor="w")
        self.tree.column("addr", width=125, anchor="w")
        self.tree.tag_configure("connected", foreground=ACCENT)
        self.tree.tag_configure("paired", foreground=FG)
        self.tree.tag_configure("available", foreground=MUTED)
        self.tree.tag_configure("blacklisted", foreground=DANGER)
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(body, command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.config(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", lambda e: self.connect())
        self.tree.bind("<Button-3>", self._popup)
        # deferred: two VBoxManage calls must not sit in front of the first
        # paint, and the answer is worth having before anything is clicked
        self.after(1200, self._radio_usb_check)

        self.menu = tk.Menu(self, tearoff=0, bg=CARD, fg=FG,
                            activebackground=ACCENT_DIM,
                            activeforeground="#eafff3", bd=0)

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=12, pady=(0, 4))
        self.btn_scan = ttk.Button(bar, text="🔍 Scan", command=self.scan)
        self.btn_scan.pack(side="left")
        ttk.Button(bar, text="↻ Refresh", command=self.refresh).pack(
            side="left", padx=6)
        ttk.Checkbutton(bar, text="Show blacklisted", variable=self.show_blk,
                        command=self.refresh).pack(side="left", padx=8)
        ttk.Button(bar, text="⟳ Restart audio",
                   command=self._restart_all).pack(side="right")

        self.out = tk.Text(self, bg="#0e1015", fg="#b7c0d4", height=5, bd=0,
                           font=("Consolas", 9), wrap="word",
                           insertbackground=FG)
        self.out.pack(fill="both", expand=False, padx=12, pady=(4, 10))
        self._log("Ready. Right-click a device for its actions.")
        self._set_radio_controls()
        self.refresh()

    # ---- radios the VM has lost ------------------------------------------
    def _radio_usb_apply(self, text, lost):
        def apply():
            self.radio_usb.set(text)
            if lost:
                self.reclaim_btn.config(
                    text=f"Repair {lost} radio" + ("s" if lost != 1 else ""))
            else:
                self.reclaim_btn.config(text="Repair radios")
        if self.app:
            self.app.ui(apply)      # workers never touch Tk, not even after()

    def _radio_usb_check(self):
        """Report how many of this machine's radios the guest can see.

        Two read-only VBoxManage calls, on a worker thread -- the UI must not
        wait on a subprocess.
        """
        def work():
            if not vm_running():
                self._radio_usb_apply(
                    "The VM is not running, so it holds no radios. Start it on "
                    "the Bridge tab.", 0)
                return
            state = read_radio_state()
            config = self.app.canvas.config if self.app else {}
            total, lost = len(state["mine"]), len(state["lost"])
            if not total:
                self._radio_usb_apply(
                    "No Bluetooth adapter on this machine matches one of the "
                    "VM's USB filters.", 0)
            elif not lost:
                self._radio_usb_apply(
                    f"All {total} radios are attached to the VM.", 0)
            else:
                names = ", ".join(usb_label(d, config) for d in state["lost"])
                self._radio_usb_apply(
                    f"{total - lost} of {total} radios are attached. The VM "
                    f"does not have {names}, so that machine cannot connect. "
                    f"Repair explains what to do.", lost)
        threading.Thread(target=work, daemon=True).start()

    def _reclaim_radios(self):
        """Hand every radio the VM has lost back to it, and say what happened."""
        self.reclaim_btn.config(state="disabled")

        def work():
            try:
                if not vm_running():
                    self._log("radios: the VM is not running — start it first.")
                    self._radio_usb_check()
                    return
                state = read_radio_state()
                if not state["mine"]:
                    self._log("radios: nothing on this machine matches the "
                              "VM's USB filters. Check the filters in "
                              "VirtualBox.")
                    self._radio_usb_check()
                    return
                if not state["lost"]:
                    self._log(f"radios: all {len(state['mine'])} are already "
                              f"attached to the VM — nothing to reclaim.")
                    self._radio_usb_check()
                    return
                config = self.app.canvas.config if self.app else {}
                for device in state["lost"]:
                    self._log(f"radios: {usb_label(device, config)} is "
                              f"{device.get('state', '?')} on the host "
                              f"(filter “{device.get('filter', '?')}”, serial "
                              f"{device.get('serial') or 'none'}) — repairing")
                self._radio_usb_apply(
                    f"Repairing {len(state['lost'])} radio(s)…", 0)
                outcome = repair_radios(config)
                for line in outcome["pinned"]:
                    self._log(f"radios: pinned filter {line} — that filter now "
                              f"matches one dongle and nothing else.")
                for name, why in outcome["pin_failed"]:
                    self._log(f"radios: could not pin filter {name} — {why}")
                for name in outcome["recovered"]:
                    self._log(f"radios: {name} attached — the VM has it.")
                for name, reason in outcome["failed"]:
                    self._log(f"radios: {name} did NOT reach the VM. {reason}")
                # The status line is the one being read. An outcome that exists
                # only in this log box is an outcome the user does not have.
                if outcome["failed"]:
                    name, reason = outcome["failed"][0]
                    self._radio_usb_apply(f"{name}: {reason}",
                                          len(outcome["failed"]))
                    self._log("radios: if a replug does not take, restart the "
                              "VM — that rebuilds its USB state from scratch. "
                              "Note the iPad needs re-pairing after a VM "
                              "power-off.")
                else:
                    self._radio_usb_check()
                if outcome["recovered"]:
                    self._log("radios: give BlueZ a few seconds to enumerate, "
                              "then Connect each device.")
                    if self.app:
                        self.app.ui(lambda: self.after(6000, self.refresh))
            finally:
                if self.app:
                    self.app.ui(
                        lambda: self.reclaim_btn.config(state="normal"))
        threading.Thread(target=work, daemon=True).start()

    def _log(self, msg):
        # callable from worker threads: queue the Text mutation to the UI
        # thread via App.ui() (workers must never call into Tk, even after())
        def put():
            self.out.insert("end", msg.rstrip() + "\n")
            self.out.see("end")
        if self.app:
            self.app.ui(put)
        else:
            put()
        _emit("bt", msg)  # mirror into the main console too

    def _reachable(self):
        return ssh_guest("echo ok", timeout=6, quiet=True).stdout.strip() == "ok"

    def _sel_mac(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _multi(self):
        return multi_radio_enabled(self.prefs)

    def _radio_display(self, radio):
        address = radio.get("address", "")
        label = self.prefs["radio_labels"].get(address)
        if not label:
            label = radio.get("hardware") or radio.get("alias") \
                or radio.get("name") \
                or radio.get("hci") or "Bluetooth radio"
        suffix = address[-5:] if address else radio.get("hci", "")
        return f"{label} ({suffix})"

    def _radio_label(self, controller):
        if not controller:
            return "Default"
        for radio in self._radios:
            if radio.get("address") == controller:
                return self._radio_display(radio)
        return f"Unavailable ({controller[-5:]})"

    def _selected_controller(self, variable):
        return self._radio_choices.get(variable.get(), "")

    def _controller_for(self, mac):
        return (self.prefs["radio_assignments"].get(mac)
                or self._device_radios.get(mac)
                or self.prefs.get("scan_radio")
                or self.prefs.get("hid_radio")
                or (self._radios[0]["address"] if self._radios else ""))

    def _set_radio_controls(self):
        enabled = self._multi()
        available = bool(self._radios)
        state = "readonly" if enabled and available else "disabled"
        self.hid_combo.configure(state=state)
        self.mac_combo.configure(state=state)
        self.scan_combo.configure(state=state)
        self.recommended_btn.state(
            ["!disabled"] if enabled and len(self._radios) >= 3
            else ["disabled"])
        if not enabled:
            self.radio_note.set(
                "Single-radio compatibility is active. Existing behavior is "
                "unchanged.")
        elif available:
            self.radio_note.set(
                f"{len(self._radios)} radio"
                f"{'s' if len(self._radios) != 1 else ''} available. "
                "Right-click a device to assign it.")
        else:
            self.radio_note.set(
                "No guest radios are available yet. The host restart is still "
                "required before the three-radio bench test.")

    def _refresh_radio_choices(self):
        self._radio_choices = {
            self._radio_display(radio): radio["address"]
            for radio in self._radios
        }
        values = tuple(self._radio_choices)
        self.hid_combo.configure(values=values)
        self.mac_combo.configure(values=values)
        self.scan_combo.configure(values=values)
        addresses = {radio["address"] for radio in self._radios}
        if self._multi() and addresses:
            first = self._radios[0]["address"]
            hid = self.prefs.get("hid_radio")
            mac = self.prefs.get("mac_radio")
            scan = self.prefs.get("scan_radio")
            if not hid:
                hid = first
                self.prefs["hid_radio"] = hid
            if not scan:
                scan = hid
                self.prefs["scan_radio"] = scan
            save_bt_prefs(self.prefs)
            reverse = {address: label
                       for label, address in self._radio_choices.items()}
            self.hid_radio.set(
                reverse.get(hid, self._radio_label(hid)))
            self.mac_radio.set(
                reverse.get(mac, self._radio_label(mac)) if mac else "")
            self.scan_radio.set(
                reverse.get(scan, self._radio_label(scan)))
        else:
            self.hid_radio.set("")
            self.mac_radio.set("")
            self.scan_radio.set("")
        self._set_radio_controls()

    def _on_mode_changed(self, _event=None):
        enabled = self.radio_mode.get() == "Multiple radios"
        self.prefs["radio_mode"] = "multi" if enabled else "single"
        save_bt_prefs(self.prefs)
        self._refresh_radio_choices()
        if enabled:
            self._log("multiple-radio mode enabled — assignments are now "
                      "controller-specific.")
        else:
            self._log("single-radio compatibility restored.")
        self.refresh(quiet=True)

        def apply():
            if not self._reachable():
                return
            if enabled:
                controller = self.prefs.get("hid_radio")
                if not controller:
                    return
                command = ("bash /opt/openspan/set-hid-radio.sh "
                           f"{controller}")
            else:
                command = ("bash /opt/openspan/set-hid-radio.sh "
                           "--default")
            r = ssh_guest(command, timeout=35, show_result=False)
            if r.returncode:
                detail = (r.stderr or r.stdout or "guest command failed").strip()
                self._log("could not apply radio mode — " + detail[-220:])
            else:
                self._log("keyboard radio mode applied.")
        threading.Thread(target=apply, daemon=True).start()

    def _on_hid_radio(self, _event=None):
        controller = self._selected_controller(self.hid_radio)
        if not controller:
            return
        if controller == self.prefs.get("mac_radio"):
            dark_alert(
                self, "Choose another radio",
                "The iPad and managed Mac each need an independent Bluetooth "
                "radio. Assign one of them to a different radio.")
            self._refresh_radio_choices()
            return
        self.prefs["hid_radio"] = controller
        save_bt_prefs(self.prefs)
        self._log("assigning the iPad keyboard to "
                  f"{self._radio_label(controller)}…")

        def apply():
            r = ssh_guest(
                "bash /opt/openspan/set-hid-radio.sh " + controller,
                timeout=35, show_result=False)
            if r.returncode:
                detail = (r.stderr or r.stdout or "guest command failed").strip()
                self._log("keyboard radio assignment FAILED — "
                          + detail[-220:])
            else:
                self._log("iPad keyboard radio assigned.")
            if self.app:
                self.app._refresh_all_device_paired()
        threading.Thread(target=apply, daemon=True).start()

    def _on_mac_radio(self, _event=None):
        controller = self._selected_controller(self.mac_radio)
        if not controller:
            return
        if controller == self.prefs.get("hid_radio"):
            dark_alert(
                self, "Choose another radio",
                "The managed Mac and iPad cannot share a radio. Move the iPad "
                "to the internal backup radio first, then assign this external "
                "TP-Link radio to the Mac.")
            self._refresh_radio_choices()
            return
        self.prefs["mac_radio"] = controller
        save_bt_prefs(self.prefs)
        self._log("assigning the managed Mac to "
                  f"{self._radio_label(controller)}…")

        def apply():
            r = ssh_guest(
                "bash /opt/openspan/set-hid-target.sh mac " + controller,
                timeout=45, show_result=False)
            if r.returncode:
                detail = (r.stderr or r.stdout or "guest command failed").strip()
                self._log("managed Mac radio assignment FAILED — "
                          + detail[-220:])
            else:
                self._log("managed Mac radio assigned and ready.")
            if self.app:
                self.app._refresh_all_device_paired()
        threading.Thread(target=apply, daemon=True).start()

    def _on_scan_radio(self, _event=None):
        controller = self._selected_controller(self.scan_radio)
        if not controller:
            return
        self.prefs["scan_radio"] = controller
        save_bt_prefs(self.prefs)
        self._log("future scans will use "
                  f"{self._radio_label(controller)}.")

    def _use_recommended_radios(self):
        if len(self._radios) < 3:
            return
        external = []
        internal = []
        for radio in self._radios:
            text = " ".join(str(radio.get(key, "")) for key in (
                "hardware", "alias", "name", "vendor", "product")).lower()
            label = self.prefs.get("radio_labels", {}).get(
                radio.get("address", ""), "").lower()
            if "tp-link" in text or "tp-link" in label \
                    or "2357" in text:
                external.append(radio["address"])
            else:
                internal.append(radio["address"])
        if len(external) < 2 or not internal:
            dark_alert(
                self, "Couldn’t identify the three radios",
                "Assign the internal radio to iPad, one external TP-Link to "
                "Managed Mac, and the other external TP-Link to Scan/audio.")
            return
        self.prefs["hid_radio"] = internal[0]
        self.prefs["mac_radio"] = external[0]
        self.prefs["scan_radio"] = external[1]
        save_bt_prefs(self.prefs)
        self._refresh_radio_choices()
        self._log(
            "recommended layout saved — internal backup: iPad; external "
            "TP-Link 1: managed Mac; external TP-Link 2: audio/scan.")

        def apply():
            ipad = self.prefs["hid_radio"]
            mac = self.prefs["mac_radio"]
            first = ssh_guest(
                "bash /opt/openspan/set-hid-target.sh ipad " + ipad,
                timeout=40, show_result=False)
            second = ssh_guest(
                "bash /opt/openspan/set-hid-target.sh mac " + mac,
                timeout=45, show_result=False)
            if first.returncode or second.returncode:
                self._log("one of the recommended radio assignments failed — "
                          "see the console.")
            else:
                self._log("all three radio lanes are active.")
            if self.app:
                self.app._refresh_all_device_paired()
        threading.Thread(target=apply, daemon=True).start()

    def _assign_radio(self, controller):
        mac = self._sel_mac()
        if not mac:
            return
        if controller:
            self.prefs["radio_assignments"][mac] = controller
            self._device_radios[mac] = controller
            self._log(f"{mac} assigned to {self._radio_label(controller)}.")
        else:
            self.prefs["radio_assignments"].pop(mac, None)
            self._log(f"{mac} radio assignment set to automatic.")
        save_bt_prefs(self.prefs)
        self.refresh(quiet=True)

    def _popup(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        mac = row
        m = self.menu
        m.delete(0, "end")
        if mac in self.prefs["blacklist"]:
            m.add_command(label="Un-blacklist (show again)",
                          command=self.unblacklist)
        else:
            if mac in self._connected:
                m.add_command(label="Disconnect", command=self.disconnect)
            else:
                m.add_command(label="🎧  Connect", command=self.connect)
            m.add_command(label="Rename…", command=self.rename)
            if self._multi() and self._radios:
                assign = tk.Menu(
                    m, tearoff=0, bg=CARD, fg=FG,
                    activebackground=ACCENT_DIM,
                    activeforeground="#eafff3", bd=0)
                current = self.prefs["radio_assignments"].get(mac, "")
                assign.add_command(
                    label=("✓ Automatic" if not current else "Automatic"),
                    command=lambda: self._assign_radio(""))
                assign.add_separator()
                for radio in self._radios:
                    controller = radio["address"]
                    label = self._radio_display(radio)
                    if controller == current:
                        label = "✓ " + label
                    assign.add_command(
                        label=label,
                        command=lambda value=controller:
                            self._assign_radio(value))
                self.assign_menu = assign
                m.add_cascade(label="Assign to radio", menu=assign)
            m.add_separator()
            m.add_command(label="Blacklist (hide from scans)",
                          command=self.blacklist)
            m.add_command(label="Forget (unpair)", command=self.forget)
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _restart_all(self):
        if not dark_confirm(
                self, "Restart audio?",
                "Restarts just the audio pipeline (~15s). Your iPad keyboard "
                "is NOT affected.\n\nRestart audio now?"):
            return
        self._log("restarting the audio pipeline (keyboard untouched)…")
        if self.app:
            self.app.restart_everything(log=self._log)

    def refresh(self, quiet=False):
        if self._refreshing:
            # never swallow a refresh: an in-flight pass may carry a
            # PRE-link snapshot; queue one trailing rerun instead
            self._refresh_pending = True
            return
        self._refreshing = True

        def ui(fn):
            if self.app:
                self.app.ui(fn)  # queue -> UI thread; safe from any thread

        def work():
            # network I/O only in this thread; every widget mutation is
            # marshaled to the UI thread via after()
            try:
                if not self._reachable():
                    if vm_running():
                        msg = ("VM is starting up (~90s)… refreshes "
                               "automatically when ready.")
                    else:
                        msg = ("VM isn't running — Start VM on the iPad "
                               "Bridge tab.")

                    def apply_unreachable():
                        self.info.set(msg)
                        self._connected_names = []  # VM down = nothing linked
                        self.after(5000, self.refresh)  # retry until reachable
                    ui(apply_unreachable)
                    return
                rows = []
                radios = []
                if self._multi():
                    r = ssh_guest(
                        "python3 /opt/openspan/openspan_bt.py list",
                        timeout=25, quiet=quiet, show_result=False)
                    try:
                        inventory = json.loads(r.stdout or "{}")
                    except (TypeError, ValueError):
                        inventory = {}
                    radios = list(inventory.get("radios", []))
                    candidates = {}
                    for item in inventory.get("devices", []):
                        mac = str(item.get("address", "")).upper()
                        controller = str(
                            item.get("controller", "")).upper()
                        if not re.match(
                                r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$", mac):
                            continue
                        assigned = self.prefs[
                            "radio_assignments"].get(mac, "")
                        score = (
                            controller == assigned,
                            bool(item.get("connected")),
                            bool(item.get("paired")),
                        )
                        old = candidates.get(mac)
                        if old is None or score > old[0]:
                            candidates[mac] = (score, item)
                    for _score, item in candidates.values():
                        rows.append((
                            str(item.get("address", "")).upper(),
                            str(item.get("alias") or item.get("name")
                                or item.get("address", "")),
                            bool(item.get("paired")),
                            bool(item.get("connected")),
                            str(item.get("icon", "")),
                            str(item.get("controller", "")).upper(),
                        ))
                else:
                    r = ssh_guest("bash /opt/openspan/bt-list.sh", timeout=25,
                                  quiet=quiet, show_result=False)
                    for line in (r.stdout or "").splitlines():
                        p = line.split("|")
                        if len(p) >= 4 and re.match(
                                r"^[0-9A-Fa-f]{2}"
                                r"(:[0-9A-Fa-f]{2}){5}$", p[0]):
                            rows.append((
                                p[0].upper(), p[1], p[2] == "1",
                                p[3] == "1",
                                p[4] if len(p) > 4 else "", ""))
                # Remember everything BlueZ currently knows, then add back any
                # device we've seen this session that BlueZ has since purged
                # (an un-bonded device is dropped the moment discovery stops).
                # Those come back as plain "available" so a failed Connect never
                # makes the buds vanish from the list — you can just try again.
                live = set()
                for mac, name, paired, conn, icon, controller in rows:
                    live.add(mac)
                    self._seen[mac] = (name, icon, controller)
                for mac, (name, icon, controller) in self._seen.items():
                    if mac not in live:
                        rows.append((
                            mac, name, False, False, icon, controller))
                rows.sort(key=lambda x: (not x[3], not x[2], x[1].lower()))
                ui(lambda: self._apply_rows(rows, radios))
            finally:
                self._refreshing = False
                if self._refresh_pending:
                    self._refresh_pending = False
                    self.refresh(quiet=True)  # the queued trailing rerun
        threading.Thread(target=work, daemon=True).start()

    def _apply_rows(self, rows, radios=None):
        """Rebuild the device list. UI thread only."""
        if radios is not None:
            self._radios = radios
            self._refresh_radio_choices()
        keep = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self._connected = set()
        self._device_radios = {}
        names = []
        nconn = nhidden = 0
        show_blk = self.show_blk.get()
        for mac, name, paired, conn, icon, controller in rows:
            blk = mac in self.prefs["blacklist"]
            if blk and not show_blk:
                nhidden += 1
                continue
            if controller:
                self._device_radios[mac] = controller
            nm = self.prefs["renames"].get(mac, name)
            typ = ("🎧 audio" if "audio" in (icon or "")
                   else (icon or "device"))
            if blk:
                status, tag = "⛔ Blacklisted", "blacklisted"
            elif conn:
                status, tag = "● Connected", "connected"
                nconn += 1
                self._connected.add(mac)
                names.append(nm)
            elif paired:
                status, tag = "○ Paired (idle)", "paired"
            else:
                status, tag = "Available", "available"
            assigned = self.prefs["radio_assignments"].get(mac)
            shown_controller = assigned or controller
            radio_name = (self._radio_label(shown_controller)
                          if self._multi() else "Default")
            self.tree.insert("", "end", iid=mac,
                             values=(nm, status, typ, radio_name, mac),
                             tags=(tag,))
        for k in keep:
            if self.tree.exists(k):
                self.tree.selection_set(k)
        self._connected_names = names
        extra = f" · {nhidden} blacklisted hidden" if nhidden else ""
        self.info.set(f"{nconn} connected{extra}  —  right-click a "
                      f"device for actions")

    def _retry_lock(self, what):
        """True (and logs) when the connect-retry loop is running: BT
        actions during it would race the guest script — bt-connect.sh's
        cleanup kills a live Scan, and a Forget would be silently undone
        by the next attempt re-pairing the device."""
        if self._conn_busy:
            self._log(f"{what} is locked while connect attempts run — "
                      "give it a few seconds.")
            return True
        return False

    def scan(self):
        if self._retry_lock("Scan"):
            return
        controller = self._controller_for("") if self._multi() else ""
        if self._multi() and not controller:
            self._log("scan needs an available radio — refresh after the "
                      "host restart.")
            return
        self.btn_scan.state(["disabled"])
        where = (f" with {self._radio_label(controller)}"
                 if controller else "")
        self._log(f"scanning 10s{where} — make sure headphones are blinking…")
        def work():
            if self.app:
                self.app._manual_bt_begin()
            try:
                if self._multi():
                    command = (
                        "python3 /opt/openspan/openspan_bt.py scan "
                        f"--controller {controller} --seconds 10")
                else:
                    command = ("source /opt/openspan/env.sh; "
                               "bluetoothctl --timeout 10 scan on")
                r = ssh_guest(
                    command, timeout=18, show_result=False)
            finally:
                if self.app:
                    self.app._manual_bt_end()
            if r.returncode == 0:
                self._log("scan done.")
            else:
                detail = (r.stderr or r.stdout or "guest SSH failed").strip()
                self._log("scan FAILED — " + detail[-240:])
            self.refresh()
            if self.app:
                self.app.ui(lambda: self.btn_scan.state(["!disabled"]))
        threading.Thread(target=work, daemon=True).start()

    def connect(self):
        mac = self._sel_mac()
        if not mac or mac in self.prefs["blacklist"]:
            return
        if self._conn_busy:
            self._log("already trying to connect — hold on…")
            return
        controller = self._controller_for(mac) if self._multi() else ""
        if self._multi() and not controller:
            self._log("assign this device to an available radio first.")
            return
        self._conn_busy = True
        where = (f" through {self._radio_label(controller)}"
                 if controller else "")
        self._log(f"connecting {mac}{where} — up to 5 attempts over ~30s…")

        def work():
            # keep trying: earbuds waking from the case routinely miss the
            # first page. Stop the moment a real link exists; a first-time
            # pairing pass ("paired ✓") rolls straight into the connect on
            # the next attempt (with a FRESH time budget — a slow pairing
            # must not eat the connect's 30s). A failed pairing stops the
            # loop outright: retrying it would fire another 30s scan volley
            # per attempt.
            if self.app:
                self.app._manual_bt_begin()
            try:
                t0 = time.time()
                for attempt in range(1, 6):
                    # first attempt may hit the (long) pairing branch; the
                    # bonded fast path needs only a few seconds — a shorter
                    # timeout keeps one wedged ssh from holding the loop
                    if self._multi():
                        command = (
                            "python3 /opt/openspan/openspan_bt.py connect "
                            f"--controller {controller} --device {mac}")
                    else:
                        command = (
                            f"bash /opt/openspan/bt-connect.sh {mac}")
                    r = ssh_guest(
                        command, timeout=70 if attempt == 1 else 20,
                        show_result=False)
                    out = (r.stdout or r.stderr or "").strip()
                    self._log(f"[{attempt}/5] {out[-260:]}")
                    if "CONNECTED" in out:
                        break
                    if self._multi() and "ERROR:" in out:
                        self._log("connect stopped — check the assignment and "
                                  "put the device in pairing mode.")
                        break
                    if "pairing didn't take" in out:
                        self._log("pairing needs the buds BLINKING — put "
                                  "them in pairing mode and click Connect "
                                  "again (one scan per click).")
                        break
                    if "paired ✓" in out or "PAIRED" in out:
                        t0 = time.time()  # fresh budget for the connect
                    if attempt >= 5 or time.time() - t0 > 30:
                        self._log("no luck after retries — wake the buds "
                                  "(pop them back in pairing mode) and "
                                  "Connect again.")
                        break
                    threading.Event().wait(2.5)
            finally:
                self._conn_busy = False
                if self.app:
                    self.app._manual_bt_end()
            self.refresh()
        threading.Thread(target=work, daemon=True).start()

    def disconnect(self):
        mac = self._sel_mac()
        if not mac or self._retry_lock("Disconnect"):
            return
        controller = self._controller_for(mac) if self._multi() else ""
        if self._multi() and not controller:
            self._log("disconnect needs a valid radio assignment.")
            return
        self._log(f"disconnecting {mac}…")
        def work():
            if self.app:
                self.app._manual_bt_begin()
            try:
                if self._multi():
                    command = (
                        "python3 /opt/openspan/openspan_bt.py disconnect "
                        f"--controller {controller} --device {mac}")
                else:
                    command = f"bluetoothctl disconnect {mac}"
                ssh_guest(command, timeout=15)
            finally:
                if self.app:
                    self.app._manual_bt_end()
            self._log("disconnected.")
            self.refresh()
        threading.Thread(target=work, daemon=True).start()

    def forget(self):
        mac = self._sel_mac()
        if not mac or self._retry_lock("Forget"):
            return
        controller = self._controller_for(mac) if self._multi() else ""
        if self._multi() and not controller:
            self._log("forget needs a valid radio assignment.")
            return
        self._log(f"forgetting {mac}…")
        self._seen.pop(mac, None)  # don't let it reappear as "available"
        def work():
            if self.app:
                self.app._manual_bt_begin()
            try:
                if self._multi():
                    command = (
                        "python3 /opt/openspan/openspan_bt.py forget "
                        f"--controller {controller} --device {mac}")
                else:
                    command = (
                        f"bluetoothctl disconnect {mac} >/dev/null 2>&1; "
                        f"bluetoothctl remove {mac}")
                ssh_guest(command, timeout=20)
            finally:
                if self.app:
                    self.app._manual_bt_end()
            self.refresh()
        threading.Thread(target=work, daemon=True).start()

    def rename(self):
        """Inline rename: drop an Entry right on top of the Device cell."""
        mac = self._sel_mac()
        if not mac or not self.tree.exists(mac):
            return
        self.tree.see(mac)
        self.tree.update_idletasks()
        bbox = self.tree.bbox(mac, "name")
        if not bbox:
            return
        x, y, w, h = bbox
        cur = self.prefs["renames"].get(mac, "")
        if not cur:
            vals = self.tree.item(mac, "values")
            cur = vals[0] if vals else ""
        ed = tk.Entry(self.tree, bg=CARD, fg=FG, insertbackground=FG,
                      relief="flat", font=("Segoe UI", 10))
        ed.insert(0, cur)
        ed.select_range(0, "end")
        ed.place(x=x, y=y, width=w, height=h)
        ed.focus_set()
        done = {"v": False}

        def commit(_=None):
            if done["v"]:
                return
            done["v"] = True
            # strip chars the remote shell would interpret inside the
            # double-quoted busctl argument (`, $, \, ")
            new = re.sub(r'[`$\\"]', "", ed.get()).strip()
            ed.destroy()
            if new:
                self.prefs["renames"][mac] = new
            else:
                self.prefs["renames"].pop(mac, None)
            save_bt_prefs(self.prefs)
            self._log(f"renamed {mac} → “{new or '(default)'}”")
            controller = self._controller_for(mac) if self._multi() else ""
            if self._multi() and controller:
                command = (
                    "python3 /opt/openspan/openspan_bt.py alias "
                    f"--controller {controller} --device {mac} "
                    f'--name "{new}"')
            else:
                path = "/org/bluez/hci0/dev_" + mac.replace(":", "_")
                command = (
                    "busctl --system set-property org.bluez " + path
                    + ' org.bluez.Device1 Alias s "' + new + '"')
            threading.Thread(
                target=lambda: ssh_guest(
                    command, timeout=10, quiet=True),
                daemon=True).start()
            self.refresh()

        def cancel(_=None):
            if not done["v"]:
                done["v"] = True
                ed.destroy()

        ed.bind("<Return>", commit)
        ed.bind("<KP_Enter>", commit)
        ed.bind("<Escape>", cancel)
        ed.bind("<FocusOut>", commit)

    def blacklist(self):
        mac = self._sel_mac()
        if not mac or self._retry_lock("Blacklist"):
            return
        self.prefs["blacklist"].add(mac)
        save_bt_prefs(self.prefs)
        self._log(f"blacklisted {mac} — it won't show in scans.")
        self.refresh()

    def unblacklist(self):
        mac = self._sel_mac()
        if not mac:
            return
        self.prefs["blacklist"].discard(mac)
        save_bt_prefs(self.prefs)
        self._log(f"un-blacklisted {mac}.")
        self.refresh()


class App:
    def __init__(self, root):
        self.root = root
        root.title(APP_LABEL)
        root.geometry("1120x930")   # multi-target canvas; console still collapsed
        root.minsize(940, 680)
        root.configure(bg=BG)
        try:
            root.iconbitmap(ICON)
        except Exception:  # noqa: BLE001
            pass
        self.portal_proc = None
        self.audio_proc = None
        self._tray = None
        self._audio_logf = None
        self._portal_logf = None
        self._audio_lock = threading.Lock()  # serialize sender (re)launch so
        #                     overlapping _poll ticks never spawn two senders
        # ui() queue + its UI-thread pump MUST exist before any worker thread
        # can be spawned (console sink, BtPanel refresh, boot thread, _tick)
        self._uiq = queue.Queue()
        self._closing = False
        self._auto_conn_busy = False   # one auto-reconnect worker at a time
        self._auto_conn_last = 0.0     # last firing (cooldown anchor)
        self._auto_conn_cooldown = 90.0  # min seconds between auto firings
        self._auto_conn_fails = 0      # 3 failed rounds -> pause for session
        self._manual_bt_ops = 0        # in-flight manual BT actions
        self._bt_ops_lock = threading.Lock()
        self._broadcast_started = 0.0
        self._pair_lock = threading.Lock()  # atomic pair-commit vs cancel-clear
        # per-device pairing state, keyed by device id (created on demand)
        self._dev_states = {}
        self._dev_rows = {}
        self._dev_conn = {}
        self._dev_status = {}   # device id -> last daemon status dict
        self._vm_reachable = False   # the VM answers ssh (readiness truth)
        root.after(50, self._drain_ui)
        self._theme()

        # The whole UI lives inside self._full. The command console collapses to
        # keep the default window lean; it re-opens via the header toggle.
        self._console_open = False
        self._was_zoomed = False   # for the un-maximize width re-sync
        self._vol_ok = True
        self._vol_now = None
        self._vol_target = None
        full = tk.Frame(root, bg=BG)
        full.pack(fill="both", expand=True)
        self._full = full

        # ---- persistent console (right side, spans BOTH tabs) --------------
        # Packed first with side="right" so it owns a full-height strip; the
        # header/status/notebook then fill the remaining left cavity. Shows
        # every command the app runs and a big readiness banner up top.
        consf = tk.Frame(full, bg=PANEL, width=390)
        consf.pack_propagate(False)
        self._consf = consf   # collapsed by default; opened via the header toggle
        self._ready_state = None
        self._ipad_conn = None
        self.ready_lbl = tk.Label(consf, text="◌  Starting…", bg=PANEL,
                                  fg=MUTED, font=("Segoe UI Semibold", 13),
                                  anchor="w", padx=12, pady=12)
        self.ready_lbl.pack(fill="x")
        chead = tk.Frame(consf, bg=PANEL)
        chead.pack(fill="x", padx=10)
        tk.Label(chead, text="Console — every command the app runs", bg=PANEL,
                 fg=MUTED, font=("Segoe UI", 9, "bold")).pack(
            side="left", pady=(0, 4))
        ttk.Button(chead, text="Clear", width=6,
                   command=self._console_clear).pack(side="right")
        cwrap = tk.Frame(consf, bg=PANEL)
        cwrap.pack(fill="both", expand=True, padx=10, pady=(0, 12))
        self.console = tk.Text(cwrap, bg="#0b0d12", fg="#b7c0d4", bd=0,
                               font=("Consolas", 9), wrap="word",
                               state="disabled", insertbackground=FG)
        csb = ttk.Scrollbar(cwrap, command=self.console.yview)
        csb.pack(side="right", fill="y")
        self.console.config(yscrollcommand=csb.set)
        self.console.pack(side="left", fill="both", expand=True)
        self.console.tag_config("ts", foreground="#5b6172")
        self.console.tag_config("cmd", foreground="#6cc6ff")
        self.console.tag_config("ok", foreground=ACCENT)
        self.console.tag_config("err", foreground=DANGER)
        self.console.tag_config("bt", foreground=PORTAL)
        self.console.tag_config("event", foreground=FG)
        self.console.tag_config("info", foreground=MUTED)
        set_log_sink(self._log_sink)
        self.log("event", "OpenSpan started.")

        head = tk.Frame(full, bg=BG)
        # flush to the very top (frameless) + extra height = a full title-bar
        # drag band, not a thin strip. Whole band is bound to _drag_* below.
        head.pack(fill="x", padx=16, pady=(0, 4), ipady=7)
        self._cons_anchor = head   # the console packs before this when opened
        _t1 = tk.Label(head, text=APP_LABEL, bg=BG, fg=FG,
                       font=("Segoe UI Semibold", 18))
        _t1.pack(side="left")
        _t2 = tk.Label(head, text="PC → iPad + Mac bridge", bg=BG, fg=MUTED,
                       font=("Segoe UI", 10))
        _t2.pack(side="left", padx=(10, 0), pady=(8, 0))
        # window controls: the caption is stripped (frameless), so THIS row is
        # the title bar. Tk buttons -> commands on the Tk thread (R1-safe); the
        # drag is the SetWindowPos header binding below (callback-free).
        _cl = tk.Button(head, text="✕", command=self._confirm_close, bg=BG,
                        fg=MUTED, bd=0, relief="flat", width=3, cursor="hand2",
                        font=("Segoe UI", 12), activebackground=DANGER,
                        activeforeground="#ffffff")
        _cl.pack(side="right", padx=(6, 0))
        _cl.bind("<Enter>", lambda e: _cl.config(bg=DANGER, fg="#ffffff"))
        _cl.bind("<Leave>", lambda e: _cl.config(bg=BG, fg=MUTED))
        _mn = tk.Button(head, text="—", command=self._minimize, bg=BG, fg=MUTED,
                        bd=0, relief="flat", width=3, cursor="hand2",
                        font=("Segoe UI", 11), activebackground=PANEL,
                        activeforeground=FG)
        _mn.pack(side="right")
        _mn.bind("<Enter>", lambda e: _mn.config(bg=PANEL, fg=FG))
        _mn.bind("<Leave>", lambda e: _mn.config(bg=BG, fg=MUTED))
        ttk.Button(head, text="—  Minimize", command=self._to_tray).pack(
            side="right", padx=(0, 12))
        self._cons_btn = ttk.Button(head, text="▸  Console",
                                    command=self._toggle_console)
        self._cons_btn.pack(side="right", padx=(0, 8))
        # DRAG the window by the header. Native HTCAPTION doesn't fire here (Tk's
        # content window covers the subclassed frame), so we move it ourselves --
        # via a raw pure-move SetWindowPos (blit, no repaint) in _drag_move, which
        # is tear-free AND R1-safe (a plain Tk binding, no ctypes modal loop).
        for _w in (head, _t1, _t2):
            _w.bind("<ButtonPress-1>", self._drag_start)
            _w.bind("<B1-Motion>", self._drag_move)

        # per-indicator status row -- each token coloured by ITS OWN state
        # (green = live, grey = off/waiting), so the iPad token greys out when
        # it is not actually connected instead of always reading green.
        indrow = tk.Frame(full, bg=BG)
        indrow.pack(fill="x", padx=16, pady=(0, 1))
        self._ind = {}
        for _k in ("vm", "ipad", "mac", "portal", "audio", "bcast", "admin"):
            _lb = tk.Label(indrow, text="", bg=BG, fg=MUTED,
                           font=("Consolas", 10))
            _lb.pack(side="left", padx=(0, 14))
            self._ind[_k] = _lb
        # transient / call-to-action line (Broadcasting…, errors, hints)
        self.status = tk.StringVar(value="Checking…")
        tk.Label(full, textvariable=self.status, bg=BG, fg=ACCENT,
                 font=("Consolas", 10), anchor="w").pack(
            fill="x", padx=16, pady=(0, 6))

        # both panels side by side in one window (no tabs): iPad Bridge on
        # the left, Bluetooth & Headphones on the right, console far right
        main = tk.Frame(full, bg=BG)
        main.pack(fill="both", expand=True, padx=10, pady=4)
        bridge_col = tk.Frame(main, bg=BG)
        bridge_col.pack(side="left", fill="both", expand=True)
        tk.Label(bridge_col, text="Device Bridge", bg=BG, fg=FG,
                 font=("Segoe UI Semibold", 12)).pack(anchor="w",
                                                      padx=16, pady=(0, 2))
        bridge = tk.Frame(bridge_col, bg=BG)
        bridge.pack(fill="both", expand=True)
        tk.Frame(main, bg="#2d3444", width=1).pack(side="left", fill="y",
                                                   pady=6)
        bt_col = tk.Frame(main, bg=BG)
        bt_col.pack(side="left", fill="both", expand=True)
        tk.Label(bt_col, text="Bluetooth & Headphones", bg=BG, fg=FG,
                 font=("Segoe UI Semibold", 12)).pack(anchor="w",
                                                      padx=12, pady=(0, 2))
        self._build_audio_panel(bt_col)
        self.bt_panel = BtPanel(bt_col, app=self)
        self.bt_panel.pack(fill="both", expand=True)

        # arrangement — always visible (Bridge tab)
        arr_wrap = tk.Frame(bridge, bg=CARD, bd=0)
        arr_wrap.pack(fill="both", expand=True, padx=8, pady=6)
        tk.Label(arr_wrap, text="Drag any screen to match your desk. Sizes "
                                "come from each screen's real diagonal — set "
                                "them in “Screen sizes…”.",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 9)).pack(
            anchor="w", padx=8, pady=(6, 0))
        self.canvas = MultiArrangeCanvas(
            arr_wrap, on_change=self._portal_changed, height=270)

        # ---- arrangements -------------------------------------------------
        # A screen's resolution really does change -- the managed Mac's
        # landscape panel gets switched between 4K and 2K -- and every distance
        # on that screen changes with it. Rather than re-entering the whole desk
        # each time, keep a named copy of each arrangement and switch.
        #
        # Named INLINE, in the entry below. No pop-out asks for a name: a new
        # window lands on whichever monitor Windows picks, which on this desk is
        # rarely the one being looked at.
        prow = tk.Frame(arr_wrap, bg=CARD)
        prow.pack(fill="x", padx=8, pady=(6, 0))
        tk.Label(prow, text="Arrangement:", bg=CARD, fg=MUTED).pack(side="left")
        self.profile_name = tk.StringVar(
            value=str(self.canvas.config.get("profile", "") or "Current"))
        self.profile_pick = ttk.Combobox(
            prow, textvariable=self.profile_name, width=24,
            values=list_profiles(), state="normal")
        self.profile_pick.pack(side="left", padx=6)
        self.profile_pick.bind("<<ComboboxSelected>>", self._switch_profile)
        ttk.Button(prow, text="Save as", width=8,
                   command=self._save_profile).pack(side="left")
        ttk.Button(prow, text="Duplicate", width=10,
                   command=self._duplicate_profile).pack(side="left", padx=4)
        ttk.Button(prow, text="Delete", width=7,
                   command=self._delete_profile).pack(side="left")
        self.canvas.pack(fill="both", expand=True, padx=8, pady=8)

        row = tk.Frame(arr_wrap, bg=CARD)
        row.pack(fill="x", padx=8, pady=(0, 8))
        tk.Label(row, text="iPad:", bg=CARD, fg=MUTED).pack(side="left")
        self.model = tk.StringVar(value=list(IPAD_PRESETS)[0])
        cb = ttk.Combobox(row, textvariable=self.model, width=22,
                          values=list(IPAD_PRESETS), state="readonly")
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", self._pick_model)
        ttk.Button(row, text="Screen sizes…",
                   command=self._screen_sizes_dialog).pack(
            side="right", padx=(6, 0))
        ttk.Button(row, text="Rotate",
                   command=self.canvas.rotate).pack(side="left")
        ttk.Button(
            row, text="Configure Mac displays…",
            command=lambda: MacDisplayEditor(self.root, self.canvas)).pack(
                side="right")

        # Bridge controls. The four connection verbs live ONLY on each device's
        # own row in the Devices panel below -- there is deliberately no second
        # global copy of them here. A duplicate row kept its own separate
        # paired-state, so unpairing via one left the other still showing the
        # device as paired.
        ctl = tk.Frame(bridge, bg=BG)
        ctl.pack(fill="x", padx=16, pady=(2, 4))
        self.vm_btn = ttk.Button(ctl, text="Start VM", command=self.toggle_vm)
        self.vm_btn.grid(row=0, column=0, sticky="ew", padx=3, pady=3)
        self.portal_btn = ttk.Button(ctl, text="Start portal",
                                     command=self.toggle_portal)
        self.portal_btn.grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        ttk.Button(ctl, text="Edit keymap",
                   command=lambda: os.startfile(KEYMAP)).grid(
            row=0, column=2, sticky="ew", padx=3, pady=3)
        self.invert_scroll = tk.BooleanVar(
            value=bool(load_setting("scroll_invert", False)))
        ttk.Checkbutton(ctl, text="⇅  Invert scroll wheel",
                        variable=self.invert_scroll,
                        command=self._on_invert_scroll).grid(
            row=1, column=0, columnspan=3, sticky="w", padx=5, pady=(2, 3))
        self.cross_button = tk.BooleanVar(
            value=bool(self.canvas.config.get(
                "cross_requires_side_button", False)))
        ttk.Checkbutton(
            ctl, text="🖱  Hold a mouse side button to move between machines",
            variable=self.cross_button,
            command=self._on_cross_button).grid(
            row=2, column=0, columnspan=3, sticky="w", padx=5, pady=(0, 3))
        self.button_jumps = tk.BooleanVar(
            value=bool(self.canvas.config.get(
                "side_button_jumps_nearest", False)))
        ttk.Checkbutton(
            ctl, text="↦  …and jump straight to the nearest screen  "
                      "(recommended for complex arrangements)",
            variable=self.button_jumps,
            command=self._on_button_jumps).grid(
            row=3, column=0, columnspan=3, sticky="w", padx=(26, 5),
            pady=(0, 3))
        for c in range(3):
            ctl.columnconfigure(c, weight=1)

        # ---- Devices: one row per device, built from the config ------------
        # Nothing here is per-device-type. Every device the user has added gets
        # the identical four verbs against its own radio, port and bonds.
        self._dev_frame = ttk.LabelFrame(
            bridge, text="Devices", padding=7)
        self._dev_frame.pack(fill="x", padx=16, pady=(3, 2))
        self._dev_rows = {}
        self._dev_body = tk.Frame(self._dev_frame, bg=BG)
        self._dev_body.pack(fill="x")
        addrow = tk.Frame(self._dev_frame, bg=BG)
        addrow.pack(fill="x", pady=(6, 0))
        ttk.Button(addrow, text="＋  Add device",
                   command=self._add_device_dialog).pack(side="left")
        tk.Label(addrow,
                 text="Each device gets its own radio, its own advertisement "
                      "and its own bonds. No software is installed on it.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(
            side="left", padx=(10, 0))
        self._rebuild_device_rows()

        # ---- System control: every backend action, nothing hidden ----
        sysf = ttk.LabelFrame(bridge, text="System control", padding=8)
        sysf.pack(fill="x", padx=16, pady=(6, 2))
        self.sys_status = tk.StringVar(value="…")
        tk.Label(sysf, textvariable=self.sys_status, bg=BG, fg=MUTED,
                 font=("Consolas", 8), anchor="w", justify="left").pack(
            fill="x", pady=(0, 4))
        sg = tk.Frame(sysf, bg=BG)
        sg.pack(fill="x")
        sysbtns = [("Stop VM", self.stop_vm),
                   ("Cold-restart VM", self.cold_restart_vm),
                   ("Restart keyboard", self.restart_keyboard),
                   ("Restart audio", self.restart_audio_btn),
                   ("⏻ Shut down everything", self.shutdown_all)]
        for i, (label, fn) in enumerate(sysbtns):
            ttk.Button(sg, text=label, command=fn).grid(
                row=i // 3, column=i % 3, sticky="ew", padx=3, pady=3)
        for c in range(3):
            sg.columnconfigure(c, weight=1)

        # ---- Radio ownership mode (switched via a clean reboot) ----
        mode = ttk.LabelFrame(bridge, text="Bluetooth radio", padding=8)
        mode.pack(fill="x", padx=16, pady=(6, 2))
        self.mode_lbl = tk.Label(mode, bg=BG, fg=FG, font=("Segoe UI", 10),
                                 anchor="w")
        self.mode_lbl.pack(fill="x")
        tk.Label(mode, bg=BG, fg=MUTED, font=("Segoe UI", 8), anchor="w",
                 wraplength=480, justify="left",
                 text="Station = the app owns the radio (iPad bridge + "
                      "command station, near-bare-metal). Windows = native "
                      "Bluetooth + audio. Switching cleanly reboots the PC."
                 ).pack(fill="x", pady=(2, 6))
        mrow = tk.Frame(mode, bg=BG)
        mrow.pack(fill="x")
        self.to_station = ttk.Button(
            mrow, text="Switch to Station  (restart)",
            command=lambda: self.switch_mode("station"))
        self.to_station.pack(side="left", expand=True, fill="x", padx=2)
        self.to_windows = ttk.Button(
            mrow, text="Switch to Windows  (restart)",
            command=lambda: self.switch_mode("windows"))
        self.to_windows.pack(side="left", expand=True, fill="x", padx=2)
        self._refresh_mode_buttons()

        tk.Label(full, text="open source · MIT · nothing phones home",
                 bg=BG, fg="#5b6172", font=("Segoe UI", 8)).pack(
            side="bottom", pady=6)

        # clipboard relay for the iPad shortcuts (CLIPBOARD_DESIGN.md);
        # fail-soft: without it everything else works, and Ctrl+Alt+V
        # typing paste is unaffected
        try:
            tok, cport = clipboard_config()
            self.clip_server = ClipboardServer(tok, cport)
            if self.clip_server.start():
                _emit("event", "clipboard relay ready on "
                               f"http://{lan_ip()}:{cport}/clip "
                               "(token in openspan_settings.json)")
        except Exception:  # noqa: BLE001
            self.clip_server = None

        # only the app owns the radio in Station mode; never grab it in
        # Windows mode
        if current_mode() == "station" and not vm_running():
            threading.Thread(target=start_vm_clean, daemon=True).start()
        # keep the Windows->VM audio sender running whenever the app is open,
        # so connecting headphones is all it takes -- nothing else to launch
        self._ensure_audio()
        # push app-bundled guest scripts to the VM so a fix in the app also
        # updates the VM-side connection logic (no manual deploy, no reliance)
        self._sync_guest_scripts()
        # FRAMELESS, the crash-safe way. The earlier frameless treatment
        # subclassed GWLP_WNDPROC with a Python ctypes callback; heavy pointer
        # traffic (WM_NCHITTEST fires on every move) faulted it in _ctypes.pyd
        # (0xc0000005). _frameless_safe drops the caption with a ONE-SHOT style
        # strip -- NO callback, native window procedure kept -- so that fault
        # cannot recur. DWM dark paint sits cosmetically on top; the header row
        # above is the title bar.
        self.root.after(120, lambda: (self._frameless_safe(),
                                      _paint_dark_titlebar(self.root)))
        # Runtime tray creation is deliberately disabled. Its pure-ctypes
        # WNDPROC also access-violated during a live soak despite passing short
        # self-tests. Native taskbar minimize needs no Python Windows callback.
        # re-sync the window width to the console state when un-maximized (a
        # width change requested while zoomed is deferred, not lost)
        self.root.bind("<Configure>", self._on_configure)
        self._tick()

    # ---- Audio & status panel (always visible) + console toggle ----------
    def _build_audio_panel(self, parent):
        """Audio + at-a-glance status, always on screen — this is what the tray
        restores to and what the console-collapsed window shows. Readiness line,
        VM / iPad / audio / portal dots, the connected headphones, a volume
        slider (drives the Windows master volume, the same dial the sender's
        GAIN mirror follows), and an L/R balance slider (written to
        audio_balance.txt, applied per-channel inside the sender)."""
        p = ttk.LabelFrame(parent, text="Audio & status", padding=8)
        p.pack(fill="x", padx=12, pady=(0, 6))

        self.c_ready = tk.Label(p, text="◌  Starting…", bg=BG, fg=MUTED,
                                font=("Segoe UI Semibold", 11), anchor="w")
        self.c_ready.pack(fill="x")

        dots = tk.Frame(p, bg=BG)
        dots.pack(fill="x", pady=(4, 0))
        self.c_stat = {}
        for key, label in [("vm", "VM"), ("ipad", "iPad"),
                           ("mac", "Mac"),
                           ("audio", "Audio"), ("portal", "Portal")]:
            cell = tk.Frame(dots, bg=BG)
            cell.pack(side="left", padx=(0, 12))
            d = tk.Label(cell, text="●", bg=BG, fg=MUTED,
                         font=("Segoe UI", 11))
            d.pack(side="left")
            tk.Label(cell, text=label, bg=BG, fg=MUTED,
                     font=("Segoe UI", 9)).pack(side="left", padx=(4, 0))
            self.c_stat[key] = d

        self.c_buds = tk.Label(p, text="🎧  —", bg=BG, fg=MUTED,
                               font=("Segoe UI", 10), anchor="w")
        self.c_buds.pack(fill="x", pady=(8, 0))

        self._vol_drag = False
        self._vol_syncing = False
        vr = tk.Frame(p, bg=BG)
        vr.pack(fill="x", pady=(8, 0))
        tk.Label(vr, text="Volume", bg=BG, fg=MUTED, width=9, anchor="w",
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        self.c_vol_var = tk.DoubleVar(value=self._load_audio_gain() * 100.0)
        self.c_vol = ttk.Scale(vr, from_=0, to=100, variable=self.c_vol_var,
                               command=self._vol_changed)
        self.c_vol.pack(side="left", fill="x", expand=True)
        self.c_vol.bind("<ButtonPress-1>",
                        lambda e: setattr(self, "_vol_drag", True))
        self.c_vol.bind("<ButtonRelease-1>",
                        lambda e: setattr(self, "_vol_drag", False))
        # disabled later by _apply_poll if _volume_thread reports no pycaw

        br = tk.Frame(p, bg=BG)
        br.pack(fill="x", pady=(6, 0))
        tk.Label(br, text="L ↔ R", bg=BG, fg=MUTED, width=9, anchor="w",
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        self.c_bal_var = tk.DoubleVar(value=self._load_balance() * 100)
        self.c_bal = ttk.Scale(br, from_=-100, to=100, variable=self.c_bal_var,
                               command=self._bal_changed)
        self.c_bal.pack(side="left", fill="x", expand=True)
        self.c_bal.bind("<Double-1>", self._bal_center)
        tk.Label(p, text="double-click balance to center", bg=BG,
                 fg="#5b6172", font=("Segoe UI", 8), anchor="w").pack(
            fill="x", pady=(2, 0))

    def _toggle_console(self):
        """Show/hide the command console on the right. Collapsed by default so
        the window opens lean; the window width grows/shrinks to match."""
        if self._console_open:
            self._consf.pack_forget()
            self._console_open = False
            self._cons_btn.config(text="▸  Console")
            self._set_win_width(1120)
        else:
            self._consf.pack(side="right", fill="y", before=self._cons_anchor)
            self._console_open = True
            self._cons_btn.config(text="◂  Console")
            self._set_win_width(1520)

    def _set_win_width(self, w):
        """Resize the window to width w, keeping height and position. No-op
        while maximized (a zoomed window ignores geometry())."""
        try:
            if self.root.state() == "zoomed":
                return
            m = re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", self.root.geometry())
            if m:
                _, h, x, y = m.groups()
                self.root.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:  # noqa: BLE001
            pass

    def _on_configure(self, e):
        """Reconcile the window width with the console state when the window
        leaves the maximized state. A width change requested while zoomed is a
        no-op (Tk ignores geometry() when maximized), so re-apply it the moment
        the window un-maximizes — otherwise it restores to the pre-maximize
        width, which may not match the current console state."""
        if e.widget is not self.root:
            return  # ignore child-widget configure events
        z = (self.root.state() == "zoomed")
        if z == self._was_zoomed:
            return  # no zoomed<->normal transition; nothing to reconcile
        self._was_zoomed = z
        if not z:  # just un-maximized -> match the current console state
            self.root.after(10, lambda: self._set_win_width(
                1520 if self._console_open else 1120))

    def _vol_changed(self, _=None):
        if self._vol_syncing:
            return
        gain = max(0.0, min(1.0, self.c_vol_var.get() / 100.0))
        try:
            tmp = GAIN_FILE + ".new"
            with open(tmp, "w") as f:
                f.write(f"{gain:.4f}")
            os.replace(tmp, GAIN_FILE)
        except OSError:
            pass

    def _load_audio_gain(self):
        """Read the UI-owned gain without loading Core Audio/comtypes."""
        try:
            with open(GAIN_FILE) as f:
                gain = float(f.read().strip())
            if not math.isfinite(gain):
                return 1.0
            return max(0.0, min(1.0, gain))
        except (OSError, ValueError):
            return 1.0

    def _load_balance(self):
        import math
        try:
            with open(BAL_FILE) as f:
                b = float(f.read().strip())
            if not math.isfinite(b):
                return 0.0  # 'nan'/'inf' would hard-pan, not center
            return max(-1.0, min(1.0, b))
        except (OSError, ValueError):
            return 0.0

    def _bal_changed(self, _=None):
        b = round(self.c_bal_var.get()) / 100.0
        try:
            # atomic replace: the sender polls this file every 150ms, and a
            # truncate-then-write would hand it a torn/empty read — an
            # audible one-tick balance jump mid-drag
            tmp = BAL_FILE + ".new"
            with open(tmp, "w") as f:
                f.write(f"{b:+.2f}")
            os.replace(tmp, BAL_FILE)
        except OSError:
            pass  # reader mid-open on the target: next drag tick rewrites

    def _bal_center(self, _=None):
        self.c_bal_var.set(0.0)
        self._bal_changed()

    def _refresh_mode_buttons(self):
        m = current_mode()
        self.mode_lbl.config(
            text=("● Station — the app owns the radio" if m == "station"
                  else "● Windows — native Bluetooth & audio"),
            fg=(ACCENT if m == "station" else FG))
        self.to_station.state(["disabled"] if m == "station" else ["!disabled"])
        self.to_windows.state(["disabled"] if m == "windows" else ["!disabled"])

    def switch_mode(self, mode):
        nice = "Station" if mode == "station" else "Windows"
        if not dark_confirm(
                self.root, f"Switch to {nice} mode?",
                f"This restarts the PC and brings it back up in {nice} mode.\n\n"
                + ("The app will own the Bluetooth radio (iPad + command "
                   "station). Windows Bluetooth/audio will be unavailable."
                   if mode == "station" else
                   "Windows gets its Bluetooth radio back (headphones, etc.). "
                   "The iPad bridge will be offline until you switch back.")
                + "\n\nSave your work first. Restart now?"):
            return
        # station mode needs the boot task installed (one-time UAC)
        if mode == "station" and not ensure_boot_task():
            dark_alert(
                self.root, "Setup needed",
                "Couldn't install the startup task (admin was declined). "
                "Station mode won't auto-start after reboot until it's "
                "installed.")
        set_mode(mode)
        subprocess.run(["shutdown", "/r", "/t", "8", "/c",
                        f"OpenSpan switching to {nice} mode"],
                       creationflags=NO_WINDOW)
        self.status.set(f"Restarting into {nice} mode in ~8s…")

    def _theme(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("TButton", background=CARD, foreground=FG,
                     bordercolor=CARD, focuscolor=CARD, relief="flat",
                     padding=8, font=("Segoe UI", 10))
        st.map("TButton", background=[("active", "#2d3444")])
        st.configure("Accent.TButton", background=ACCENT_DIM,
                     foreground="#eafff3", font=("Segoe UI Semibold", 10))
        st.map("Accent.TButton", background=[("active", "#2a8f5c")])
        st.configure("Danger.TButton", background="#53292a",
                     foreground="#ffd9d6", font=("Segoe UI Semibold", 10))
        st.map("Danger.TButton", background=[("active", "#6e3335")])
        # sliders (compact mode's volume/balance)
        st.configure("Horizontal.TScale", background=BG, troughcolor=CARD,
                     bordercolor=CARD, lightcolor=ACCENT_DIM,
                     darkcolor=ACCENT_DIM)
        st.map("TButton",
               foreground=[("disabled", "#5b6172")],
               background=[("disabled", PANEL), ("active", "#2d3444")])
        # LabelFrame (the panel that was glaringly light)
        st.configure("TLabelframe", background=BG, bordercolor="#2d3444",
                     relief="solid", borderwidth=1)
        st.configure("TLabelframe.Label", background=BG, foreground=MUTED,
                     font=("Segoe UI", 9, "bold"))
        st.configure("TFrame", background=BG)
        st.configure("TCheckbutton", background=BG, foreground=FG)
        st.map("TCheckbutton", background=[("active", BG)])
        # Notebook tabs (dark)
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", background=PANEL, foreground=MUTED,
                     padding=(14, 7), borderwidth=0)
        st.map("TNotebook.Tab", background=[("selected", CARD)],
               foreground=[("selected", FG)])
        # Combobox + its drop-down list
        st.configure("TCombobox", fieldbackground=CARD, background=CARD,
                     foreground=FG, arrowcolor=FG, bordercolor="#2d3444",
                     selectbackground=CARD, selectforeground=FG)
        st.map("TCombobox", fieldbackground=[("readonly", CARD)],
               foreground=[("readonly", FG)])
        self.root.option_add("*TCombobox*Listbox.background", CARD)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_DIM)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#eafff3")
        # Treeview (the Bluetooth device list)
        st.configure("Treeview", background=CARD, foreground=FG,
                     fieldbackground=CARD, bordercolor=CARD, borderwidth=0,
                     rowheight=26, font=("Segoe UI", 10))
        st.configure("Treeview.Heading", background=PANEL, foreground=MUTED,
                     relief="flat", font=("Segoe UI", 9, "bold"))
        st.map("Treeview.Heading", background=[("active", "#2d3444")])
        st.map("Treeview", background=[("selected", ACCENT_DIM)],
               foreground=[("selected", "#eafff3")])

    def _minimize(self):
        # normal window -> native minimize to the taskbar; always restorable.
        try:
            self.root.iconify()
        except tk.TclError:
            pass

    def _drag_start(self, e):
        # Offset in Tk/screen space (same space as e.x_root). winfo_x stays live
        # -- our subclass forwards WM_WINDOWPOSCHANGED to Tk's proc -- so it's
        # correct even after prior raw-SetWindowPos drags. No Tk<->physical mixing.
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()
        # Resolve the top-level WRAPPER hwnd (the same handle _dark_titlebar
        # subclasses) ONCE per drag so _drag_move never re-resolves mid-flood.
        # argtypes are REQUIRED: without them ctypes truncates the 64-bit HWND.
        try:
            import ctypes
            import ctypes.wintypes as wt
            u = ctypes.windll.user32
            u.SetWindowPos.restype = wt.BOOL
            u.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_uint]
            u.GetAncestor.restype = wt.HWND
            u.GetAncestor.argtypes = [wt.HWND, ctypes.c_uint]
            self._drag_u = u
            hwnd = u.GetAncestor(self.root.winfo_id(), 2) or self.root.winfo_id()
            # Never raw-move a maximized/snapped window: a size-free move keeps it
            # WS_MAXIMIZE while displacing it (broken inset, no restore). Rare --
            # no maximize affordance -- so fall back to Tk geometry() (below),
            # which un-maximizes and moves correctly. hwnd=None selects that path.
            self._drag_hwnd = None if u.IsZoomed(hwnd) else hwnd
        except Exception:  # noqa: BLE001 -- any ctypes failure -> Tk geometry drag
            self._drag_hwnd = None

    def _drag_move(self, e):
        x, y = e.x_root - self._dx, e.y_root - self._dy
        h = getattr(self, "_drag_hwnd", None)
        if h:
            # Pure MOVE: SWP_NOSIZE|NOZORDER|NOACTIVATE = 0x0015. A size-free move
            # does NO client invalidation -- Windows/DWM blits the existing window
            # pixels to the new spot -- so no WM_PAINT is queued and there is
            # nothing for the <B1-Motion> flood to starve => tear-free. NOT
            # SWP_NOREDRAW: the default copy-bits blit IS the fast path. R1-safe:
            # a synchronous Win32 call from a normal Tk callback on the main thread
            # -- NOT inside a WNDPROC, and it starts NO modal move loop (returns at
            # once), unlike the old ReleaseCapture+WM_NCLBUTTONDOWN(HTCAPTION) that
            # crashed. Tk's position cache stays live via the subclass forwarding
            # WM_WINDOWPOSCHANGED, so no release resync (and its repaint flash).
            self._drag_u.SetWindowPos(h, 0, x, y, 0, 0, 0x0015)
        else:
            # Fallback (maximized, or ctypes unavailable): the original Tk move.
            self.root.geometry(f"+{x}+{y}")

    def _frameless_safe(self):
        """Frameless the CRASH-SAFE way: a ONE-SHOT Win32 style strip that drops
        WS_CAPTION -- NO ctypes WNDPROC subclass, so the _ctypes.pyd 0xc0000005
        the old WM_NCCALCSIZE/HTCAPTION callback hit under heavy pointer traffic
        cannot recur (there is no callback to fault). WS_THICKFRAME stays, so the
        window keeps a native resize border, minimize/maximize, snap and taskbar.
        The header row is the title bar; the drag is the SetWindowPos header
        binding (also callback-free). Wrapped so any failure just leaves the
        native caption -- never a crash."""
        try:
            import ctypes
            import ctypes.wintypes as wt
            u = ctypes.windll.user32
            u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            u.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
            u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
            u.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int,
                                            ctypes.c_ssize_t]
            hwnd = u.GetAncestor(self.root.winfo_id(), 2) or self.root.winfo_id()
            GWL_STYLE, WS_CAPTION = -16, 0x00C00000  # WS_BORDER | WS_DLGFRAME
            style = u.GetWindowLongPtrW(hwnd, GWL_STYLE)
            u.SetWindowLongPtrW(hwnd, GWL_STYLE, style & ~WS_CAPTION)
            # FRAMECHANGED|NOSIZE|NOMOVE|NOZORDER so the strip takes effect in place
            u.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)
        except Exception:  # noqa: BLE001 -- fall back to the native caption
            pass

    def _dark_titlebar(self):
        """[SUPERSEDED by _frameless_safe -- NOT called. Kept for reference only.]
        Fully FRAMELESS, the SAFE way (rule R1). NO overrideredirect, NO style
        flip, NO SendMessage modal drag -- every one of those crashed. The window
        stays a NORMAL top-level (native taskbar / minimize / maximize / snap /
        Alt-Tab intact). We subclass its window-proc only to (a) drop the caption
        via WM_NCCALCSIZE and (b) drive drag+resize via WM_NCHITTEST -> HTCAPTION
        / HTLEFT / ... . Windows performs the move/resize itself, so there is NO
        modal loop in our process -> no reentrancy. The proc does PURE Win32 math
        and NEVER touches Tk (R1). The header row above is the title bar."""
        import ctypes
        import ctypes.wintypes as wt
        try:
            u = ctypes.windll.user32
            self.root.update_idletasks()
            hwnd = u.GetAncestor(self.root.winfo_id(), 2) or self.root.winfo_id()
            u.CallWindowProcW.restype = ctypes.c_ssize_t
            u.CallWindowProcW.argtypes = [ctypes.c_void_p, wt.HWND,
                                          ctypes.c_uint, wt.WPARAM, wt.LPARAM]
            u.SetWindowLongPtrW.restype = ctypes.c_void_p
            u.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int,
                                            ctypes.c_void_p]

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
            WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND,
                                         ctypes.c_uint, wt.WPARAM, wt.LPARAM)
            old = [0]
            B, HDR, BTN = 6, 46, 340  # resize grip / header height / button strip

            def proc(h, msg, wp, lp):
                # R1: ctypes callback INSIDE Windows message dispatch -- PURE
                # Win32 math only, NEVER a Tk call.
                try:
                    if msg == 0x0083 and wp:      # WM_NCCALCSIZE, wParam TRUE
                        if u.IsZoomed(h):         # maximized: stay off the taskbar
                            p = ctypes.cast(lp, ctypes.POINTER(RECT)).contents
                            fx = u.GetSystemMetrics(32) + u.GetSystemMetrics(92)
                            fy = u.GetSystemMetrics(33) + u.GetSystemMetrics(92)
                            p.left += fx; p.top += fy
                            p.right -= fx; p.bottom -= fy
                        return 0                  # else client = whole window
                    if msg == 0x0084:             # WM_NCHITTEST
                        x = ctypes.c_short(lp & 0xFFFF).value
                        y = ctypes.c_short((lp >> 16) & 0xFFFF).value
                        rc = RECT(); u.GetWindowRect(h, ctypes.byref(rc))
                        rx, ry = x - rc.left, y - rc.top
                        w, ht = rc.right - rc.left, rc.bottom - rc.top
                        lf, rg = rx < B, rx > w - B
                        tp, bt = ry < B, ry > ht - B
                        if tp and lf: return 13
                        if tp and rg: return 14
                        if bt and lf: return 16
                        if bt and rg: return 17
                        if lf: return 10
                        if rg: return 11
                        if tp: return 12
                        if bt: return 15
                        if ry < HDR and rx < w - BTN:  # header, left of buttons
                            return 2              # HTCAPTION -> native drag
                        return 1                  # HTCLIENT -> Tk handles it
                except Exception:  # noqa: BLE001
                    pass
                return u.CallWindowProcW(old[0], h, msg, wp, lp)

            self._fl_proc = WNDPROC(proc)   # keep the thunk alive for app life
            old[0] = u.SetWindowLongPtrW(hwnd, -4, self._fl_proc)  # GWLP_WNDPROC
            u.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x27)  # FRAMECHANGED|NOSIZE|MOVE|ZORDER
            try:  # rounded corners (Win11; harmless no-op on Win10)
                pref = ctypes.c_int(2)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass

    def _refresh_profiles(self, select=None):
        self.profile_pick["values"] = list_profiles()
        if select is not None:
            self.profile_name.set(select)

    def _save_profile(self):
        """Name the desk as it stands now, and start using that name.

        There is no plain "save": whatever is on the canvas is already stored,
        and stored into the selected arrangement if there is one. This is how an
        unnamed desk gets its first name, and how a copy gets a chosen one
        instead of the automatic name Duplicate gives it.
        """
        typed = self.profile_name.get().strip()
        if not typed:
            _emit("err", "type a name for this arrangement first.")
            return
        name = profile_name(typed)
        active = str(self.canvas.config.get("profile") or "")
        if name != typed:
            _emit("event", f"saved as “{name}” — an arrangement is named after "
                           f"its file, so punctuation becomes “_”.")
        if name in list_profiles() and name != active and not dark_confirm(
                self.root, "Replace that arrangement?",
                f"“{name}” already exists. Replace it with the desk as it is "
                f"now? The one it is replacing cannot be recovered."):
            return
        self.canvas.config["profile"] = save_profile(self.canvas.config, name)
        self.canvas.save()
        self._refresh_profiles(name)
        _emit("ok", f"this desk is now the “{name}” arrangement.")

    def _duplicate_profile(self):
        """Copy this arrangement under a new name and switch to it.

        The copy is what gets edited -- resolutions, sizes, positions -- so the
        one being used stays exactly as it is until the new one is chosen."""
        base = profile_name(self.profile_name.get().strip() or "Arrangement")
        taken = set(list_profiles())
        index = 2
        name = f"{base} {index}"
        while name in taken:
            index += 1
            name = f"{base} {index}"
        self.canvas.config["profile"] = save_profile(self.canvas.config, name)
        self.canvas.save()
        self._refresh_profiles(name)
        _emit("ok", f"duplicated as “{name}” — this is the one being edited "
                    f"now; “{base}” keeps the screens it had.")

    def _switch_profile(self, _event=None):
        name = profile_name(self.profile_name.get().strip())
        if name not in list_profiles():
            return          # a name being typed for Save as, not a selection
        if name == str(self.canvas.config.get("profile") or ""):
            return
        try:
            loaded = load_profile(name, self.canvas.config)
        except (OSError, ValueError) as exc:
            _emit("err", f"could not load “{name}”: {exc}")
            return
        self.canvas.adopt(loaded)
        # An arrangement now carries the whole-desk settings with it, so a
        # switch can change them -- and these two checkboxes were built once and
        # never looked at the config again. A tick that no longer matches what
        # the portal was told is worse than no tick at all: it is the UI
        # answering a question it has stopped being able to answer.
        self.cross_button.set(
            bool(self.canvas.config.get("cross_requires_side_button", False)))
        self.button_jumps.set(
            bool(self.canvas.config.get("side_button_jumps_nearest", False)))
        self.canvas.redraw()
        self.canvas.save()          # persists AND reloads the portal
        self._rebuild_device_rows()
        ensure_device_forwards(self.canvas.config)
        _emit("ok", f"arrangement “{name}” is now in use.")

    def _delete_profile(self):
        name = profile_name(self.profile_name.get().strip())
        if name not in list_profiles():
            return
        if not dark_confirm(self.root, "Delete arrangement?",
                            f"Delete the saved arrangement “{name}”? The "
                            f"screens in use right now do not change."):
            return
        delete_profile(name)
        active = str(self.canvas.config.get("profile") or "")
        if name == active:
            # the desk itself does not change -- it just stops having a name,
            # which is what stops _persist writing it back to a deleted file
            self.canvas.config.pop("profile", None)
            self.canvas.save()
            active = "Current"
        # deleting an arrangement that is NOT in use must not relabel the one
        # that is -- the box has to keep naming what is actually on the canvas
        self._refresh_profiles(active)
        _emit("event", f"arrangement “{name}” deleted. The screens in use did "
                       f"not change.")

    def _on_button_jumps(self):
        """While a side button is held, ignore adjacency and go to the nearest."""
        on = bool(self.button_jumps.get())
        self.canvas.config["side_button_jumps_nearest"] = on
        self.canvas.save()
        _emit("event",
              "with a side button held, the pointer now goes to the NEAREST "
              "screen that way, whatever the layout says is adjacent."
              if on else
              "crossings follow the arrangement again.")

    def _on_cross_button(self):
        """Require an explicit hold before control leaves this machine."""
        on = bool(self.cross_button.get())
        self.canvas.config["cross_requires_side_button"] = on
        self.canvas.save()          # reloads the portal; see save()
        _emit("event",
              "crossing now needs a mouse side button held down."
              if on else
              "crossing no longer needs a side button — a deliberate push is "
              "enough.")

    def _on_invert_scroll(self):
        on = bool(self.invert_scroll.get())
        save_setting("scroll_invert", on)
        _emit("event", f"scroll wheel {'INVERTED' if on else 'normal'} — "
                       "applies live, no restart.")

    def _manual_bt_begin(self):
        """Manual BT actions (Connect/Disconnect/Forget/Scan) register here
        so auto-reconnect defers to them instead of contending for the
        radio. Manual is never blocked by auto — only the reverse."""
        with self._bt_ops_lock:
            self._manual_bt_ops += 1

    def _manual_bt_end(self):
        with self._bt_ops_lock:
            self._manual_bt_ops = max(0, self._manual_bt_ops - 1)

    def ui(self, fn):
        """Run fn on the Tk main thread. Worker threads must NEVER touch Tk
        directly — not even after(): a background after() racing the UI
        thread hard-crashes the interpreter (PyEval_RestoreThread GIL abort,
        reproduced in the render harness). Closures go on a plain queue that
        the UI thread drains every 50ms; queue.put is unconditionally safe."""
        self._uiq.put(fn)

    def _drain_ui(self):
        """UI-thread pump for ui(): run queued closures, reschedule."""
        try:
            while True:
                try:
                    fn = self._uiq.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    pass  # e.g. a widget destroyed during shutdown
        finally:
            if not self._closing:
                try:
                    self.root.after(50, self._drain_ui)
                except Exception:  # noqa: BLE001
                    pass  # root gone -> the pump simply ends

    # ---- console ----
    def _log_sink(self, kind, text):
        """Thread-safe entry point for module-level _emit: queue to the UI."""
        self.ui(lambda: self.log(kind, text))

    def log(self, kind, text):
        """Append a timestamped, color-tagged line to the console (UI thread)."""
        try:
            c = self.console
            c.config(state="normal")
            c.insert("end", time.strftime("%H:%M:%S "), "ts")
            c.insert("end", text.rstrip() + "\n", kind)
            if int(c.index("end-1c").split(".")[0]) > 800:  # cap growth
                c.delete("1.0", "200.0")
            c.see("end")
            c.config(state="disabled")
        except Exception:  # noqa: BLE001
            pass

    def _console_clear(self):
        try:
            self.console.config(state="normal")
            self.console.delete("1.0", "end")
            self.console.config(state="disabled")
        except Exception:  # noqa: BLE001
            pass

    # ---- actions ----
    def restart_everything(self, log=None):
        """Restart ONLY the audio pipeline: the PipeWire/WirePlumber services in
        the VM plus the Windows sender. Deliberately does NOT touch the VM or the
        keyboard daemon (openspanble) -- audio and the iPad keyboard are
        independent, so restarting audio must never drop the keyboard."""
        def say(m):
            try:
                if log:
                    log(m)
            except Exception:  # noqa: BLE001
                pass
            self.ui(lambda: self.status.set(m))

        def work():
            say("restarting the audio pipeline (keyboard untouched)…")
            # audio-only: these never touch bluetoothd/the radio/openspanble
            ssh_guest("systemctl restart openspan-wireplumber "
                      "openspan-pipewire-pulse openspan-udprecv", timeout=45)
            try:
                if self.audio_proc and self.audio_proc.poll() is None:
                    _terminate_role_process(self.audio_proc)
            except Exception:  # noqa: BLE001
                pass
            self.audio_proc = None
            self._ensure_audio()
            say("audio restarted — wake your headphones to reconnect. "
                "Keyboard was not touched.")
        threading.Thread(target=work, daemon=True).start()

    def _auto_reconnect_audio(self, reason):
        """Autonomously (re)connect the last-used earbuds when they are
        bonded but idle. Fired on the READY edge (the buds page the adapter
        during the ~90s boot, give up, and never retry) and after an iPad
        pairing (the LE burst can knock A2DP off the shared antenna).

        STRUCTURALLY unable to scan or pair: it never calls bt-connect.sh
        (whose unpaired branch scans+pairs) — it runs a connect-ONLY command
        that re-verifies the bond guest-side in the same shell and does
        nothing on any doubt. Targets only devices whose BlueZ Icon says
        audio. Defers to any in-flight manual BT action, fires at most once
        per cooldown window, and pauses for the session after 3 failed
        rounds so it can never sit there paging powered-off buds forever."""
        now = time.time()
        if (self._auto_conn_busy or self._manual_bt_ops > 0
                or self._any_device_busy()
                or now - self._auto_conn_last < self._auto_conn_cooldown
                or self._auto_conn_fails >= 3):
            return
        self._auto_conn_busy = True
        self._auto_conn_last = now

        def work():
            attempted = False
            ok = False
            try:
                r = ssh_guest("cat /opt/openspan/audio-device.txt 2>/dev/null",
                              timeout=8, quiet=True)
                pin = (r.stdout or "").strip().upper()
                pin_parts = pin.split("|", 1)
                pin_controller = (
                    pin_parts[0] if len(pin_parts) == 2 else "")
                mac = pin_parts[-1]
                if not re.match(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$", mac):
                    return  # no known last device -> nothing to do
                prefs = load_bt_prefs()
                if mac in prefs["blacklist"]:
                    return
                name, paired, conn, icon = mac, False, False, ""
                if multi_radio_enabled(prefs):
                    r = ssh_guest(
                        "python3 /opt/openspan/openspan_bt.py list",
                        timeout=25, quiet=True)
                    try:
                        devices = json.loads(
                            r.stdout or "{}").get("devices", [])
                    except (TypeError, ValueError):
                        devices = []
                    for item in devices:
                        if str(item.get("address", "")).upper() != mac:
                            continue
                        if pin_controller and str(
                                item.get("controller", "")).upper() \
                                != pin_controller:
                            continue
                        name = prefs["renames"].get(
                            mac, item.get("alias") or item.get("name") or mac)
                        paired = bool(item.get("paired"))
                        conn = bool(item.get("connected"))
                        icon = str(item.get("icon", ""))
                        break
                else:
                    r = ssh_guest(
                        "bash /opt/openspan/bt-list.sh", timeout=25,
                        quiet=True)
                    for line in (r.stdout or "").splitlines():
                        p = line.split("|")
                        if len(p) >= 4 and p[0].upper() == mac:
                            name = prefs["renames"].get(p[0], p[1])
                            paired, conn = p[2] == "1", p[3] == "1"
                            icon = p[4] if len(p) > 4 else ""
                            break
                if "audio" not in (icon or ""):
                    return  # never auto-touch anything that isn't audio
                if conn or not paired:
                    return  # already connected, or not bonded (never
                    #         auto-pair -- pairing needs the user's intent)
                _emit("event", f"{reason} — reconnecting “{name}” "
                               "automatically…")
                # connect-only, bond re-verified in the SAME shell: empty or
                # doubtful info -> NOT_BONDED -> we do NOTHING (fail-closed)
                if multi_radio_enabled(prefs):
                    cmd = (
                        "python3 /opt/openspan/openspan_bt.py "
                        "reconnect-audio")
                else:
                    cmd = (f'info=$(bluetoothctl info {mac} 2>/dev/null); '
                           f'echo "$info" | grep -q "Paired: yes" '
                           '|| { echo NOT_BONDED; exit 0; }; '
                           f'echo "$info" | grep -q "Connected: yes" '
                           '&& { echo CONNECTED; exit 0; }; '
                           f'bluetoothctl connect {mac} >/dev/null 2>&1; '
                           'sleep 3; '
                           f'bluetoothctl info {mac} 2>/dev/null '
                           '| grep -q "Connected: yes" '
                           '&& echo CONNECTED || echo NO_LINK')
                attempted = True
                for attempt in (1, 2):
                    r = ssh_guest(cmd, timeout=25, quiet=True)
                    tok = ((r.stdout or "").strip().splitlines() or [""])[-1]
                    if "CONNECTED" in tok:
                        ok = True
                        _emit("event", f"auto-reconnect: “{name}” connected ✓")
                        break
                    if "NOT_BONDED" in tok:
                        break  # bond gone/unreadable -> hands off, no retry
                    if attempt == 1:
                        threading.Event().wait(4)  # buds may need a moment
                if attempted and not ok:
                    more = ("  (auto-reconnect is pausing for this session)"
                            if self._auto_conn_fails + 1 >= 3 else "")
                    _emit("event", f"auto-reconnect: “{name}” didn't respond "
                                   f"— wake the buds, then click Connect.{more}")
                self.ui(self.bt_panel.refresh)
            finally:
                if attempted:
                    self._auto_conn_fails = 0 if ok \
                        else self._auto_conn_fails + 1
                self._auto_conn_busy = False
        threading.Thread(target=work, daemon=True).start()

    # ---- System control (full manual control, nothing hidden) ----
    def stop_vm(self):
        if not dark_confirm(
                self.root, "Stop VM?",
                "Power off the audio/keyboard VM. Audio and the iPad keyboard "
                "stop until you start it again.\n\nStop now?"):
            return
        self.status.set("Stopping VM…")

        def work():
            ssh_guest("journalctl --sync; sync", timeout=12, quiet=True)
            vbox("controlvm", VM, "poweroff")
        threading.Thread(target=work, daemon=True).start()

    def cold_restart_vm(self):
        if not dark_confirm(
                self.root, "Cold-restart VM?",
                "Power-cycle the whole VM (~90s). Audio + keyboard come back "
                "fresh; you re-pair the keyboard on the iPad.\n\nRestart now?"):
            return
        self.status.set("Cold-restarting VM…")
        def work():
            if vm_running():
                ssh_guest("journalctl --sync; sync", timeout=12, quiet=True)
                vbox("controlvm", VM, "poweroff")
                for _ in range(30):
                    if not vm_running():
                        break
                    threading.Event().wait(1)
            start_vm_clean()
        threading.Thread(target=work, daemon=True).start()

    def restart_keyboard(self):
        self.status.set("Restarting keyboard daemon…")
        def work():
            ssh_guest("systemctl restart openspanble", timeout=25)
            self.ui(lambda: self.status.set(
                "Keyboard restarted — forget + re-pair on the iPad."))
        threading.Thread(target=work, daemon=True).start()

    def restart_audio_btn(self):
        self.restart_everything()

    def shutdown_all(self):
        if not dark_confirm(
                self.root, "Shut down everything?",
                "Power off the VM and close the app. Audio, keyboard, portal, "
                "and sender all stop — nothing keeps running.\n\nShut down "
                "now?"):
            return
        self._full_stop()

    # ---- close / tray ----
    def _full_stop(self):
        """The FULL STOP: portal, audio sender, and the VM all go down, then
        the app closes — nothing lingers, next launch is a clean cold boot."""
        self._closing = True  # stop the ui() pump rescheduling past destroy
        if getattr(self, "clip_server", None):
            self.clip_server.stop()  # clipboard offline before teardown
        # best-effort: flush the guest journal to disk before the hard power
        # cut, so the last minutes of Bluetooth events survive for post-mortem
        ssh_guest("journalctl --sync; sync", timeout=12, quiet=True)
        if self._tray:
            self._tray.destroy()
            self._tray = None
        for p in (self.portal_proc, self.audio_proc):
            try:
                if p and p.poll() is None:
                    _terminate_role_process(p)
            except Exception:  # noqa: BLE001
                pass
        try:
            if vm_running():
                vbox("controlvm", VM, "poweroff")
        except Exception:  # noqa: BLE001
            pass
        # the single-instance mutex is NOT closed here on purpose: the OS
        # releases it at process exit (even on a crash), and closing the raw
        # handle early would let a second instance start during shutdown
        self.root.after(400, self.root.destroy)

    def _confirm_close(self):
        """X handler. The recommended close is KEEP THE BRIDGE WARM (minimized):
        a full shutdown powers off the VM, and a fresh VM makes the bonded
        iPad reconnect to 'connected but no input' until it is re-paired. So
        minimize is the default and shutdown is the deliberate, warned choice.
        Shown as an in-frame overlay; the re-entrancy guard handles a second
        X while it's open."""
        choice = _dialog(
            self.root, "Keep OpenSpan running?",
            "Minimize it and the bridge stays warm — the iPad keeps "
            "working and reconnects with NO re-pairing.\n\nA full shut down "
            "powers off the VM. Next launch the iPad reconnects but won't "
            "accept input until you re-pair it (the ↻ Re-pair / reset "
            "button). Only shut down if you're done for a while.",
            [("—  Minimize  (keeps the iPad working)", "tray", "TButton"),
             ("⏻  Shut down — needs a re-pair next time", "shutdown",
              "Danger.TButton"),
             ("Cancel", "cancel", "TButton")])
        if choice == "tray":
            self._to_tray()
        elif choice == "shutdown":
            self._full_stop()

    def _ensure_tray(self):
        """Compatibility no-op: runtime tray callbacks are deliberately off."""
        return None

    def _to_tray(self):
        """Minimize to the native taskbar while every bridge role stays live."""
        self.root.iconify()
        _emit("event", "minimized — everything keeps running.")

    def _from_tray(self):
        # arrives on the Tk thread (the tray window shares this thread's pump);
        # marshal anyway. The tray icon is PERSISTENT -- not destroyed here.
        def show():
            self.root.deiconify()
            self.root.lift()
            try:
                self.root.focus_force()
            except tk.TclError:
                pass
        self.ui(show)

    def _show_tray_menu(self):
        # Called from the tray's ctypes WndProc (inside Windows message
        # dispatch). Do NOT touch Tk here: tk_popup runs a NESTED Tk event loop,
        # and running it reentrantly inside message dispatch is the ucrtbase
        # 0xC0000409 fail-fast we kept hitting (same signature I wrongly blamed
        # on the frameless). Defer the whole menu onto the UI queue, which the Tk
        # thread drains OUTSIDE any reentrant context -- the proven-safe path.
        self.ui(self._post_tray_menu)

    def _post_tray_menu(self):
        """Build + post the tray menu -- runs on the Tk thread via self.ui, never
        reentrantly. State comes from the last poll so nothing blocks. Dialog
        actions bypass their confirm or raise the window (never pop into hiding)."""
        try:
            c = getattr(self, "_cache", {})
            run = bool(c.get("running"))
            m = tk.Menu(self.root, tearoff=0, bg=CARD, fg=FG, bd=0,
                        activebackground=ACCENT_DIM, activeforeground="#eafff3",
                        font=("Segoe UI", 10))
            m.add_command(label=f"Open {APP_LABEL}", command=self._from_tray)
            m.add_separator()
            # One Connect/Disconnect per DEVICE, driven by the same per-device
            # verbs and the same per-device state the Devices panel uses -- so
            # the tray can never disagree with the window.
            for _d in (self.canvas.devices() if run else []):
                _id, _label = _d["id"], _d.get("name", _d["id"])
                _s = self._dev_state(_id)
                _liveN = bool(
                    (self._dev_status.get(_id) or {}).get("kbd_subscribed"))
                _busy = _s["inflight"] or _s["broadcasting"]
                m.add_command(
                    label=f"Connect {_label}",
                    command=lambda i=_id: self._connect_device(i),
                    state=("normal" if (_s["paired"] and not _liveN
                                        and not _busy) else "disabled"))
                m.add_command(
                    label=f"Disconnect {_label}",
                    command=lambda i=_id: self._disconnect_device(i),
                    state=("normal" if (_liveN or _busy) else "disabled"))
            m.add_command(
                label=("■  Stop portal" if c.get("on") else "▶  Start portal"),
                command=self.toggle_portal,
                state=("normal" if run else "disabled"))
            m.add_command(label="🎧  Reconnect headphones",
                          command=lambda: self._auto_reconnect_audio(
                              "tray reconnect"),
                          state=("normal" if run else "disabled"))
            m.add_separator()
            m.add_command(
                label="⏻  Shut down everything",
                command=lambda: (self._from_tray(),
                                 self.root.after(250, self._confirm_close)))
            x, y = self.root.winfo_pointerxy()
            try:
                m.tk_popup(x, y)
            finally:
                m.grab_release()
        except Exception:  # noqa: BLE001
            pass

    def _pick_model(self, *_):
        w, h = IPAD_PRESETS[self.model.get()]
        self.canvas.set_ipad_size(w, h)

    def _portal_changed(self, ok):
        if not ok:
            self.status.set(
                "⚠ No managed device touches a PC monitor — no portal")
        if self.portal_proc and self.portal_proc.poll() is None:
            # The portal reads geometry and input settings once at process
            # start. Apply a drag, resize, rotation, resolution, sensitivity or
            # acceleration edit immediately.
            _terminate_role_process(self.portal_proc)
            self.portal_proc = None
            self._start_portal_process()
            self.log("event", "portal geometry reloaded from the arrangement.")

    def toggle_vm(self):
        if vm_running():
            if dark_confirm(self.root, "Stop VM?",
                            "Stop the bridge VM? The iPad will disconnect "
                            "until you start it again."):
                self.vm_btn.config(text="Stopping VM…")
                vbox("controlvm", VM, "acpipowerbutton")
        else:
            self.vm_btn.config(text="Starting VM…")  # immediate feedback
            threading.Thread(target=start_vm_clean, daemon=True).start()

    def toggle_portal(self):
        if self.portal_proc and self.portal_proc.poll() is None:
            _terminate_role_process(self.portal_proc)
            self.portal_proc = None
            self.portal_btn.config(text="Start portal")  # immediate feedback
            self.log("event", "portal STOPPED — keyboard/mouse no longer "
                              "bridging to managed devices.")
        else:
            self._start_portal_process()
            self.log("event", "portal STARTED — keyboard/mouse now bridging "
                              "to the iPad and managed Mac.")

    def _start_portal_process(self):
        try:
            if self._portal_logf:
                self._portal_logf.close()
        except OSError:
            pass
        self._portal_logf = open(LOG, "a", buffering=1)
        self.portal_proc = subprocess.Popen(
            PORTAL_CMD,
            stdout=self._portal_logf, stderr=self._portal_logf,
            creationflags=NO_WINDOW, env=_independent_frozen_env())
        self.portal_btn.config(text="Stop portal")

    def _ensure_audio(self):
        """(Re)start the Windows->VM audio sender if it isn't running. Captures
        the default output via WASAPI loopback and streams it to the VM, which
        plays it to the connected Bluetooth headphones. Called on launch and on
        every status tick, so it self-heals if the sender ever dies."""
        with self._audio_lock:
            try:
                if self._closing:
                    return  # a respawned sender would outlive the app and
                    #         hold the OpenSpanAudioSender mutex hostage
                if self.audio_proc and self.audio_proc.poll() is None:
                    return
                try:
                    if self._audio_logf:
                        self._audio_logf.close()
                except OSError:
                    pass
                self._audio_logf = open(AUDIO_LOG, "a", buffering=1)
                self.audio_proc = subprocess.Popen(
                    AUDIO_CMD, stdout=self._audio_logf,
                    stderr=self._audio_logf, creationflags=NO_WINDOW,
                    env=_independent_frozen_env())
            except Exception:  # noqa: BLE001
                pass

    def _sync_guest_scripts(self):
        """Deploy app-bundled guest scripts to the VM once it's reachable, so a
        fix in the app also fixes the VM-side logic -- no manual deploy needed.
        Content is streamed over ssh stdin, so there's no shell-escaping of the
        script body."""
        # udp_to_sink.py loads on the next openspan-udprecv restart (the
        # "⟳ Restart audio" button); btready.sh runs at the next VM boot.
        # Syncing never disturbs anything already running.
        jobs = [("guest-bt-connect.sh", "/opt/openspan/bt-connect.sh"),
                (os.path.join("..", "guest", "udp_to_sink.py"),
                 "/opt/openspan/udp_to_sink.py"),
                (os.path.join("..", "guest", "btready.sh"),
                 "/opt/openspan/btready.sh"),
                (os.path.join("..", "guest", "openspan_bt.py"),
                 "/opt/openspan/openspan_bt.py"),
                (os.path.join("..", "guest", "set-hid-device.sh"),
                 "/opt/openspan/set-hid-device.sh"),
                (os.path.join("..", "guest", "bt-preflight.sh"),
                 "/opt/openspan/bt-preflight.sh"),
                # the per-device systemd TEMPLATE unit: without it every
                # openspanble@<id> enable/restart fails and the lane can't pair
                (os.path.join("..", "guest", "system", "openspanble@.service"),
                 "/etc/systemd/system/openspanble@.service"),
                (os.path.join("..", "guest", "ensure-dualmode.sh"),
                 "/opt/openspan/ensure-dualmode.sh"),
                (os.path.join("..", "guest", "wait-hci0.sh"),
                 "/opt/openspan/wait-hci0.sh"),
                (os.path.join("..", "guest", "openspan_ble.py"),
                 "/opt/openspan/openspan_ble.py")]
        def work():
            reachable = False
            for _ in range(60):
                if ssh_guest("echo ok", timeout=5, quiet=True).stdout.strip() \
                        == "ok":
                    reachable = True
                    self._vm_reachable = True
                    break
                threading.Event().wait(3)
            if not reachable:
                return
            ensure_device_forwards(self.canvas.config)
            _emit("event", "VM reachable — syncing guest scripts…")
            # idempotent guest prep: keep the journal ON DISK so Bluetooth
            # events survive a VM power-off -- without this, a "Broadcast
            # broke the audio" report can never be diagnosed after the fact
            # (volatile journald is lost the moment the VM powers down)
            ssh_guest("install -d /var/log/journal && "
                      "systemctl kill -s USR1 systemd-journald",
                      timeout=10, quiet=True)
            daemon_changed = False
            for local, remote in jobs:
                src = os.path.join(HERE, local)
                if not os.path.exists(src):
                    continue
                try:
                    with open(src, "r", encoding="utf-8", newline="") as f:
                        content = f.read().replace("\r\n", "\n").replace(
                            "\r", "\n")
                    # bytes (not text=True) so Windows never re-adds \r\n on the
                    # ssh stdin -- the guest must receive pure LF or bash breaks
                    # write-then-rename: atomic replace, so a script that is
                    # RUNNING right now (btready.sh during boot) keeps its old
                    # inode instead of being truncated mid-execution
                    r = subprocess.run(
                        _ssh_argv(
                            f"cat > {remote}.new && "
                            f"if [ -f {remote} ] && "
                            f"cmp -s {remote}.new {remote}; then "
                            f"rm -f {remote}.new; echo UNCHANGED; "
                            f"else chmod +x {remote}.new && "
                            f"mv -f {remote}.new {remote} && echo CHANGED; fi"),
                        input=content.encode("utf-8"), timeout=20,
                        capture_output=True,
                        creationflags=NO_WINDOW)
                    if r.returncode != 0:
                        detail = (r.stderr or r.stdout or b"").decode(
                            "utf-8", errors="replace").strip()
                        _emit("err", f"guest sync failed for "
                                     f"{os.path.basename(remote)}: "
                                     f"{detail[-180:]}")
                    elif (remote.endswith("/openspan_ble.py")
                          or remote.endswith("/openspanble-mac.service")) \
                            and b"CHANGED" in (r.stdout or b"").split():
                        # WHOLE TOKEN. The guest prints CHANGED or UNCHANGED and
                        # a substring test matches BOTH -- so the HID daemon was
                        # restarted on EVERY launch, wiping GATT subscription
                        # state exactly when a bonded host reconnects (the
                        # no-input ghost this file warns about elsewhere).
                        daemon_changed = True
                except Exception as exc:  # noqa: BLE001
                    _emit("err", f"guest sync failed for "
                                 f"{os.path.basename(remote)}: {exc}")
            if daemon_changed:
                # Restart the PER-DEVICE lanes only. The legacy single-lane
                # units are retired below; restarting them here re-armed a
                # daemon on a port a real device owns.
                r = ssh_guest(
                    "systemctl daemon-reload; "
                    "for u in $(systemctl list-units --no-legend "
                    "--state=active 'openspanble@*' 2>/dev/null "
                    "| awk '{print $1}'); do systemctl restart \"$u\"; done; "
                    "exit 0",
                    timeout=35, quiet=True)
                if r.returncode == 0:
                    _emit("event", "guest HID daemon updated and restarted "
                                   "(advertising remains off).")
                else:
                    detail = (r.stderr or r.stdout or "").strip()
                    _emit("err", "guest HID daemon update did not restart "
                                 f"cleanly: {detail[-180:]}")
            # Retire the legacy single-lane units ONCE. This is required, not
            # cosmetic: set-hid-device.sh refuses a controller that another
            # lane's drop-in already claims, and it greps the FILES -- so a
            # stale openspanble{,-mac}.service.d/20-radio.conf makes every real
            # lane fail with exit 3 while the legacy daemon still holds the
            # port, hiding the failure. Idempotent.
            ssh_guest(
                "systemctl disable --now openspanble openspanble-mac "
                ">/dev/null 2>&1; "
                "rm -f /etc/systemd/system/openspanble.service.d/20-radio.conf "
                "/etc/systemd/system/openspanble-mac.service.d/20-radio.conf; "
                "systemctl daemon-reload; exit 0",
                timeout=30, quiet=True)
            # Bring up a lane for every device that already has a radio. This
            # is what makes the app self-healing: a lane is just a drop-in plus
            # an openspanble@<id> instance, so re-asserting it is idempotent
            # (the script prints UNCHANGED when it is already right). Without
            # this, retiring the legacy units left NOTHING listening and the
            # app waited forever on a daemon that only Pair could create.
            # Safe by construction: each device owns its own radio, so these
            # can never claim each other's controller.
            for device in self.canvas.devices():
                radio = str(device.get("radio", "") or "")
                if not device.get("enabled", True) or not radio:
                    continue
                name = f"OpenSpan {device.get('name', device['id'])}"[:24]
                r = ssh_guest(
                    "bash /opt/openspan/bt-preflight.sh; "
                    f"bash /opt/openspan/set-hid-device.sh {device['id']} "
                    f"{radio} {int(device.get('port', BASE_PORT))} "
                    f"{shlex.quote(name)}",
                    timeout=45, quiet=True)
                if r.returncode == 0:
                    _emit("event", f"{device.get('name', device['id'])} lane "
                                   f"ready on its own radio ({radio}).")
                else:
                    detail = (r.stderr or r.stdout or "").strip()
                    _emit("err", f"{device.get('name', device['id'])} lane "
                                 f"could not be brought up: {detail[-160:]}")
            # NOTE: beyond this, lanes are (re)configured by _pair_device_worker
            # via set-hid-device.sh, which owns the drop-in for each
            # openspanble@<id>. There is deliberately no boot-time re-apply of
            # a global iPad/Mac radio here: it wrote the LEGACY drop-ins with
            # the same radios the real lanes use, and set-hid-device.sh then
            # refused that controller (exit 3) so the real lane was never
            # created -- silently, because the port probe was satisfied by the
            # legacy daemon still holding it.
        threading.Thread(target=work, daemon=True).start()

    def device_record(self, device_id):
        return device_by_id(self.canvas.config, device_id)

    def device_lane(self, device_id):
        """(record, controller_mac, port, advertised_name) for a device."""
        record = self.device_record(device_id) or {}
        return (record,
                str(record.get("radio", "") or "").upper(),
                int(record.get("port", BASE_PORT)),
                f"OpenSpan {record.get('name', device_id)}")

    def _dev_state(self, device_id):
        """Mutable per-device pairing state, created on demand."""
        return self._dev_states.setdefault(device_id, {
            "inflight": False, "broadcasting": False, "paired": False,
            "gen": 0, "started": 0.0, "lock": threading.Lock(),
        })

    def _pair_device(self, device_id, reset=False, confirm=True):
        record, controller, _port, _name = self.device_lane(device_id)
        if not record:
            return
        label = record.get("name", device_id)
        if not controller:
            dark_alert(
                self.root, f"Assign a radio to {label}",
                f"Every device needs its OWN Bluetooth radio. Open Radio "
                f"options and assign one to “{label}” first.")
            return
        clash = next(
            (other for other in self.canvas.devices()
             if other.get("id") != device_id
             and str(other.get("radio", "")).upper() == controller), None)
        if clash:
            dark_alert(
                self.root, "That radio is already in use",
                f"“{clash.get('name')}” is already using this radio. Each "
                f"device needs its own, so give “{label}” a different one.")
            return
        state = self._dev_state(device_id)
        if state["inflight"] or state["broadcasting"]:
            return
        title = (f"Reset and re-pair {label}?" if reset
                 else f"Pair {label} now?")
        body = (
            f"This forgets the saved bond for “{label}” on its own radio, then "
            f"broadcasts for a clean pairing. Pair only from that device."
            if reset else
            f"“{label}” has its own Bluetooth radio, which will now broadcast. "
            f"Pair only from that device — do not select this name on any "
            f"other. Your other devices and audio are untouched.")
        if confirm and not dark_confirm(self.root, title, body):
            return
        state["inflight"] = True
        state["started"] = time.time()
        self.status.set(f"Working — preparing the Bluetooth radio for {label}…")
        threading.Thread(target=self._pair_device_worker,
                         args=(device_id, reset), daemon=True).start()

    def _pair_device_worker(self, device_id, reset=False):
        record, controller, port, adv_name = self.device_lane(device_id)
        label = record.get("name", device_id)
        state = self._dev_state(device_id)
        if not vm_running():
            start_vm_clean()
            for _ in range(45):
                if ssh_guest(
                        "echo ok", timeout=5, quiet=True).stdout.strip() == "ok":
                    break
                threading.Event().wait(2)
        if not re.match(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$", controller):
            r = subprocess.CompletedProcess(
                [], 1, "", f"No radio is assigned to {label}")
        else:
            # The guest will listen on this port, but Windows can only
            # reach it through a NAT rule on the VM. Devices added after start
            # had no rule: the lane came up perfectly, the guest-side check for
            # a listening socket passed, and then every command from this side
            # timed out -- so pairing failed with a healthy daemon behind it.
            ensure_device_forwards(self.canvas.config)
            reset_arg = " --reset" if reset else ""
            unit = f"openspanble@{device_id}"
            restart = f"systemctl restart {unit}; " if reset else ""
            command = (
                # unwedge a non-answering bluetoothd first: otherwise every
                # command below just blocks until the ssh timeout
                "bash /opt/openspan/bt-preflight.sh; "
                f"bash /opt/openspan/set-hid-device.sh {device_id} "
                f"{controller} {port} {shlex.quote(adv_name)}; "
                "python3 /opt/openspan/openspan_bt.py prepare-hid "
                f"--controller {controller} --target {device_id}{reset_arg}; "
                f"{restart}"
                "for i in $(seq 25); do "
                f"ss -ltn 2>/dev/null | grep -q ':{port}' && exit 0; "
                "sleep 1; done; exit 1")
            r = ssh_guest(command, timeout=55)
        if r.returncode:
            set_target_advertising(device_id, False)
            state["inflight"] = False
            detail = (r.stderr or r.stdout or "guest command failed").strip()
            _emit("err", f"{label} pair FAILED before advertising — "
                  f"{detail[-220:]}. Nothing is advertising.")
            self.ui(lambda: self.status.set(f"{label} pair failed — see console."))
            return
        if not state["inflight"]:
            _emit("event", f"{label} pair cancelled before advertising.")
            return
        _emit("event", f"{label} radio ready — starting its independent "
                       "Bluetooth HID broadcast…")
        adv_ok = set_target_advertising(device_id, True)
        status = target_daemon_status(device_id)
        really_adv = bool(status and status.get("advertising"))
        if not adv_ok or not really_adv:
            cleanup_ok = set_target_advertising(device_id, False)
            state["broadcasting"] = False
            state["inflight"] = False
            _emit("err", f"{label.upper()} BROADCAST DID NOT START. "
                  f"Cleanup-off confirmed={cleanup_ok}.")
            self.ui(lambda: self.status.set(
                f"{label} advertising didn't start — see console."))
            return
        with state["lock"]:
            cancelled = not state["inflight"]
            if not cancelled:
                state["broadcasting"] = True
        if cancelled:
            set_target_advertising(device_id, False)
            return
        _emit("event", f"✅ {label.upper()} NOW BROADCASTING — on that device, "
                       f"open Bluetooth and connect “{adv_name}”. Do not "
                       f"select it on any other device.")
        self.ui(lambda: self.status.set(
            f"📡 {label} — connect “{adv_name}” from that device."))

    def _connect_device(self, device_id):
        self._pair_device(device_id, reset=False, confirm=False)

    def _disconnect_device(self, device_id):
        record, _controller, _port, _name = self.device_lane(device_id)
        label = record.get("name", device_id)
        state = self._dev_state(device_id)

        def work():
            with state["lock"]:
                state["broadcasting"] = False
                state["inflight"] = False
            set_target_advertising(device_id, False)
            reply = target_daemon_cmd(device_id, {"cmd": "disconnect"})
            if reply and reply.get("ok"):
                _emit("event", f"{label} DISCONNECTED "
                               f"({reply.get('disconnected', 0)} link) — "
                               "advertising off, its on-screen keyboard returns.")
            else:
                _emit("err", f"couldn't disconnect {label}.")
            self._refresh_device_paired(device_id)
        threading.Thread(target=work, daemon=True).start()

    def _unpair_device(self, device_id):
        record, controller, _port, _name = self.device_lane(device_id)
        label = record.get("name", device_id)
        if not dark_confirm(
                self.root, f"Unpair {label}?",
                f"This forgets the bond for “{label}” on its own radio. Remove "
                f"OpenSpan in that device's Bluetooth settings too. No other "
                f"device is affected."):
            return
        state = self._dev_state(device_id)

        def work():
            with state["lock"]:
                state["broadcasting"] = False
                state["inflight"] = False
            set_target_advertising(device_id, False)
            target_daemon_cmd(device_id, {"cmd": "disconnect"})
            r = ssh_guest(
                "python3 /opt/openspan/openspan_bt.py forget-hid "
                f"--controller {controller} --target {device_id}",
                timeout=25)
            if r.returncode == 0:
                state["paired"] = False
                _emit("event", f"{label} UNPAIRED on the OpenSpan side.")
            else:
                _emit("err", f"{label} unpair failed — see console.")
            self._refresh_device_paired(device_id)
        threading.Thread(target=work, daemon=True).start()

    def _refresh_device_paired(self, device_id):
        """Read the real bond state for ONE device from its own radio."""
        _record, controller, _port, _name = self.device_lane(device_id)
        state = self._dev_state(device_id)
        state["gen"] += 1
        generation = state["gen"]

        def work():
            try:
                if not controller:
                    if generation == state["gen"]:
                        state["paired"] = False
                    return
                r = ssh_guest(
                    "python3 /opt/openspan/openspan_bt.py hid-status "
                    f"--controller {controller} --target {device_id}",
                    timeout=8, quiet=True)
                if r.returncode == 0 and generation == state["gen"]:
                    out = (r.stdout or "").upper()
                    # The guest prints exactly PAIRED or NOT_PAIRED. A bare
                    # substring test is TRUE for "NOT_PAIRED", which pinned
                    # every device to "paired" forever -- match the whole token.
                    state["paired"] = ("PAIRED" in out
                                       and "NOT_PAIRED" not in out)
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=work, daemon=True).start()

    # ---- device panel (dynamic: one row per configured device) -------------
    def _apply_device_rows(self, portal_on):
        """Colour + gate every device row from ITS OWN live state. One loop for
        N devices; no branch anywhere depends on what kind of device it is."""
        devices = self.canvas.devices()
        if set(self._dev_rows) != {d["id"] for d in devices}:
            self._rebuild_device_rows()
        for device in devices:
            device_id = device["id"]
            row = self._dev_rows.get(device_id)
            if not row:
                continue
            state = self._dev_state(device_id)
            status = self._dev_status.get(device_id)
            live = bool(status and status.get("kbd_subscribed"))
            paired = bool(state["paired"])
            up = status is not None
            busy = state["inflight"] or state["broadcasting"]
            radio = str(device.get("radio", "") or "")
            # Is this device's assigned radio actually PRESENT? A dongle that
            # vanished (unplugged, or claimed-but-not-attached by VirtualBox)
            # left the row showing its last known "paired" forever, because
            # every query errored with "controller not available" and the state
            # simply never updated. A frozen yes is worse than an honest
            # "cannot tell" -- it hides the real fault.
            known = {str(r.get("address", "")).upper()
                     for r in (getattr(self.bt_panel, "_radios", []) or [])}
            radio_missing = bool(radio) and bool(known) and radio not in known
            # grey = not paired · amber = paired/idle · green = live
            if radio_missing:
                colour, text = DANGER, "radio not present"
                paired = False
            elif live and portal_on:
                colour, text = ACCENT, "connected"
            elif live:
                colour, text = WARN, "portal off"
            elif paired:
                colour, text = WARN, "paired"
            elif not radio:
                colour, text = MUTED, "no radio assigned"
            else:
                colour, text = MUTED, "not paired"
            row["dot"].config(fg=colour)
            row["name"].config(text=f"{device.get('name', device_id)}  ·  {text}")
            row["radio"].config(
                text=(f"{radio}  :{device.get('port')}" if radio
                      else f":{device.get('port')}"))
            buttons = row["buttons"]
            # NOT gated on `up`: pairing is what brings the lane into
            # existence, so requiring its daemon first is a deadlock.
            usable = radio and not radio_missing
            buttons["pair"].state(
                ["!disabled"] if (usable and self._vm_reachable and not busy
                                  and not live) else ["disabled"])
            buttons["connect"].state(
                ["!disabled"] if (usable and up and not busy and paired
                                  and not live) else ["disabled"])
            # Disconnect doubles as CANCEL for an in-flight attempt
            buttons["disconnect"].state(
                ["!disabled"] if (live or busy) else ["disabled"])
            buttons["unpair"].state(
                ["!disabled"] if (usable and up and not busy and paired)
                else ["disabled"])
            self.canvas.set_target_state(device_id, live and portal_on, paired)

    def _any_device_busy(self):
        """True while ANY device is mid-pair/broadcast -- replaces the old
        global broadcasting/_pair_inflight pair, which no path sets any more."""
        return any(
            self._dev_state(d["id"])["inflight"]
            or self._dev_state(d["id"])["broadcasting"]
            for d in self.canvas.devices())

    def _refresh_all_device_paired(self):
        for device in self.canvas.devices():
            self._refresh_device_paired(device["id"])

    def _poll_device_status(self):
        """Probe every device's own daemon. Runs on a worker thread."""
        return {
            device["id"]: target_daemon_status(device["id"])
            for device in self.canvas.devices()
            if device.get("enabled", True)
        }

    def _rebuild_device_rows(self):
        """Render one identical control row per device. Called whenever the
        device list changes -- adding the Nth device needs no new UI code."""
        for child in list(self._dev_body.winfo_children()):
            child.destroy()
        self._dev_rows = {}
        devices = self.canvas.devices()
        if not devices:
            tk.Label(self._dev_body,
                     text="No devices yet — add the machines you want to "
                          "drive from this PC.",
                     bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(
                anchor="w", pady=4)
            return
        for device in devices:
            self._build_device_row(device)

    def _build_device_row(self, device):
        device_id = device["id"]
        row = tk.Frame(self._dev_body, bg=BG)
        row.pack(fill="x", pady=(2, 4))
        head = tk.Frame(row, bg=BG)
        head.pack(fill="x")
        dot = tk.Label(head, text="●", bg=BG, fg=MUTED, font=("Segoe UI", 11))
        dot.pack(side="left")
        name = tk.Label(head, text=device.get("name", device_id), bg=BG, fg=FG,
                        font=("Segoe UI Semibold", 10))
        name.pack(side="left", padx=(4, 8))
        radio = tk.Label(head, text="", bg=BG, fg=MUTED, font=("Consolas", 8))
        radio.pack(side="left")
        # The DEVICE record is the single writer of its radio. Assigning it
        # here (rather than in a fixed global slot) is what lets a device added
        # at runtime ever get one.
        ttk.Button(head, text="Radio…",
                   command=lambda d=device_id: self._assign_device_radio(d)
                   ).pack(side="right", padx=(4, 0))
        ttk.Button(head, text="Input…",
                   command=lambda d=device_id: self._device_input_dialog(d)
                   ).pack(side="right", padx=(4, 0))
        ttk.Button(head, text="Rename",
                   command=lambda d=device_id: self._rename_device(d)).pack(
            side="right", padx=(4, 0))
        ttk.Button(head, text="Displays…",
                   command=lambda d=device_id: self._edit_device_displays(d)
                   ).pack(side="right", padx=(4, 0))
        ttk.Button(head, text="Remove",
                   command=lambda d=device_id: self._remove_device(d)).pack(
            side="right", padx=(4, 0))
        verbs = tk.Frame(row, bg=BG)
        verbs.pack(fill="x", pady=(3, 0))
        buttons = {}
        for column, (key, text, command) in enumerate((
                ("pair", "Pair",
                 lambda d=device_id: self._pair_device(d)),
                ("connect", "Connect",
                 lambda d=device_id: self._connect_device(d)),
                ("disconnect", "Disconnect",
                 lambda d=device_id: self._disconnect_device(d)),
                ("unpair", "Unpair",
                 lambda d=device_id: self._unpair_device(d)))):
            button = ttk.Button(verbs, text=text, command=command)
            button.grid(row=0, column=column, sticky="ew", padx=3)
            button.state(["disabled"])
            buttons[key] = button
        for column in range(4):
            verbs.columnconfigure(column, weight=1)
        self._dev_rows[device_id] = {
            "dot": dot, "name": name, "radio": radio, "buttons": buttons}

    def _screen_sizes_dialog(self):
        """Type each screen's real diagonal in inches. Size is DERIVED from it
        (with the aspect its resolution and rotation imply), so the arrangement
        is physically truthful instead of being drawn to taste."""
        win = FrameModal(self.root)
        win.title("Screen sizes")
        win.configure(bg=CARD)
        win.transient(self.root)
        win.resizable(False, False)

        tk.Label(win, text="Screen sizes", bg=CARD, fg=FG,
                 font=("Segoe UI Semibold", 12)).pack(
            anchor="w", padx=18, pady=(16, 2))
        tk.Label(win,
                 text="Enter each screen's diagonal in inches. Everything is "
                      "drawn to the same physical scale, so a 32\" really is "
                      "about twice the width of a 17\". Resolution is not "
                      "changed.",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 9), wraplength=440,
                 justify="left").pack(anchor="w", padx=18, pady=(0, 10))
        rows = []

        def add_row(parent, label, detail, current):
            box = tk.Frame(parent, bg=CARD)
            box.pack(fill="x", padx=18, pady=2)
            tk.Label(box, text=label, bg=CARD, fg=FG, width=22, anchor="w",
                     font=("Segoe UI", 10)).pack(side="left")
            var = tk.StringVar(value=f"{float(current or 0):g}"
                               if current else "")
            ttk.Entry(box, textvariable=var, width=7).pack(side="left")
            tk.Label(box, text="in", bg=CARD, fg=MUTED,
                     font=("Segoe UI", 9)).pack(side="left", padx=(4, 10))
            tk.Label(box, text=detail, bg=CARD, fg=MUTED,
                     font=("Consolas", 8)).pack(side="left")
            return var

        tk.Label(win, text="This PC", bg=CARD, fg=ACCENT,
                 font=("Segoe UI Semibold", 10)).pack(anchor="w", padx=18,
                                                      pady=(6, 2))
        for monitor in self.canvas.monitors:
            var = add_row(win, monitor["name"].replace("\\\\.\\", ""),
                          f"{monitor['w']}x{monitor['h']}",
                          monitor.get("diagonal_in"))
            rows.append(("monitor", monitor, var))
        for device in self.canvas.devices():
            tk.Label(win, text=device.get("name", device["id"]), bg=CARD,
                     fg=ACCENT, font=("Segoe UI Semibold", 10)).pack(
                anchor="w", padx=18, pady=(8, 2))
            for display in device.get("displays", []):
                var = add_row(
                    win, display.get("name", display["id"]),
                    f"{display['res_w']}x{display['res_h']}"
                    + (f" @{display['rotation']}°" if display.get("rotation")
                       else ""),
                    display.get("diagonal_in"))
                rows.append(("display", display, var))

        def apply_and_close():
            changed = 0
            for kind, obj, var in rows:
                text = var.get().strip()
                if not text:
                    continue
                try:
                    inches = max(1.0, min(120.0, float(text)))
                except ValueError:
                    dark_alert(self.root, "Not a number",
                               f"“{text}” is not a screen size in inches.")
                    return
                obj["diagonal_in"] = inches
                if kind == "monitor":
                    obj["layout_w"], obj["layout_h"] = physical_size(
                        inches, obj["w"], obj["h"], 0)
                else:
                    obj["w"], obj["h"] = physical_size(
                        inches, obj["res_w"], obj["res_h"],
                        obj.get("rotation", 0))
                changed += 1
            win.destroy()
            self.canvas.redraw()
            self.canvas.save()
            _emit("event", f"screen sizes updated ({changed}) — drag them "
                           "into your desk arrangement.")

        row = tk.Frame(win, bg=CARD)
        row.pack(anchor="e", padx=18, pady=(16, 16))
        ttk.Button(row, text="Apply", style="Accent.TButton",
                   command=apply_and_close).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="left")
        win.update_idletasks()
        win.geometry(
            f"+{self.root.winfo_rootx() + 110}+{self.root.winfo_rooty() + 60}")
        win.deiconify()
        win.grab_set()

    def _device_input_dialog(self, device_id):
        """Per-device input settings with real sliders. Everything here is
        applied on THIS side so the device's own settings stay untouched and it
        remains pleasant to use standalone."""
        record = self.device_record(device_id)
        if not record:
            return
        label = record.get("name", device_id)
        win = FrameModal(self.root)
        win.title(f"Input — {label}")
        win.configure(bg=CARD)
        win.transient(self.root)
        win.resizable(False, False)

        tk.Label(win, text=f"Input settings — {label}", bg=CARD, fg=FG,
                 font=("Segoe UI Semibold", 12)).pack(
            anchor="w", padx=18, pady=(16, 2))
        tk.Label(win,
                 text="Applied by OpenSpan, not by the device — so the "
                      "device keeps its own settings and stays usable on its "
                      "own.",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 9), wraplength=430,
                 justify="left").pack(anchor="w", padx=18, pady=(0, 10))

        state = {}

        def slider(key, title, lo, hi, default, hint):
            box = tk.Frame(win, bg=CARD)
            box.pack(fill="x", padx=18, pady=(6, 0))
            head = tk.Frame(box, bg=CARD)
            head.pack(fill="x")
            tk.Label(head, text=title, bg=CARD, fg=FG,
                     font=("Segoe UI", 10)).pack(side="left")
            val = tk.Label(head, text="", bg=CARD, fg=ACCENT,
                           font=("Consolas", 10))
            val.pack(side="right")
            var = tk.DoubleVar(value=float(record.get(key, default)))

            def on_move(_v=None):
                val.config(text=f"{var.get():.2f}")
            var.trace_add("write", lambda *_a: on_move())
            ttk.Scale(box, from_=lo, to=hi, variable=var,
                      orient="horizontal").pack(fill="x", pady=(2, 0))
            tk.Label(box, text=hint, bg=CARD, fg=MUTED,
                     font=("Segoe UI", 8), wraplength=430,
                     justify="left").pack(anchor="w")
            on_move()
            state[key] = var

        slider("sensitivity", "Mouse sensitivity", 0.1, 3.0, 1.0,
               "How far the pointer travels on this device for the same hand "
               "movement. Lower it if this device feels too fast.")
        slider("pointer_accel", "Pointer acceleration", 0.0, 4.0, 0.0,
               "0 = perfectly linear. Applied here, so the pointer position "
               "stays exact — unlike the device's own acceleration.")

        comp = tk.BooleanVar(
            value=bool(record.get("compensate_target_accel", False)))
        ttk.Checkbutton(
            win, text="Compensate for this device's own pointer acceleration",
            variable=comp).pack(anchor="w", padx=18, pady=(14, 0))
        tk.Label(win,
                 text="Leave the DEVICE's acceleration switched on. OpenSpan "
                      "inverts its curve — asking for a distance and sending "
                      "the exact report that produces it — so the pointer "
                      "stays accurate without changing anything on the device.",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 8), wraplength=430,
                 justify="left").pack(anchor="w", padx=38)

        verbatim = tk.BooleanVar(
            value=bool(record.get("keyboard_verbatim", False)))
        ttk.Checkbutton(
            win, text="Send keys exactly as pressed (this device remaps its own)",
            variable=verbatim).pack(anchor="w", padx=18, pady=(14, 0))
        tk.Label(win,
                 text="Turn this on when the DEVICE already swaps its own "
                      "modifiers — a Mac with Command and Control exchanged, "
                      "for example. OpenSpan then sends exactly what you press "
                      "and lets the device do the mapping, instead of "
                      "translating twice and fighting itself.",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 8), wraplength=430,
                 justify="left").pack(anchor="w", padx=38)

        inv = tk.BooleanVar(value=bool(record.get("scroll_invert", False)))
        ttk.Checkbutton(win, text="Invert scroll wheel on this device",
                        variable=inv).pack(anchor="w", padx=18, pady=(12, 0))

        alt_now = record.get("modifier_remap")
        alt_var = tk.StringVar(
            value=("command" if (alt_now or {}).get("alt") in ("cmd", "gui")
                   else ("option" if isinstance(alt_now, dict) else "inherit")))
        altbox = tk.Frame(win, bg=CARD)
        altbox.pack(fill="x", padx=18, pady=(10, 0))
        tk.Label(altbox, text="Send physical Alt as", bg=CARD, fg=FG,
                 font=("Segoe UI", 10)).pack(side="left")
        ttk.Combobox(altbox, textvariable=alt_var, width=12, state="readonly",
                     values=("option", "command", "inherit")).pack(
            side="left", padx=(10, 0))
        tk.Label(win, text="option = macOS Option  ·  command = iPad Command",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(
            anchor="w", padx=18)

        def apply_and_close():
            record["sensitivity"] = round(float(state["sensitivity"].get()), 3)
            record["pointer_accel"] = round(
                float(state["pointer_accel"].get()), 3)
            record["scroll_invert"] = bool(inv.get())
            record["compensate_target_accel"] = bool(comp.get())
            record["keyboard_verbatim"] = bool(verbatim.get())
            choice = alt_var.get()
            record["modifier_remap"] = (
                {} if choice == "option"
                else ({"alt": "cmd"} if choice == "command" else None))
            self.canvas.save()
            win.destroy()
            _emit("event", f"{label}: sensitivity "
                           f"{record['sensitivity']:.2f}, acceleration "
                           f"{record['pointer_accel']:.2f}, Alt={choice}.")

        row = tk.Frame(win, bg=CARD)
        row.pack(anchor="e", padx=18, pady=(16, 16))
        ttk.Button(row, text="Apply", style="Accent.TButton",
                   command=apply_and_close).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="left")
        win.update_idletasks()
        win.geometry(
            f"+{self.root.winfo_rootx() + 120}+{self.root.winfo_rooty() + 90}")
        win.deiconify()
        win.grab_set()

    def _device_input_dialog_legacy(self, device_id):
        """Per-device input settings: scroll direction and how physical Alt is
        delivered. These are per DEVICE because the right answer differs: an
        iPad wants Alt as Command, a Mac wants it as Option."""
        record = self.device_record(device_id)
        if not record:
            return
        label = record.get("name", device_id)
        remap = record.get("modifier_remap")
        alt_now = ("command" if (remap or {}).get("alt") in ("cmd", "gui",
                                                            "win")
                   else ("option" if isinstance(remap, dict) else "inherit"))
        answer = dark_prompt(
            self.root, f"Input settings — {label}",
            "1. Invert scroll wheel:  "
            f"[{'ON' if record.get('scroll_invert') else 'off'}]\n"
            f"2. Send physical Alt as:  [{alt_now}]\n"
            "     option  = macOS Option/Alt  (correct for a Mac)\n"
            "     command = Command/GUI       (correct for an iPad)\n"
            "     inherit = use the shared keymap\n\n"
            "Type: 1 to toggle scroll, 2 option|command|inherit, "
            "or 3 <number>",
            default="")
        if answer is None or not answer.strip():
            return
        parts = answer.strip().lower().split()
        if parts[0] == "1":
            record["scroll_invert"] = not bool(record.get("scroll_invert"))
            _emit("event", f"{label}: scroll wheel "
                           f"{'INVERTED' if record['scroll_invert'] else 'normal'}.")
        elif parts[0] == "2" and len(parts) > 1:
            choice = parts[1]
            if choice == "option":
                # explicit empty override -> physical Alt passes through as
                # the HID Alt bit, which macOS reports as Option
                record["modifier_remap"] = {}
            elif choice == "command":
                record["modifier_remap"] = {"alt": "cmd"}
            elif choice == "inherit":
                record["modifier_remap"] = None
            else:
                dark_alert(self.root, "Not a choice",
                           "Use: 2 option, 2 command, or 2 inherit.")
                return
            _emit("event", f"{label}: physical Alt is sent as {choice}.")
        elif parts[0] == "3" and len(parts) > 1:
            try:
                value = max(0.0, min(4.0, float(parts[1])))
            except ValueError:
                dark_alert(self.root, "Not a number", "Use: 3 1.0")
                return
            record["pointer_accel"] = value
            _emit("event", f"{label}: pointer acceleration "
                           f"{'OFF (linear)' if value == 0 else value}.")
        else:
            return
        self.canvas.save()

    def _assign_device_radio(self, device_id):
        """Give ONE device its own radio. The device record is the single
        source of truth -- the pair path reads device["radio"], so this is the
        only thing that can bring a newly added device's lane to life."""
        record = self.device_record(device_id)
        if not record:
            return
        radios = list(getattr(self.bt_panel, "_radios", []) or [])
        if not radios:
            dark_alert(self.root, "No radios found yet",
                       "The VM hasn't reported its Bluetooth controllers yet. "
                       "Wait for the bridge to finish booting, then try again.")
            return
        taken = {str(d.get("radio", "")).upper(): d.get("name", d["id"])
                 for d in self.canvas.devices() if d.get("id") != device_id
                 and d.get("radio")}
        lines, options = [], []
        for index, radio in enumerate(radios, 1):
            address = str(radio.get("address", "")).upper()
            label = (radio.get("hardware") or radio.get("alias")
                     or radio.get("hci") or address)
            owner = taken.get(address)
            lines.append(f"  {index}. {label} — {address}"
                         + (f"   [in use by {owner}]" if owner else ""))
            options.append(address)
        answer = dark_prompt(
            self.root, f"Radio for {record.get('name', device_id)}",
            "Each device needs its OWN radio. Enter the number:\n"
            + "\n".join(lines),
            default="")
        if answer is None or not answer.strip():
            return
        try:
            address = options[int(answer.strip()) - 1]
        except (ValueError, IndexError):
            dark_alert(self.root, "Not a listed number",
                       f"Enter 1–{len(options)}.")
            return
        if address in taken:
            dark_alert(self.root, "That radio is already in use",
                       f"“{taken[address]}” is using it. Each device needs "
                       f"its own radio.")
            return
        record["radio"] = address
        self.canvas.save()
        self._rebuild_device_rows()
        _emit("event", f"{record.get('name', device_id)} assigned radio "
                       f"{address}. Press Pair to bring its lane up.")

    def _add_device_dialog(self):
        name = dark_prompt(
            self.root, "Add a device",
            "What do you want to call it? (Any machine you can pair a "
            "Bluetooth keyboard to — nothing is installed on it.)",
            default="")
        if name is None:
            return
        device = self.canvas.add_device(name.strip() or None)
        self._dev_state(device["id"])
        self._rebuild_device_rows()
        _emit("event", f"added device “{device['name']}” on port "
                       f"{device['port']} — assign it a radio in Radio options.")

    def _rename_device(self, device_id):
        record = self.device_record(device_id)
        if not record:
            return
        name = dark_prompt(self.root, "Rename device",
                           "The name is only a label — the device keeps its "
                           "radio, port and bonds.",
                           default=record.get("name", ""))
        if name is None or not name.strip():
            return
        record["name"] = name.strip()
        self.canvas.save()
        self._rebuild_device_rows()

    def _remove_device(self, device_id):
        record = self.device_record(device_id)
        if not record:
            return
        label = record.get("name", device_id)
        if not dark_confirm(
                self.root, f"Remove {label}?",
                f"This removes “{label}” from OpenSpan, including its screens "
                f"in the arrangement. Unpair it first if it is still bonded."):
            return
        self.canvas.remove_device(device_id)
        self._dev_states.pop(device_id, None)
        self._rebuild_device_rows()
        _emit("event", f"removed device “{label}”.")

    def _edit_device_displays(self, device_id):
        record = self.device_record(device_id)
        if not record:
            return
        MacDisplayEditor(self.root, self.canvas, device_id=device_id)
        self._rebuild_device_rows()

    def _stop_portal_if_running(self):
        """Stop the input portal if it's running (Tk-touching -> UI thread)."""
        if self.portal_proc and self.portal_proc.poll() is None:
            self.toggle_portal()

    # ---- status tick ----
    def _tick(self):
        threading.Thread(target=self._poll, daemon=True).start()
        self.root.after(3000, self._tick)

    def _poll(self):
        """Worker thread: network/process checks only — every widget update
        happens in _apply_poll on the UI thread."""
        if self._closing:
            return  # shutting down: never respawn anything past _full_stop
        running = vm_running()
        if not running:
            self._vm_reachable = False
        st = daemon_status() if running else None
        # probe EVERY device's own daemon (worker thread; no Tk here)
        dev_status = self._poll_device_status() if running else {}
        self._dev_status = dev_status
        mac_st = None
        on = bool(self.portal_proc and self.portal_proc.poll() is None)
        self._ensure_audio()  # watchdog: relaunch the sender if it died
        aud = bool(self.audio_proc and self.audio_proc.poll() is None)
        # compact mode has no device list on screen, so keep the buds line
        # fresh with a periodic (no-scan) refresh every ~15s
        self._poll_n = getattr(self, "_poll_n", 0) + 1
        if running and self._poll_n % 5 == 0:
            self.bt_panel.refresh(quiet=True)  # routine poll: no console line
            self._refresh_all_device_paired()   # keep bond state fresh; self-heals
        if running and self._poll_n % 20 == 0:
            # flush the VM disk every ~60s so a bond (or any state) can't be
            # lost to an unclean poweroff/crash between the connect-edge sync
            # and shutdown -- the recurring "have to re-pair" root cause
            threading.Thread(
                target=lambda: ssh_guest("sync", timeout=8, quiet=True),
                daemon=True).start()
        self.ui(lambda: self._apply_poll(running, st, on, aud, mac_st))

    def _apply_poll(self, running, st, on, aud, mac_st=None):
        # per-indicator status row: each token coloured by ITS OWN live state.
        def setind(key, text, good):
            self._ind[key].config(text=text, fg=(ACCENT if good else MUTED))
        setind("vm", f"VM {'●' if running else '○'}", running)
        if st:
            _sub = bool(st.get("kbd_subscribed"))
            if _sub and on:
                self._ind["ipad"].config(text="iPad ● connected", fg=ACCENT)
            elif _sub:                       # link up but portal off -> amber
                self._ind["ipad"].config(text="iPad ◐ portal off", fg=WARN)
            elif any(self._dev_state(d["id"])["paired"]
                     for d in self.canvas.devices()):
                self._ind["ipad"].config(text="iPad ◐ paired", fg=WARN)
            else:
                self._ind["ipad"].config(text="iPad ○ not paired", fg=MUTED)
        elif running:
            setind("ipad", "iPad ○ daemon starting", False)
        else:
            setind("ipad", "iPad ○ off", False)
        # one summary token for ALL devices -- the per-device detail lives in
        # the Devices panel, so the row does not grow a column per machine.
        _devs = self.canvas.devices()
        if _devs:
            _liveN = sum(
                1 for d in _devs
                if (self._dev_status.get(d["id"]) or {}).get("kbd_subscribed"))
            self._ind["mac"].config(
                text=f"devices {_liveN}/{len(_devs)}",
                fg=(ACCENT if _liveN else MUTED))
        else:
            self._ind["mac"].config(text="no devices", fg=MUTED)
        setind("portal", f"portal {'● ON' if on else '○ off'}", on)
        setind("audio", f"audio {'●' if aud else '○'}", aud)
        # Honest broadcast state, read straight from the daemon -- never a UI
        # guess: if it says BROADCASTING the machine really is advertising.
        _ipad_adv = bool(st and st.get("advertising"))
        _mac_adv = bool(mac_st and mac_st.get("advertising"))
        _adv = _ipad_adv or _mac_adv
        _adv_names = " + ".join(
            name for name, enabled in (
                ("iPad", _ipad_adv), ("Mac", _mac_adv)) if enabled)
        setind(
            "bcast",
            (f"📡 {_adv_names} BROADCASTING"
             if _adv else "📡 not broadcasting") if (st or mac_st) else "",
            _adv)
        # The boolean above is confirmed BlueZ state. Transitional and failure
        # states get their own honest, non-green rendering.
        _adv_state = st.get("advertising_state", "off") if st else "off"
        _adv_error = st.get("advertising_error", "") if st else ""
        if st and not _adv:
            if _adv_state in ("starting", "stopping"):
                self._ind["bcast"].config(
                    text=f"broadcast {_adv_state}...", fg=WARN)
            elif _adv_error:
                self._ind["bcast"].config(
                    text="broadcast error", fg=DANGER)
        # UIPI: without admin, input hooks die under any elevated window
        if is_elevated():
            self._ind["admin"].config(text="")
        else:
            self._ind["admin"].config(text="⚠ NOT ADMIN", fg=DANGER)
        # readiness banner (only reacts on a state change, so no console spam)
        if not running:
            r_state, r_txt, r_col = "stopped", "○  Stopped", MUTED
        elif not self._vm_reachable:
            # Ready means the VM ANSWERS -- not that some device's HID daemon
            # happens to be listening. A device with no lane yet (or no radio
            # assigned) must never pin the whole app on "Booting..." forever.
            r_state, r_txt, r_col = "booting", "◐  Booting…  (~90s)", PORTAL
        else:
            r_state, r_txt, r_col = "ready", "●  READY — connect headphones", \
                ACCENT
        if r_state != self._ready_state:
            self._ready_state = r_state
            try:
                self.ready_lbl.config(text=r_txt, fg=r_col)
            except Exception:  # noqa: BLE001
                pass
            _emit("event", {
                "stopped": "VM stopped — everything is down.",
                "booting": "VM up — services starting, hold ~90s…",
                "ready": "READY — the bridge is fully up. Connect your "
                         "headphones.",
            }[r_state])
            if r_state == "ready":
                # detect a returning bond at first-ready (don't wait ~15s for the
                # periodic tick) so Connect/Unpair light up right away; the
                # periodic read is still the self-heal.
                self._refresh_all_device_paired()
            if r_state == "ready" and not self._any_device_busy():
                # the buds try to reconnect on their own during the ~90s
                # boot, give up before the stack is up, and then just sit
                # there -- so reconnect them ourselves once we're READY
                self._auto_reconnect_audio("bridge is READY")
        connected = bool(st and st.get("kbd_subscribed"))
        mac_connected = bool(mac_st and mac_st.get("kbd_subscribed"))
        # snapshot for the tray menu (built on the Tk thread; must never block)
        self._cache = {"running": running, "connected": connected, "on": on,
                       "aud": aud,
                       "busy": self._any_device_busy()}
        self.canvas.set_ipad_state(connected and on, False)
        # gate the four verbs by REAL state. Pair: daemon up + not mid-pair.
        # Connect: bonded but not connected. Disconnect: connected. Unpair:
        # bonded. Never fight the pair flow while it owns the radio.
        self._apply_device_rows(on)
        # console confirmation on the iPad connect/disconnect edge
        if connected != self._ipad_conn:
            if self._ipad_conn is not None or connected:
                if connected:
                    _emit("event", "iPad CONNECTED — keyboard/mouse subscribed "
                          "and live.")
                    # flush the VM disk now so a fresh bond survives an unclean
                    # poweroff/crash (BlueZ writes bonds to /var/lib/bluetooth;
                    # they can otherwise sit unflushed and be lost on restart)
                    threading.Thread(
                        target=lambda: ssh_guest("sync", timeout=8, quiet=True),
                        daemon=True).start()
                elif st is not None:
                    _emit("event", "iPad disconnected.")
            self._ipad_conn = connected
            self._refresh_all_device_paired()   # a bond may have just formed or dropped
        # connect/disconnect edge for EVERY device, reported by its own name
        for _dev in self.canvas.devices():
            _did = _dev["id"]
            _live = bool(
                (self._dev_status.get(_did) or {}).get("kbd_subscribed"))
            if _live == self._dev_conn.get(_did):
                continue
            if _did in self._dev_conn or _live:
                _label = _dev.get("name", _did)
                if _live:
                    _emit("event", f"{_label} CONNECTED — keyboard/mouse "
                                   "subscribed and live.")
                    # flush the VM disk so a fresh bond survives a hard stop
                    threading.Thread(
                        target=lambda: ssh_guest("sync", timeout=8, quiet=True),
                        daemon=True).start()
                    _st = self._dev_state(_did)
                    if _st["broadcasting"] or _st["inflight"]:
                        _st["broadcasting"] = False
                        _st["inflight"] = False
                        threading.Thread(
                            target=set_target_advertising, args=(_did, False),
                            daemon=True).start()
                        if not (self.portal_proc
                                and self.portal_proc.poll() is None):
                            self.toggle_portal()
                            _emit("event", f"{_label} paired — portal "
                                           "auto-started.")
                        on = bool(self.portal_proc
                                  and self.portal_proc.poll() is None)
                else:
                    _emit("event", f"{_label} disconnected.")
            self._dev_conn[_did] = _live
            self._refresh_device_paired(_did)
        # once the iPad connects: settle the button to a check, auto-start the
        # portal (no manual click), and bring the earbuds back — full steady
        # state without another button press. Clearing broadcasting/_pair_
        # inflight FIRST is required: _auto_reconnect_audio early-returns while
        # either is set.
        # an abandoned attempt must never leave a device's radio beaconing
        for _dev in self.canvas.devices():
            _st = self._dev_state(_dev["id"])
            if (_st["broadcasting"] or _st["inflight"]) and                     time.time() - _st["started"] > 300:
                threading.Thread(
                    target=set_target_advertising, args=(_dev["id"], False),
                    daemon=True).start()
                _st["broadcasting"] = False
                _st["inflight"] = False
                _emit("event", f"{_dev.get('name', _dev['id'])} advertising "
                               "window expired — press Pair or Connect again "
                               "when ready.")
        # secondary status readout — set AFTER the connect-edge auto-start so
        # `on` reflects the portal we may have just started this tick
        try:
            self.sys_status.set(
                f"VM {'● up' if running else '○ down'}    "
                f"keyboard {'● up' if st is not None else '○ down'}"
                f"{'  (iPad subscribed)' if (st and st.get('kbd_subscribed')) else ''}"
                f"    Mac {'● up' if mac_st is not None else '○ down'}"
                f"{' (subscribed)' if mac_connected else ''}"
                f"    audio {'● on' if aud else '○ off'}"
                f"    portal {'● on' if on else '○ off'}")
        except Exception:  # noqa: BLE001
            pass
        # while hidden in the tray, make sure the icon still exists (an
        # explorer.exe restart wipes tray icons); if it can't be restored,
        # bring the window back — the app must never be strandable
        if self._tray and not self._closing \
                and self.root.state() == "withdrawn":
            if not self._tray.ensure():
                _emit("event", "tray icon lost — bringing the window back.")
                self._from_tray()
        # ---- compact-mode widgets (cheap; update even when hidden) ----
        colors = {True: ACCENT, False: MUTED}
        self.c_stat["vm"].config(fg=colors[bool(running)])
        self.c_stat["ipad"].config(
            fg=colors[bool(st and st.get("kbd_subscribed"))])
        self.c_stat["mac"].config(fg=colors[mac_connected])
        self.c_stat["audio"].config(fg=colors[bool(aud)])
        self.c_stat["portal"].config(fg=colors[bool(on)])
        self.c_ready.config(text=r_txt, fg=r_col)
        names = self.bt_panel._connected_names if running else []
        self.c_buds.config(
            text="🎧  " + (", ".join(names) if names
                           else "no headphones connected"),
            fg=ACCENT if names else MUTED)
        if self._vol_ok is False and "disabled" not in self.c_vol.state():
            self.c_vol.state(["disabled"])
        v = self._vol_now
        if v is not None and not self._vol_drag \
                and self._vol_target is None:
            self._vol_syncing = True
            self.c_vol_var.set(round(v * 100))
            self._vol_syncing = False
        # `on` may have been refreshed by the connect-edge auto-start above; the
        # coloured portal token already reflects it. Keep self.status for the
        # transient line: don't stomp an in-flight broadcast or a failure
        # message; otherwise show a plain call-to-action for the current state.
        self._ind["portal"].config(text=f"portal {'● ON' if on else '○ off'}",
                                   fg=(ACCENT if on else MUTED))
        cur = self.status.get()
        if not self._any_device_busy() \
                and "fail" not in cur.lower() and "didn't" not in cur:
            if not running:
                self.status.set("Bridge stopped — click Bridge VM to start.")
            elif st is None:
                self.status.set("Booting the bridge… (~90s)")
            elif connected:
                self.status.set("iPad connected — keyboard & mouse bridging.")
            elif mac_connected:
                self.status.set("Device connected — keyboard & mouse bridging.")
            else:
                self.status.set(
                    "Ready — pair or connect the iPad or managed Mac.")
        # the Pair button stays a static "Pair"; connection state is shown by the
        # indicator colours + which of Connect/Disconnect/Unpair are enabled.
        self.vm_btn.config(text="Bridge VM ✓" if running
                           else "Start Bridge VM")
        self.portal_btn.config(text="Stop portal" if on else "Start portal")


def _single_instance_lock():
    """Windows named mutex as a single-instance lock. Returns the handle
    (held for the process lifetime) or None if another instance already
    holds it. The OS releases it automatically on exit -- even on a crash --
    so there is no stuck lock and no TCP TIME_WAIT to wait out."""
    try:
        import ctypes
        h = ctypes.windll.kernel32.CreateMutexW(None, False,
                                                "OpenSpanSingleInstance")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ALREADY_EXISTS
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
            return None
        return h
    except Exception:  # noqa: BLE001
        return True  # never block startup if the mutex mechanism is unavailable


def _release_single_instance_lock(lock):
    """Release the startup mutex before launching the elevated replacement."""
    if not lock or lock is True:
        return
    try:
        import ctypes
        ctypes.windll.kernel32.CloseHandle(lock)
    except Exception:  # noqa: BLE001
        pass


def run_app():
    """The GUI entry point — used by both `python openspan.py` and the
    frozen OpenSpan.exe (via openspan_launcher.py)."""
    lock = _single_instance_lock()
    if lock is None:
        # already running — exit immediately and silently, never stack a
        # window or block on a dialog
        sys.exit(0)

    # This decision must precede ensure_ssh_key and App(...): App construction
    # starts the VM/audio workers. Close therefore means a truly inert exit.
    if not is_elevated():
        startup_choice = _elevation_gate()
        if startup_choice == "close":
            return
        if startup_choice == "restart":
            _release_single_instance_lock(lock)
            if not _launch_elevated():
                _show_elevation_launch_failed()
            return

    key_ok = ensure_ssh_key()  # make + secure it before any guest operation
    root = tk.Tk()
    app = App(root)
    if not key_ok:
        _emit("err", "Bridge SSH key setup failed. Guest actions are disabled; "
                     "check id_openspan permissions and restart OpenSpan.")
    # X asks first (it's a FULL STOP: portal + audio + VM), and offers
    # "send to system tray" to keep the bridge running instead
    root.protocol("WM_DELETE_WINDOW", app._confirm_close)
    root.mainloop()
if __name__ == "__main__":
    run_app()
