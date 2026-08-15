#!/usr/bin/env python3
"""EsotericOS (Astral Compass) — one dark window that runs the whole desk.

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
import traceback
import tkinter as tk
from tkinter import ttk

# reuse the monitor enumeration + presets from the setup module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openspan_setup import enum_monitors, IPAD_PRESETS  # noqa: E402
from openspan_targets import (  # noqa: E402
    BASE_PORT, DESK_UNITS_PER_INCH, add_device, compute_adjacencies,
    compute_portals, device_by_id, physical_size,
    dedupe_display_ids, merge_live_monitors, normalize_config,
    oriented_resolution,
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
# The product is EsotericOS (Astral Compass identity). The VM name and every
# guest-side path stay "OpenSpan" -- that is plumbing, not brand, and renaming
# plumbing breaks bonds, units, and deploys for zero user-visible gain.
APP_LABEL = (str(_BOOT_SETTINGS.get("app_label", "EsotericOS")).strip()
             or "EsotericOS")
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
# Astral Compass app icon (brand kit export). The legacy openspan.ico is the
# fallback so a stray copy of the exe without brand/ still shows an icon.
ICON = os.path.join(ROOT, "brand", "esotericos-app.ico")
if not os.path.isfile(ICON):
    ICON = os.path.join(ROOT, "openspan.ico")
TRAY_ICON = os.path.join(ROOT, "brand", "esotericos-tray.ico")  # for the
# tray's return: size-specific kit frames, never rescaled masters.

# ---- build identity -------------------------------------------------------
# Bump deliberately. The build TIME comes from the executable itself, because
# six builds can share a version in one evening and the only question that
# actually matters at the desk is "am I looking at the change I just made?".
VERSION = "0.3.0"


def build_stamp():
    """(text, is_test). What the bottom-left corner says about this build.

    A build is a TEST build unless it is the canonical EsotericOS.exe: every
    staged build is named EsotericOS-next.exe / -wm.exe / and so on, and
    running from source is not a shipped build either. Deriving it from the
    executable's own name means nothing has to be remembered at build time
    and the label cannot lie about what is actually running.
    """
    if getattr(sys, "frozen", False):
        name = os.path.basename(sys.executable)
        is_test = name.lower() != f"{APP_LABEL.lower()}.exe"
        try:
            built = time.strftime(
                "%b %d %H:%M", time.localtime(os.path.getmtime(sys.executable)))
        except OSError:
            built = "unknown"
        label = f"v{VERSION} · {built}"
        if is_test:
            label += f" · {name}"
        return label, is_test
    return f"v{VERSION} · source", True

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

# ---- Astral Compass typography ---------------------------------------------
# Interface face per the brand kit: Inter, falling back to Segoe when Inter is
# not installed. Tk substitutes unknown families SILENTLY with something that
# is not "Inter SemiBold", so the weights are resolved once against the real
# installed-family list the moment a Tk root exists -- before any widget.
FONT_UI = "Segoe UI"
FONT_UI_SEMI = "Segoe UI Semibold"


def _resolve_brand_fonts(root):
    global FONT_UI, FONT_UI_SEMI
    try:
        import tkinter.font as tkfont
        families = set(tkfont.families(root))
        if "Inter" in families:
            FONT_UI = "Inter"
            FONT_UI_SEMI = ("Inter SemiBold" if "Inter SemiBold" in families
                            else "Inter")
    except Exception:  # noqa: BLE001 -- fonts are cosmetic, never fatal
        pass


# ---- Astral Compass palette (brand kit tokens/brand-tokens.json) ----
BG = "#080B10"
PANEL = "#0D0D12"
CARD = "#17161C"
FG = "#E6E6F0"
MUTED = "#A6A1B0"
ACCENT = "#8A5CFF"
ACCENT_DIM = "#5F3DC4"
WARN = "#f5c451"   # amber: connected but idle (portal off)
MON_FILL = "#1D1930"
MON_LINE = "#6B5CA8"
IPAD_FILL = "#5F3DC4"
IPAD_LINE = "#8A5CFF"
IPAD_OFF_FILL = "#17161C"   # iPad box when NOT connected -> muted grey
IPAD_OFF_LINE = "#3E3A4A"
IPAD_IDLE_FILL = "#413615"  # connected but portal OFF -> amber (idle/paused)
IPAD_IDLE_LINE = "#f5c451"
PORTAL = "#B28DFF"       # crossing edges -- violet, so they read as routes
# The build stamp, bottom-left of every pane. Blue because nothing else in
# this palette is blue: it must never be mistaken for a state colour, and a
# version string that reads as "state" is worse than no version string.
BUILD_BLUE = "#5AA9FF"
BUILD_TEST_YELLOW = "#f5c451"   # the kit's functional amber, already in use
HOVER_FILL = "#0B0B11"   # the detail card that appears on mouseover
HOVER_LINE = "#4A4556"
DANGER = "#FF5CA8"
SCRIM = "#05070C"   # near-black overlay behind an in-frame modal
BORDER = "#3A3346"  # card edge for the in-frame modal

# ---- pressed feedback -------------------------------------------------------
# ttk's "active" state is HOVER, not pressed. Every button map in this file used
# only "active", so a click produced no change on the button at all: you pressed
# something, nothing moved, and the only way to learn whether it had registered
# was to watch for a side effect somewhere else in the window. Several actions
# here are threaded and take seconds, so "no side effect yet" and "the click
# missed" looked identical.
#
# These are one step further along each button's own hover ramp, so a press
# always reads as more of what hover already started.
PRESS = "#2A203B"         # TButton        : CARD -> #2d3444 hover -> this
PRESS_ACCENT = "#8A5CFF"  # Accent.TButton : arcane700 -> #764BE2 hover -> this
PRESS_DANGER = "#8A3566"  # Danger.TButton : #53292a -> #6e3335 hover -> this
PRESS_WARN = "#fbe09b"    # Warn.TButton   : #f5c451 -> #f8d276 hover -> this

# ---- the navigation rail's four values --------------------------------------
# The rail items are raw tk.Button, not ttk, so NONE of the style maps above
# reach them and their values have to be spelled out here.
#
# They shipped with activebackground=CARD -- and CARD is also the background of
# the SELECTED item. So hovering an inactive pane painted it exactly the colour
# of the pane you are on, and the only thing left distinguishing "you are here"
# from "your mouse is here" was a 3px accent bar. Hover needs a value of its
# own, distinct from BOTH resting and selected.
#
# Same ordering as every ttk button in this file -- resting, selected, hover,
# pressed, each one step lighter -- so a press reads as more of what hover
# started. Hover being lighter than selected is deliberate and is what the ttk
# ramp already does: selection is stated by the accent bar and the FG label,
# not by being the brightest thing in the column.
RAIL_REST = PANEL         # #1d212b -- an inactive item
RAIL_LIVE = CARD          # #232936 -- the pane you are on
RAIL_HOVER = "#221F2A"    # the same hover step TButton's "active" uses
RAIL_PRESS = PRESS        # #3d4860 -- one further along, as everywhere else

# ---- suppressed register: a global CAUSE vs a local STATE -------------------
# "portal off" and "paired but not connected" were rendered in the SAME amber,
# in the device cards and in the indicator row alike -- the distinction was
# never drawn anywhere in this file. They are not the same kind of fact. A
# stopped portal is a GLOBAL CAUSE: one process is down and every device is
# consequently idle. "Paired, not connected" is a LOCAL STATE of one lane. In
# full-strength amber, three cards shouted about a single stopped process while
# the one control that fixes it looked like every other button in the column.
#
# So the register splits. While the portal is down, nothing in the device area
# uses a full-strength alarm colour -- everything drops to these two suppressed
# tones -- and the only full-strength amber left in the window is the Start
# portal button itself. One alarm, at the cause.
#
# Both stay CHROMATIC on purpose. Rendering a suppressed device in MUTED
# (#8b93a7, a blue-grey) would read as "dead", and a bonded, connected device
# with the portal stopped is not dead: it is waiting for one button.
ACCENT_SUPPRESSED = "#8F7BC4"  # green, drained -- connected, portal not running
WARN_SUPPRESSED = "#a8925f"    # amber, drained -- paired/idle, portal not running


def suppressed(colour, portal_on):
    """THE register rule as ONE function: while the portal is down, a
    full-strength alarm colour becomes its drained twin.

    Every writer of a device-area colour goes through here, or through
    device_state_colour which is itself written in terms of it. That matters
    because the register first shipped as a HABIT applied at each call site,
    and two call sites missed it -- the arrangement canvas kept a private
    fill/outline table and painted full amber on the largest element in the
    window, and the indicator row's broadcast token kept a raw `fg=WARN`. A
    habit at N sites is not a rule; a function is.

    Anything that is not an alarm colour -- MUTED, DANGER, PORTAL -- passes
    through unchanged. DANGER in particular is a real FAULT rather than a
    consequence of the stopped portal, and outranks this rule exactly as it
    outranks device_state_colour.
    """
    if portal_on:
        return colour
    return {ACCENT: ACCENT_SUPPRESSED, WARN: WARN_SUPPRESSED}.get(colour, colour)


# The one suffix that says "…and the reason is global". Kept as a constant
# because the indicator row strips it back off: that row already carries its own
# `portal ● ON / ○ off` token, so repeating the cause in the neighbouring token
# is noise -- there the colour alone carries the register.
PORTAL_OFF_SUFFIX = " — portal off"


def device_state_colour(portal_on, connected, paired):
    """(colour, text) for ONE device's status dot. The whole truth table.

        portal ON  + connected              -> ACCENT             "connected"
        portal ON  + paired, not connected  -> WARN               "paired"
        portal OFF + connected              -> ACCENT_SUPPRESSED  "connected — …"
        portal OFF + paired, not connected  -> WARN_SUPPRESSED    "paired — …"
        not paired                          -> MUTED              "not paired"

    A pure function of three booleans, deliberately: this is the one claim in
    the file that has to be identical in the device cards, in the arrangement
    canvas and in the indicator row, and a truth table that cannot be evaluated
    without building a window is a truth table nobody checks.

    It does NOT cover the two RADIO faults (missing dongle, no radio assigned).
    Those outrank every row here because in both the device's state is
    unknowable rather than merely idle -- see _apply_device_rows.
    """
    if connected:
        return (suppressed(ACCENT, portal_on),
                "connected" + ("" if portal_on else PORTAL_OFF_SUFFIX))
    if paired:
        return (suppressed(WARN, portal_on),
                "paired" + ("" if portal_on else PORTAL_OFF_SUFFIX))
    return (MUTED, "not paired")


def _dim(colour, factor):
    """Scale a #rrggbb toward black.

    The canvas box fills are their own outline at a fixed fraction --
    IPAD_FILL is IPAD_LINE at ~0.50, IPAD_IDLE_FILL is IPAD_IDLE_LINE at ~0.27
    -- so the suppressed boxes are DERIVED from ACCENT_SUPPRESSED /
    WARN_SUPPRESSED by the same ratios rather than eyeballed a second time.
    Two hand-picked palettes for one truth table is how the canvas came to
    disagree with the card below it.
    """
    return "#" + "".join(
        f"{max(0, min(255, int(int(colour[i:i + 2], 16) * factor))):02x}"
        for i in (1, 3, 5))


# ---- the same truth table, at the size of the arrangement canvas ------------
# The canvas rectangle for a device is the card's status dot at 400x300px. It
# used to fold portal_on INTO "live" before the canvas ever saw it -- the caller
# passed `live and portal_on` -- so the canvas could not tell "portal off" from
# "paired but not connected" and rendered BOTH in full-strength WARN, on the
# largest element in the window, directly above a card that said the opposite.
#
# So the canvas does not decide any more. Its state token is looked up FROM the
# colour device_state_colour returns, and its outline IS that colour
# (IPAD_LINE == ACCENT and IPAD_IDLE_LINE == WARN, exactly). There is one truth
# table and the canvas is a rendering of it.
TARGET_STATE_BY_COLOUR = {
    ACCENT: "live",
    WARN: "idle",
    ACCENT_SUPPRESSED: "live-suppressed",
    WARN_SUPPRESSED: "idle-suppressed",
    MUTED: "off",
}
TARGET_BOX_COLOURS = {
    # state             (fill, outline, label)
    "live": (IPAD_FILL, IPAD_LINE, "#D8C8FF"),
    "idle": (IPAD_IDLE_FILL, IPAD_IDLE_LINE, "#ffe9b0"),
    "live-suppressed": (_dim(ACCENT_SUPPRESSED, 0.50), ACCENT_SUPPRESSED,
                        _dim("#D8C8FF", 0.72)),
    "idle-suppressed": (_dim(WARN_SUPPRESSED, 0.27), WARN_SUPPRESSED,
                        _dim("#ffe9b0", 0.72)),
    "off": (IPAD_OFF_FILL, IPAD_OFF_LINE, MUTED),
}


def target_state_name(portal_on, connected, paired):
    """The arrangement canvas's state token for ONE device.

    Derived from device_state_colour rather than restated, so the box and the
    dot cannot disagree: a fifth row in that truth table is a KeyError here at
    the moment it is added, not a colour that quietly diverges.
    """
    return TARGET_STATE_BY_COLOUR[
        device_state_colour(portal_on, connected, paired)[0]]


def broadcast_token(adv_state, adv_error, portal_on):
    """(text, colour) for the indicator row's broadcast token while the daemon
    is NOT confirmed advertising -- or None when it has nothing to say.

    Pure, and out here beside device_state_colour, for the same reason: this
    token was the last raw full-strength `fg=WARN` in the file, and a claim
    buried in a 250-line _apply_poll is a claim no test can reach without
    building the whole window.

    A transitional state is a consequence of the portal like every other idle
    thing in the window, so it takes the suppressed register. An advertising
    ERROR is a fault of its own and stays DANGER.
    """
    if adv_state in ("starting", "stopping"):
        return f"broadcast {adv_state}...", suppressed(WARN, portal_on)
    if adv_error:
        return "broadcast error", DANGER
    return None


def broadcast_names(names):
    """The indicator row's name list for N broadcasting devices, kept SHORT.

    This is the widest token in the row and the last one packed (see
    INDICATOR_ORDER), so what it spends comes straight out of the tokens behind
    it. Three names joined with " + " measure 304px of a 908px cavity -- a
    third of the row -- to describe a state that lasts seconds.

    Two names still read as names. Beyond that the count IS the honest summary:
    the row's job is "is anything beaconing", and which machines is a question
    the Devices pane answers per device, one rail click away.
    """
    names = list(names)
    if len(names) <= 2:
        return " + ".join(names)
    return f"{len(names)} devices"


# ---- the indicator row: PACK ORDER IS DROP ORDER ----------------------------
# The row does not scroll and does not wrap, and Tk's packer does not shrink an
# overflowing child: it hands each slave the cavity it asks for, in pack order,
# and gives what is left to the next one. The tail of the row is therefore
# CLIPPED and then dropped outright -- silently, with no scrollbar, no ellipsis
# and nothing anywhere to say a token went missing.
#
# MEASURED, not reasoned. At the app's minimum width the row's cavity is
# 940 - 2*16 = 908px, and the widest honest row on a three-device desk asks for
# 925px. Under the historical order -- vm, ipad, devices, portal, audio, bcast,
# admin -- the token that paid the 17px was `admin`: it was allocated 77 of the
# 94px it needs and rendered cut off.
#
# That is the worst possible casualty. Per is_elevated's docstring the admin
# lamp is the ONLY surface in this app that can explain a silently dead mouse:
# under Windows UIPI a non-elevated process's low-level hooks receive NOTHING
# while an elevated window has focus, the hooks still report as successfully
# installed, and no log, no exception and no other token says so. It cost days
# to find the first time.
#
# The row is packed in PRIORITY order instead, worst-to-lose first, and the
# packer's own rule then does the right thing on its own:
#
#   admin   non-negotiable, so first. Its text is EMPTY whenever the app is
#           elevated -- the normal case -- so leading with it costs the row its
#           14px of padding and nothing else.
#   vm      48px, and while the VM is down every other token is a consequence
#           of it.
#   ipad    the first device's own lane. The only token here that speaks for a
#           single machine, and it has no second home anywhere in the header.
#   portal  the process the user starts and stops.
#   mac     `devices N/M` -- an aggregate whose per-device detail is one rail
#           click away in the Devices pane.
#   audio   a convenience readout; losing it costs nothing that is not audible.
#   bcast   LAST, deliberately. Widest token in the row and the most transient
#           (a broadcast is seconds long), so it is the one that should yield.
#           broadcast_names above shortens it as well, which is what takes the
#           full three-device row from 925px to 799px -- inside the cavity.
#
# The invariant, asserted by MEASURING in test_panes.py rather than by reading
# the order back: at the app's minimum width every token in
# INDICATOR_MUST_SURVIVE is still allocated its full width, whatever the rest
# of the row does.
INDICATOR_ORDER = ("admin", "vm", "ipad", "portal", "mac", "audio", "bcast")
INDICATOR_MUST_SURVIVE = ("admin", "vm", "ipad", "portal")


# ---- N devices, honestly ----------------------------------------------------
# What follows replaces a two-device status model that had been structurally
# dead for as long as it existed. `_poll` opened with `mac_st = None`, never
# reassigned it, and passed it into `_apply_poll`, where four surfaces read it:
# the broadcast token, the "Mac ● up / ○ down" half of the System control line,
# the compact-mode Mac dot, and the call-to-action line. `mac_st is not None`
# is therefore False on every tick, so the window reported Doug's Managed Mac
# as DOWN while it was connected -- not intermittently, always.
#
# The app had already outgrown that model: `self._dev_status` carries a daemon
# status dict for EVERY configured device. The status rendering simply never
# followed. These two functions are where it follows, and they are module-level
# and pure for the same reason device_state_colour is: a claim buried inside a
# 250-line _apply_poll is a claim no test can reach without building a window,
# and _apply_poll is precisely the method whose exceptions get swallowed.


def device_reach_state(portal_on, reachable, connected, paired, vm_up=True):
    """(colour, text) for ONE device's card, with daemon REACHABILITY in it.

    A card used to print "not paired" whether the device was genuinely
    unpaired or its daemon simply did not answer. Those are two completely
    different situations -- one is a thing you fix by pairing, the other is a
    thing you fix by starting the VM or the lane -- and the only surface in the
    window that distinguished them was a global line about a singleton device
    that no longer exists.

    This WRAPS device_state_colour, it does not restate it: when the daemon
    answers, the returned pair is device_state_colour's, unchanged. There is
    still exactly one truth table for connected/paired.

    Unreachable is not a row in that table on purpose. It is not a state of the
    lane, it is not KNOWING the state of the lane, so it is expressed as a
    qualifier over the top:

        unreachable + a known bond -> suppressed(WARN)  "paired · daemon
                                      unreachable"      -- a lane we expected
                                      to find is gone; that is worth amber, and
                                      it takes the register like everything
                                      else in the device area
        unreachable + no bond      -> MUTED             "daemon unreachable"
                                      -- nothing was expected, so nothing is
                                      alarming; only the WORD was ever wrong

    Both go through suppressed(), so an unreachable device while the portal is
    stopped still reads in the drained register rather than shouting alongside
    the one control that fixes it.

    `vm_up` is what makes "unreachable" mean anything. With the VM DOWN, no
    device's daemon can answer -- that is not N faults, it is one stopped
    process -- and the words above turned an ordinary stopped desk into three
    red-flag cards: "paired · daemon unreachable" on every one of them, for a
    cause none of them owns. The register was already right (they were amber
    suppressed); the SENTENCE was the alarm. So while the VM is down this
    declines to make the claim at all and falls back to the ordinary state
    table, and the one surface that owns the cause -- the readiness banner,
    which says "○  Stopped" -- states it once. Same rule W3 established for the
    portal: one alarm, at the cause.

    An unanswered daemon while the VM IS up is the opposite case: something
    that should be there is not, it belongs to that lane alone, and it keeps
    its amber and its sentence.
    """
    if not reachable and vm_up:
        if paired:
            return (suppressed(WARN, portal_on),
                    "paired · daemon unreachable"
                    + ("" if portal_on else PORTAL_OFF_SUFFIX))
        return (suppressed(MUTED, portal_on), "daemon unreachable")
    return device_state_colour(portal_on, connected, paired)


def device_status_rollup(devices, dev_status):
    """Roll N per-device daemon statuses up into the counts the GLOBAL surfaces
    need. Pure, so the aggregate can be checked without a window.

    Returns a dict:

        total       how many devices are configured
        reachable   how many answered their daemon this tick
        live        how many have the keyboard/mouse subscribed
        live_names  those devices' names, in configured order
        advertising names of the devices confirmed BROADCASTING by their own
                    daemon -- never a UI guess
        adv_state   the first transitional advertising state reported by any
                    device ("starting"/"stopping"), else "off"
        adv_error   the first advertising error reported by any device, else ""

    Every global surface that used to reason about "the iPad" and "the Mac"
    reads this instead. Where a surface genuinely wants an aggregate it now
    says so in words -- "devices 2/3", "2 devices connected" -- rather than
    naming one machine and silently meaning another.

    The membership test is isinstance(dict), not `is not None`. A daemon status
    is whatever json.loads made of the bytes on the socket, and JSON's top level
    is legally a scalar or an array: one `5\\n` or `[]\\n` from a wrong process on
    that port, and `status.get` is an AttributeError raised at the very TOP of
    _apply_poll, which aborts every status surface below it inside the closure
    _drain_ui swallows. That is the silent half-frozen window again, from a
    stray byte. target_daemon_status normalises at the socket as well; this is
    the second half of the same guard, because _dev_status is a plain dict that
    anything can seed.
    """
    live_names, advertising = [], []
    reachable = 0
    adv_state, adv_error = "off", ""
    for device in devices:
        status = dev_status.get(device["id"])
        if not isinstance(status, dict):
            continue
        reachable += 1
        name = device.get("name", device["id"])
        if status.get("kbd_subscribed"):
            live_names.append(name)
        if status.get("advertising"):
            advertising.append(name)
        if adv_state == "off":
            adv_state = status.get("advertising_state", "off") or "off"
        if not adv_error:
            adv_error = status.get("advertising_error", "") or ""
    return {
        "total": len(devices),
        "reachable": reachable,
        "live": len(live_names),
        "live_names": live_names,
        "advertising": advertising,
        "adv_state": adv_state,
        "adv_error": adv_error,
    }


# ---- the device card, declared once -----------------------------------------
# _build_device_row writes this dict and _apply_device_rows indexes it, three
# thousand lines apart. When they drift the failure is SILENT: _poll marshals
# through ui(), _drain_ui swallows every exception, so a KeyError aborts
# _apply_poll mid-function and the status dots, the readiness banner and the
# headphones line simply freeze with nothing in the console. Both sides are
# driven off these two constants so they cannot drift.
DEVICE_ROW_KEYS = ("dot", "name", "radio", "buttons", "more")

# (key, resting label, in-flight label). The four CONNECTION verbs -- never
# collapsed into one relabelling button: two of them are live at once in both
# the paired-idle and the live state, so there is no single correct verb, and a
# button that re-aims under the cursor every three seconds with Unpair in the
# rotation is a trap rather than a saving.
DEVICE_VERB_SPEC = (
    ("pair", "Pair", "Pairing…"),
    ("connect", "Connect", "Connecting…"),
    ("disconnect", "Disconnect", "Disconnecting…"),
    ("unpair", "Unpair", "Unpairing…"),
)
DEVICE_VERBS = tuple(key for key, _label, _busy in DEVICE_VERB_SPEC)


def _require_verb_coverage(mapping, what):
    """Prove a verb-keyed table covers exactly DEVICE_VERBS. Returns it.

    Two tables in this file are keyed by verb and then indexed by the
    DEVICE_VERB_SPEC loop variable -- the gate below and the card's command
    table. A fifth entry in the spec with no fifth entry in one of them is a
    KeyError raised INSIDE _apply_device_rows, and _drain_ui swallows every
    exception: the status dots, the readiness banner and the headphones line
    simply freeze with nothing in the console and no traceback anywhere.

    So both tables are module-level and checked HERE, at import. A gap is a
    startup error that names the missing verb, in the file that declared it,
    before any window exists to freeze.
    """
    missing = [key for key in DEVICE_VERBS if key not in mapping]
    extra = [key for key in mapping if key not in DEVICE_VERBS]
    if missing or extra:
        raise KeyError(
            f"{what} does not cover DEVICE_VERBS: missing {missing}, "
            f"unexpected {extra}")
    return mapping


# Which App method each verb clicks through to, by NAME so the table can sit
# beside the spec instead of three thousand lines away inside _build_device_row.
DEVICE_VERB_HANDLERS = _require_verb_coverage({
    "pair": "_pair_device",
    "connect": "_connect_device",
    "disconnect": "_disconnect_device",
    "unpair": "_unpair_device",
}, "DEVICE_VERB_HANDLERS")

# Which verbs are OFFERED, as pure predicates over one device's facts. Out here
# rather than inline in _apply_device_rows so the coverage check above runs at
# import; the facts themselves are still gathered per device, per tick.
#
#   usable  the device has a radio and that radio is actually present
#   vm      the VM answers (self._vm_reachable)
#   up      this device's own HID daemon answered its status probe
#   busy    inflight or broadcasting
DEVICE_VERB_GATES = _require_verb_coverage({
    # NOT gated on `up`: pairing is what brings the lane into existence, so
    # requiring its daemon first is a deadlock.
    "pair": lambda f: (f["usable"] and f["vm"] and not f["busy"]
                       and not f["live"]),
    "connect": lambda f: (f["usable"] and f["up"] and not f["busy"]
                          and f["paired"] and not f["live"]),
    # Disconnect doubles as CANCEL for an in-flight attempt.
    "disconnect": lambda f: f["live"] or f["busy"],
    "unpair": lambda f: (f["usable"] and f["up"] and not f["busy"]
                         and f["paired"]),
}, "DEVICE_VERB_GATES")


# How each verb reads in a MENU, where there is room to say what it costs.
# Coverage-checked like the gate and handler tables: a fifth verb in the spec
# with no entry here would be a KeyError raised while a menu is being posted.
#
# The waits are MEASURED off the handlers, not estimated. Pair and Connect run
# the same worker, whose guest command is ssh_guest(timeout=55) and which may
# start the VM first; Pair asks with dark_confirm, Connect does not.
# Disconnect is set_target_advertising (a daemon command with an 8s timeout,
# because BlueZ completes advertising changes asynchronously) plus a 2s
# disconnect. Unpair is those two plus forget-hid, ssh_guest(timeout=25).
#
# None of them says "restarts input": portal_signature is taken over CONFIG
# fields, and no verb here writes config. The display entries above them in the
# same menu DO cost eight seconds and say so.
# Every number here is the sum of the timeouts on that verb's OWN path, not the
# largest single call in it. A label naming one component understates the wait
# by whatever the rest costs, which is the same dishonesty as not labelling it.
# Derived, and re-derived by test_device_verbs so they cannot drift:
#
#   pair / connect  both run _pair_device_attempt:
#                     ssh_guest(pair command, timeout=55)                    55s
#                     set_target_advertising x4 -> target_daemon_cmd(8s each) 32s
#                       (one to start broadcasting, three on the various
#                        cleanup and failure exits -- not all on one path, but
#                        the label must cover the worst, not the typical)
#                                                                    total ~87s
#     There is also a VM-start branch, 45 x (ssh timeout=5 + 2s wait) = ~315s.
#     It is UNREACHABLE from an offered verb: pair's gate requires f["vm"] and
#     connect's requires f["up"], and both mean the VM is already answering. Do
#     not fold it into the label, and do not "correct" the label to include it.
#
#   disconnect      set_target_advertising -> target_daemon_cmd(timeout=8)    8s
#                   target_daemon_cmd(disconnect, default timeout=2)          2s
#                                                                    total ~10s
#
#   unpair          set_target_advertising -> target_daemon_cmd(timeout=8)    8s
#                   target_daemon_cmd(disconnect, default timeout=2)          2s
#                   ssh_guest(forget-hid, timeout=25)                        25s
#                                                                    total ~35s
DEVICE_VERB_MENU_SUFFIX = _require_verb_coverage({
    "pair": "{verb}…   (confirms first — up to ~125s before it broadcasts)",
    "connect": "{verb}   (up to ~125s)",
    "disconnect": "{verb}   (up to ~10s)",
    "unpair": "{verb}…   (confirms first — up to ~35s)",
}, "DEVICE_VERB_MENU_SUFFIX")

# Disconnect's gate is `live or busy`, so mid-pair it is the CANCEL -- and
# "Disconnect" there offers to end a connection that does not exist yet.
DEVICE_VERB_CANCEL_LABEL = "Cancel pairing   (stops the broadcast, up to ~10s)"


def device_verb_offer(facts):
    """Which verbs are LIVE for one device, as {verb: bool}. THE ONLY CALLER
    OF DEVICE_VERB_GATES, and therefore the only place the answer is decided.

    TWO surfaces offer these four verbs now: the device card's row of buttons
    and the arrangement canvas's right-click menu. That is exactly the shape
    that has already failed once in this app -- a second surface for the same
    action, carrying its own copy of the state, drifting from the first. See
    the comment on _build_device_row about the five permanently-enabled
    buttons: they were enabled because nothing re-derived them, and the row
    that held them looked authoritative the whole time.

    So neither surface owns a predicate. Both call THIS, with facts from the
    single producer (App._device_verb_facts), and the menu therefore cannot
    offer Unpair on a lane whose card has Unpair greyed out. Adding a third
    surface later costs two calls and no new judgement.

    Pure and module-level so a test can drive it through every device state
    without a window.
    """
    return {key: bool(DEVICE_VERB_GATES[key](facts)) for key in DEVICE_VERBS}


# ---- pending: the OTHER half of "a button must react" -----------------------
# The pressed state above covers the instant of the click. This covers the wait
# after it: 26 actions in this file run on a worker thread and take seconds, and
# until now exactly two of them (the VM button) said so, ad-hoc, by writing
# config(text="Starting VM..."). Everything else looked identical to a click
# that had missed.
#
# The parked label lives in a module-level registry keyed by the widget's Tk
# path rather than on the widget, because every OTHER writer of that button's
# text has to be able to ask "is this button mid-flight?" without holding a
# reference to whoever started the work -- _apply_poll rewrites the VM button's
# label on every 3-second tick, and would otherwise paint straight over the wait.
_BUSY_IDLE = {}   # Tk widget path -> the label to restore when the work ends


def paint_button_busy(button, label):
    """The busy LOOK, and nothing else: present participle, disabled.

    Separate from the parking below because the four per-device verbs are
    destroyed and rebuilt by _rebuild_device_rows and re-derived from
    _dev_state on every tick. Parking a label for a widget that will not
    survive the wait is how a rebuilt button inherits a stale "Pairing…".
    """
    button.config(text=label)
    button.state(["disabled"])


def set_button_busy(button, label):
    """Park the resting label, then paint the busy one. UI THREAD ONLY --
    App.busy() is the entry point that is safe from a worker."""
    key = str(button)
    if key not in _BUSY_IDLE:
        _BUSY_IDLE[key] = button.cget("text")
    paint_button_busy(button, label)


def clear_button_busy(button):
    """Restore the parked label and re-enable. Idempotent.

    Both halves are conditional on this helper having actually PARKED
    something. The re-enable used to be unconditional, so calling it on a
    button nothing had parked -- a doubled `done()` out of two nested
    finallys, or a device verb the gate deliberately holds disabled -- silently
    handed that button back to the user. A helper that restores state it never
    took is not idempotent, it is a second writer.
    """
    text = _BUSY_IDLE.pop(str(button), None)
    if text is None:
        return
    button.config(text=text)
    button.state(["!disabled"])


def button_is_busy(button):
    """True while a resting label is parked for this button."""
    return str(button) in _BUSY_IDLE


def rebase_button_busy(button, label):
    """Change what a busy button will be restored TO, without disturbing the
    busy label it is showing now. Returns False when it is not busy, which is
    the caller's cue to write the label directly.

    This exists because a background job can learn a better resting label WHILE
    the button is waiting -- "Repair radios" becomes "Repair 2 radios" halfway
    through a repair -- and the alternative is either clobbering the busy label
    or restoring a stale one.
    """
    if str(button) in _BUSY_IDLE:
        _BUSY_IDLE[str(button)] = label
        return True
    return False

# One look for every popup menu in the app. disabledforeground is not decoration
# here: a menu's title line and its read-only Windows facts are disabled ENTRIES,
# and Tk's default disabled grey is very nearly invisible on this background.
MENU_STYLE = {
    "bg": CARD, "fg": FG, "activebackground": ACCENT_DIM,
    "activeforeground": "#F1EBFF", "disabledforeground": MUTED, "bd": 0,
}

# ---- what a screen may be offered, per KIND of screen ----------------------
# The iPad's res_w/res_h hold POINTS, not pixels: the live config's ipad-main is
# 1080x810, byte-identical to IPAD_PRESETS["iPad 10.2\""], and every pointer
# distance on that HID lane is computed from those numbers. Offering a generic
# 3840x2160 on that rectangle would not change any screen -- it would silently
# rescale the whole iPad lane. So the resolution menu is built per display KIND
# and an iPad is offered iPad geometries by name, which is also where the
# deleted "iPad model" combobox's one job now lives.
DESKTOP_PRESETS = (
    ("1280 × 720", (1280, 720)),
    ("1366 × 768", (1366, 768)),
    ("1440 × 900", (1440, 900)),
    ("1600 × 900", (1600, 900)),
    ("1680 × 1050", (1680, 1050)),
    ("1920 × 1080", (1920, 1080)),
    ("1920 × 1200", (1920, 1200)),
    ("2560 × 1440", (2560, 1440)),
    ("2560 × 1600", (2560, 1600)),
    ("3440 × 1440", (3440, 1440)),
    ("3840 × 2160", (3840, 2160)),
)
REFRESH_PRESETS = (30, 60, 75, 90, 100, 120, 144, 165, 240)


def display_kind(device, display):
    """"ipad" or "desktop" -- which resolution family this screen belongs to.

    Read from three independent tells, because any one of them can be edited by
    the user: the device id, the device name, and whether the stored geometry is
    already one of the known iPad point sizes.
    """
    device = device or {}
    display = display or {}
    if str(device.get("id", "")).lower() == "ipad":
        return "ipad"
    if "ipad" in str(device.get("name", "")).lower():
        return "ipad"
    size = (int(display.get("res_w", 0)), int(display.get("res_h", 0)))
    if size in {tuple(v) for v in IPAD_PRESETS.values()}:
        return "ipad"
    return "desktop"


def hz_label(value):
    """"144 Hz" for a real reading, "" for a number nobody has."""
    if not value:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{int(number) if number.is_integer() else number} Hz"


def short_monitor_name(name):
    """\\\\.\\DISPLAY4 -> DISPLAY4."""
    return str(name).replace("\\\\.\\", "").replace("\\", "")


def describe_monitor_refresh(report):
    """What a Windows re-read actually changed, in one line.

    Merging without saying what moved is how a desk gets rewritten under
    someone -- the point of the button is that Windows is the authority on four
    fields and on nothing else."""
    parts = []
    for name in report.get("added", []):
        parts.append(f"added {short_monitor_name(name)}")
    for name in report.get("removed", []):
        parts.append(f"removed {short_monitor_name(name)}")
    for name, old, new in report.get("resolution", []):
        parts.append(f"{short_monitor_name(name)} {old} → {new}")
    for name, _old, new in report.get("refresh", []):
        parts.append(f"{short_monitor_name(name)} {hz_label(new)}")
    for name in report.get("primary", []):
        parts.append(f"{short_monitor_name(name)} is now primary")
    return ", ".join(parts) if parts else "nothing changed"

# ---- vertical rhythm --------------------------------------------------------
# Every vertical pad in the left column was chosen locally, one widget at a
# time, over many sessions. Each choice was reasonable on its own; together they
# are how a window ends up two monitors tall. These four are the only vertical
# steps the column is allowed to spend.
PAD_XS = 2
PAD_SM = 4
PAD_MD = 6
PAD_LG = 12


def _section(parent, title, pady=(PAD_MD, PAD_XS), padx=16):
    """A titled block in the left column. Returns the BODY frame to fill.

    This is what the left column's three ttk.LabelFrames became. Measured on
    this machine, at this theme, with an identical 50px body in both arms:

        ttk.LabelFrame(text=..., padding=7/8)   85-87px   -> 35-37px of chrome
        _section(...)                           69px      -> 19px of chrome

    A LabelFrame spends that before it holds anything: a label band, a 1px
    border on all four sides, and 7-8px of internal padding on all four sides.
    Three of them stack in the one column that binds this window's height, so
    the column was paying ~108px to say three words. A bold muted caption over
    a hairline rule says the same three words for ~57px.

    The right column's two LabelFrames (Radio options, Audio & status) are
    deliberately LEFT ALONE. That column does not bind the window's height, so
    changing them would buy nothing and cost a visual inconsistency for it.
    The ttk TLabelframe theming in App._theme therefore now serves only BtPanel
    -- it is still needed, just no longer by the left column.

    `pady` is passed through rather than fixed so the vertical rhythm the
    panels already had survives the swap exactly: the Devices block sat at
    (PAD_XS, PAD_XS), the two below it at (PAD_MD, PAD_XS).
    """
    block = tk.Frame(parent, bg=BG)
    block.pack(fill="x", padx=padx, pady=pady)
    # bd=0/padx=0/pady=0: a tk.Label's DEFAULTS are a 1px border and 1px of
    # internal pad on each side, which is 4px of nothing per caption.
    tk.Label(block, text=title, bg=BG, fg=MUTED, font=(FONT_UI, 9, "bold"),
             anchor="w", bd=0, padx=0, pady=0).pack(fill="x")
    tk.Frame(block, bg="#221F2A", height=1).pack(fill="x", pady=(1, PAD_XS))
    body = tk.Frame(block, bg=BG)
    body.pack(fill="x")
    return body


# The wraplength a prose label starts at, before it has ever been mapped and
# can measure itself. It is a FALLBACK and nothing else: bind_wraplength
# replaces it with the label's real width the first time the widget is
# configured, and every time it changes after that.
#
# The literals it replaces were not fallbacks. `wraplength=480` in the
# Bluetooth-radio explainer, and 500/470 in BtPanel, were fixed numbers chosen
# against a 1120px window; at the app's own minsize the column is ~460px wide,
# so every one of them silently spent an extra line. A wraplength literal is a
# height bug at any width its author did not have.
WRAP_FALLBACK = 460


def fit_wraplength(label, width, floor=240):
    """Apply ONE measured width to a prose label. Returns True if it changed.

    Out here as a named function rather than buried in the closure below so the
    rule can be driven directly: a binding that is never exercised is a binding
    nobody has checked.

    `floor` is not decoration. Tk delivers <Configure> with width=1 for a
    widget that has never been mapped -- which is exactly what happens under a
    withdrawn root in the test suite, and briefly during startup -- and a
    wraplength of 1 renders one word per line and multiplies the label's
    height. Below the floor this declines to act and the fallback stands.
    """
    if width < floor:
        return False
    try:
        current = int(str(label.cget("wraplength")))
    except Exception:  # noqa: BLE001
        current = -1
    if current == width:
        return False
    label.config(wraplength=width)
    return True


# A default tk.Label's own chrome, per axis: 1px of border plus 1px of internal
# pad on EACH side (see _section, which strips exactly this to stop paying 4px
# per caption). <Configure> reports the widget's OUTER width, so a wraplength
# set to that number is 4px wider than the box the text is actually laid out in
# and the last line of a paragraph can clip. Named rather than written 4,
# because it is a fact about the widget and not a taste.
LABEL_CHROME_W = 4


def bind_wraplength(label, inset=LABEL_CHROME_W, floor=240):
    """Make a prose label wrap to its OWN width, for as long as it lives.

    Setting wraplength cannot change the label's WIDTH (these all pack
    fill="x"), so there is no Configure feedback loop; the no-op guard inside
    fit_wraplength is there to keep a 3-second-tick-adjacent path cheap, not to
    break a cycle.

    `inset` defaults to the label's own chrome rather than to 0. Every call
    site in this file is a default-bordered tk.Label, so 0 asked the text to
    wrap at four pixels wider than the space it had.
    """
    label.config(wraplength=WRAP_FALLBACK)
    label.bind("<Configure>",
               lambda e: fit_wraplength(label, e.width - inset, floor),
               add="+")
    return label

# Height budget for the whole window's content.
#
# NOTHING in this file used to set a window height. geometry() declared one at
# import and minsize() permitted a window far SHORTER than the left column
# actually needs -- so at the app's own default size "System control" and
# "Bluetooth radio" packed silently off the bottom. There is no scrolling
# anywhere by design, so there was no scrollbar, no clipping indicator and no
# way to learn those panels existed. App.__init__ now measures the built content
# and derives both the opening height and the minimum from it.
#
# This ceiling is a TRIPWIRE, not a clamp: content above it is reported, never
# trimmed. Clamping would re-create the exact starvation described above.
LAYOUT_MAX_CONTENT_H = 1600


def work_area_height(default=1080):
    """Usable vertical space on the primary monitor -- the screen minus the
    taskbar. winfo_screenheight() is the wrong number here: it counts pixels the
    taskbar already owns, so sizing to it puts the last panel under the clock."""
    try:
        import ctypes
        import ctypes.wintypes as wt
        rect = wt.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(
                0x0030, 0, ctypes.byref(rect), 0):   # SPI_GETWORKAREA
            return max(400, rect.bottom - rect.top)
    except Exception:  # noqa: BLE001
        pass
    return default


def window_height_plan(content_h, avail_h=None, ceiling=LAYOUT_MAX_CONTENT_H):
    """Turn a MEASURED content height into the window's geometry and minsize.

    Returns (geometry_h, minsize_h, over_budget, clipped).

    minsize_h follows the content, NOT a guessed constant: a minsize shorter
    than the content is exactly the silent packer-starvation this exists to
    close -- Tk drops the last-packed panels without a word, and with no
    scrolling anywhere there is no scrollbar to hint they went.

    But the content is not allowed to outrank the physical screen. Doug's desk
    changed from a 1440p primary to three 1080p panels between one launch and
    the next; the same content that fitted comfortably became 223px taller than
    the display, and a minsize equal to it meant the window could not be shrunk
    to fit AT ALL -- the bottom panels were unreachable by any means. A window
    you cannot fit on your screen is worse than one whose overflow is reported.

    So the screen wins, and when it does we say so out loud rather than let the
    packer eat panels quietly. `clipped` is that signal, and it is the app's cue
    that the CONTENT has to get shorter -- not that the warning should be muted.
    """
    content_h = max(1, int(content_h))
    if avail_h is None:
        avail_h = work_area_height()
    avail_h = max(400, int(avail_h))
    fitted = min(content_h, avail_h)
    return fitted, fitted, content_h > ceiling, content_h > avail_h


# ---- one pane at a time -----------------------------------------------------
# Doug, 2 August: *"the app is still showing too much information at once i
# think -- how can we get this thing to be a reasonable size on 1080p? it
# demands too much -- consider InputDirector interface for ideas"*
#
# Input Director's shape, and the reason it fits on a laptop: a narrow labelled
# rail down the left edge, ONE pane visible beside it, and a small persistent
# header. The window's height is then the TALLEST PANE rather than the SUM of
# every section -- which is exactly what this window had become. Two columns,
# every panel packed at once, 1136 x 1054 measured, on a 1080p work area of
# 1040.
#
# THE PANES ARE BUILT ONCE, ALL OF THEM, and hidden with pack_forget. None is
# built lazily and none is ever destroyed or reparented -- Tk has no reparent
# operation, and two of these are SERVICE OBJECTS as much as they are panels:
#
#   * BtPanel._radios gates the radio-missing check on every device card,
#     BtPanel._connected_names feeds the headphones line, and _poll calls
#     bt_panel.refresh(quiet=True) on every fifth tick whether or not the
#     Bluetooth pane is the one showing.
#   * MultiArrangeCanvas owns the desk config. devices() alone is read six
#     times a tick, and portal_signature, every device lookup, the hover card,
#     the right-click menus and _fit_height all go through the same object.
#
# A pane that existed only while you were looking at it would take those with
# it. pack_forget leaves the widget alive and configurable; that is the whole
# mechanism here.
PANE_SPEC = (
    ("desk",      "▦  Desk"),
    ("devices",   "▣  Devices"),
    ("bluetooth", "🎧  Bluetooth"),
    ("system",    "⏻  System"),
    ("console",   "▸  Console"),
)
PANE_KEYS = tuple(key for key, _label in PANE_SPEC)
DEFAULT_PANE = "desk"

# The panes whose CONTENT is meant to grow when the window is dragged taller.
#
# Four of the five are a fixed stack of controls, and surplus height under a
# stack of buttons is dead space -- which is the entire reason `bridge` carries
# a designated spacer. The console is not a stack of controls, it is a LOG:
# vertical room is the only thing you go there for, and it was the one pane in
# the app that could not take any. cwrap packed expand=True inside it, but the
# pane itself was packed expand=False, so every pixel of a dragged-taller window
# went to the spacer while the log stayed the height it opened at.
#
# W1's invariant is NOT "the spacer expands". It is "exactly ONE child of
# `bridge` expands", so the surplus has a single named destination and cannot be
# split between two panels that each distort a little to absorb it. select_pane
# keeps that count at one and moves WHICH child it is: while a pane in this
# tuple is showing, that pane is the designated expanding child and the spacer
# stands down; otherwise the spacer is the sponge exactly as before.
PANE_EXPANDS = ("console",)

# The shortest this window may be made, whatever the visible pane asks for.
#
# Not taste, and not a re-introduction of the guessed minsize this file spent a
# wave deleting: FrameModal._fit clamps its card to host.winfo_height() - 40,
# and the tallest dialog in the file -- MacDisplayEditor -- asks for 900x420.
# The Devices pane is the SHORTEST pane in the app (~180px of content) and its
# own card menu is what opens that dialog. Without a floor, selecting the
# smallest pane would silently cut the buttons off the biggest modal it can
# raise.
PANE_MIN_WINDOW_H = 520


def pane_height_floor(avail_h):
    """PANE_MIN_WINDOW_H, never taller than the screen it has to live on."""
    return max(1, min(PANE_MIN_WINDOW_H, int(avail_h)))


def pane_window_plan(content_h, avail_h=None, floor=None):
    """window_height_plan for ONE visible pane: the same policy, plus the floor.

    Returns (geometry_h, minsize_h, over_budget, clipped).

    minsize has to come DOWN when a shorter pane is selected, or the window
    cannot be shrunk to it: a minsize left at the Bluetooth pane's height would
    pin the Devices pane inside a window with 700px of nothing under it, which
    is the same "cannot be resized to fit" failure window_height_plan exists to
    close, moved one level up.
    """
    if avail_h is None:
        avail_h = work_area_height()
    avail_h = max(400, int(avail_h))
    geom_h, min_h, over, clipped = window_height_plan(content_h, avail_h)
    low = (pane_height_floor(avail_h) if floor is None
           else max(1, min(int(floor), avail_h)))
    return max(geom_h, low), max(min_h, low), over, clipped


def load_last_pane(default=DEFAULT_PANE):
    """The pane the app opens on, persisted across restarts.

    An unknown, missing or corrupt value falls back to the default rather than
    raising. This runs inside App.__init__, before there is any surface to
    report a fault on, and a settings file edited by hand must never be able to
    stop the app starting.
    """
    try:
        value = load_setting("last_pane", None)
    except Exception:  # noqa: BLE001
        return default
    return value if value in PANE_KEYS else default


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
             font=(FONT_UI_SEMI, 13)).pack(anchor="w")
    if message:
        try:  # wrap to the window so a small/compact window still fits
            wl = max(240, min(400, top.winfo_width() - 80))
        except tk.TclError:
            wl = 360
        tk.Label(inner, text=message, bg=BG, fg=MUTED, justify="left",
                 wraplength=wl, font=(FONT_UI, 10)).pack(anchor="w",
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


# Mouse sensitivity has notches rather than a free slide. A continuous scale
# stored 0.747 while the readout said 0.75, so the number on screen was not the
# number in the file — and a value tuned by feel could never be typed back.
#
# The spacing is deliberately NOT uniform. Everything usable sits between 0.55
# and 1.0, so that band gets 0.05 steps; below and above it the steps open out,
# because the difference between 2.5 and 2.75 is not a decision anyone makes.
SENSITIVITY_NOTCHES = (
    0.25, 0.5,
    0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0,
    1.25, 1.5, 1.75, 2.0, 2.5, 3.0,
)


def nearest_notch_index(value, notches=SENSITIVITY_NOTCHES):
    """Which notch a value sits closest to. Ties go to the LOWER notch, so a
    value exactly between two never drifts a device faster on its own."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 1.0
    best = 0
    for i in range(1, len(notches)):
        if abs(notches[i] - value) < abs(notches[best] - value) - 1e-12:
            best = i
    return best


def snap_sensitivity(value, notches=SENSITIVITY_NOTCHES):
    """The notch value a slider lands on. Used when the handle MOVES — never
    when a dialog opens, so a value already tuned by feel is left alone."""
    return notches[nearest_notch_index(value, notches)]


def format_sensitivity(value):
    """Show the number that is actually stored. Two places for a notch, three
    for a legacy value that predates them — 0.747 must not read as 0.75."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "1.00"
    return (f"{value:.2f}" if abs(value - round(value, 2)) < 1e-9
            else f"{value:.3f}")


def notched_scale(parent, var, notches=SENSITIVITY_NOTCHES, **kw):
    """A slider that can only come to rest ON a notch.

    The scale runs in notch-INDEX space rather than value space, so there is no
    position between two notches for the handle to stop at. `var` holds the
    real value and is written only when the handle moves — building the widget
    positions the handle at the nearest notch and leaves `var` exactly as it
    was found, so opening a dialog never rewrites a device tuned by feel."""
    pos = tk.DoubleVar(value=float(nearest_notch_index(var.get(), notches)))
    snapping = []

    def on_drag(raw):
        if snapping:                # our own snap-back, not the user's hand
            return
        index = max(0, min(len(notches) - 1, int(round(float(raw)))))
        snapping.append(True)
        try:
            pos.set(float(index))   # the handle jumps to the notch
        finally:
            snapping.pop()
        var.set(notches[index])

    scale = ttk.Scale(parent, from_=0, to=len(notches) - 1, variable=pos,
                      orient="horizontal", command=on_drag, **kw)
    scale.notch_position = pos      # the test needs to drive it
    scale.notches = notches
    return scale


PROFILE_DIR = os.path.join(ROOT, "profiles")

# ---------------------------------------------------------------------------
# WHAT A PROFILE CARRIES, AND WHAT IT MUST NOT.
#
# A device record holds two different kinds of fact, and only one of them has
# anything to do with an arrangement.
#
#   DEVICE fields follow the HARDWARE. They describe the machine, the lane it
#   is reached on, or how it feels under the hand. Rearrange every screen on
#   the desk and not one of them is any different. The live config owns them;
#   a profile neither stores them nor restores them.
#
#   ARRANGEMENT fields describe WHERE THINGS SIT. They are the entire reason a
#   named arrangement exists, so a profile carries them.
#
# ONE QUESTION classifies a new field: if the desk were rearranged and nothing
# else changed, would this value now be wrong? If no, it is a DEVICE field.
#
# Do NOT classify by what a field affects downstream. `pointer_gain` is
# model-side (DEVLOG: it scales the virtual cursor's belief, never the wire)
# and `sensitivity` is wire-side, but that only decides which SYMPTOM a wrong
# value produces -- drift on a crossing versus the wrong feel. It says nothing
# about who owns the value, and reasoning from it puts the two halves of one
# calibration in different places.
#
# The classification below is COMPLETE by construction: normalize_config()
# builds a device from a fixed whitelist, so these two tuples plus the join key
# are exactly its keys, and test_profiles.py fails if a new field appears in
# that whitelist without being classified here.
#
#   id           the JOIN KEY, neither kind. It is how a saved record finds the
#                live one, so it is stripped from neither side.
#
#   radio        DEVICE. A physical dongle. The bonds behind it live on the
#                guest per radio, so an arrangement saved when the Mac was on a
#                different dongle would point the lane at a radio holding no
#                bond for it -- pairs, goes green, does nothing.
#   port         DEVICE. A lane on the guest, allocated one per machine. Two
#                arrangements of one desk must never fight over a lane.
#   name         DEVICE. What that machine is called. It is a label on the
#                hardware; renaming a device and then switching arrangement
#                used to silently undo the rename.
#   enabled      DEVICE. Whether the machine is in service at all -- it gates
#                the daemon, pairing and status polling, which are hardware
#                acts. It also drops the device's screens from the desk, which
#                is why it reads arrangement-shaped; that is a CONSEQUENCE of
#                the machine being out of service, not the purpose. An
#                arrangement that wants a device off the desk moves or omits
#                its screens; it does not stop that machine's daemon.
#   clipboard    DEVICE. A capability, not a preference: the relay needs helper
#                shortcuts installed ON the device. Whether they are installed
#                cannot depend on where its screens sit.
#   sensitivity  DEVICE. Feel. A property of the device and of the hand using
#                it. This is the field that produced the bug: three devices
#                tuned to one notch reverted the moment the arrangement
#                changed, because the profile carried a snapshot of the
#                numbers they had been tuned away from.
#   pointer_accel   DEVICE. Our own acceleration curve for that device, applied
#                here so the wire and the model see the same motion. Feel.
#   scroll_invert   DEVICE. Which way the wheel goes on that machine. It exists
#                to cancel the device's OWN scroll convention (a Mac with
#                "natural" scrolling on), so it tracks that machine's settings.
#   pointer_gain DEVICE. Target POINTS produced per HID unit -- a calibration
#                of that machine's window server, which is a hardware fact. The
#                arrangement is already accounted for elsewhere in the same
#                expression (`gain * display["w"] / res_w`): resize a screen's
#                desk rectangle and the mapping follows on its own, with no
#                change to gain. Were it arrangement-scoped, re-calibrating one
#                desk would leave the SAME machine mis-calibrated on every
#                other -- the defect this constant exists to prevent.
#   compensate_target_accel   DEVICE. Inverts the TARGET's own acceleration
#                curve. Only meaningful where that curve is known, which is a
#                statement about the target OS.
#   keyboard_verbatim   DEVICE. "This machine already remaps its own
#                modifiers." A fact about that Mac, true at any desk.
#   modifier_remap   DEVICE. An iPad wants physical Alt as Command, a Mac wants
#                Option. A device convention.
#
#   displays     ARRANGEMENT. The rectangles ARE the desk.
#                Known and accepted impurity: a display record also carries
#                res_w/res_h/rotation/refresh_hz/diagonal_in, which are
#                hardware facts. They stay with the arrangement deliberately --
#                re-describing a screen is exactly what a second arrangement is
#                FOR here (the saved desks are named "Mac4k" and "Mac 2k" after
#                the resolution they hold), and w/h are derived from the
#                diagonal, resolution and rotation together, so the rectangle
#                cannot be separated from them. This is the one path by which a
#                profile can still overwrite a hardware fact, and it is meant
#                to.
# ---------------------------------------------------------------------------
DEVICE_KEY = "id"
DEVICE_FIELDS = ("radio", "port", "name", "enabled", "clipboard",
                 "sensitivity", "pointer_accel", "scroll_invert",
                 "pointer_gain", "compensate_target_accel",
                 "keyboard_verbatim", "modifier_remap")
ARRANGEMENT_FIELDS = ("displays",)


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
    """Snapshot the arrangement under a name. Device fields are dropped.

    An arrangement is a picture of WHERE THINGS SIT. Everything that follows
    the hardware instead -- see DEVICE_FIELDS above -- is left out entirely,
    so the file cannot hold an opinion about it to restore later.

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
        for field in DEVICE_FIELDS:
            device.pop(field, None)
    snapshot["profile"] = name
    os.makedirs(PROFILE_DIR, exist_ok=True)
    with open(_profile_path(name) + ".new", "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, indent=2)
    os.replace(_profile_path(name) + ".new", _profile_path(name))
    return name


def load_profile(name, current):
    """A saved arrangement, wearing THIS machine's devices.

    Which dongle drives a device, what it is called, and how it feels under the
    hand are facts about the hardware, not about the desk -- so every field in
    DEVICE_FIELDS is taken from what is running NOW, matched by device id, and
    never read out of the file.

    A field the live device does not carry is DELETED rather than left as the
    file found it. Profiles written before this rule still hold the old values,
    and a device that has since been removed from the desk would be the one
    record able to smuggle them back in; normalize_config supplies the default
    instead. The result: no device-scoped value can originate from a profile,
    conditional on nothing.

    The cost, stated so it is not a surprise: a device that exists ONLY in a
    saved arrangement comes back as an unconfigured stub -- no radio, a fresh
    port, no name, default feel -- with its screens laid out where they were.
    That was already true of its radio and port; it is now true of the rest,
    which is the honest reading, because there is no live device for those
    values to describe."""
    with open(_profile_path(name), encoding="utf-8") as handle:
        loaded = json.load(handle)
    hardware = {device.get(DEVICE_KEY): device
                for device in current.get("devices", [])}
    for device in loaded.get("devices", []):
        live = hardware.get(device.get(DEVICE_KEY), {})
        for field in DEVICE_FIELDS:
            if field in live:
                device[field] = live[field]
            else:
                device.pop(field, None)
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
             font=(FONT_UI_SEMI, 11)).pack(
        anchor="w", padx=18, pady=(16, 2))
    tk.Label(win, text=message, bg=CARD, fg=MUTED, font=(FONT_UI, 9),
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
                 padding=8, font=(FONT_UI, 10))
    # "pressed" BEFORE "active": ttk takes the first matching state, and a held
    # button is both pressed and hovered at once. Active-first would mean the
    # press never showed.
    st.map("TButton", background=[("pressed", PRESS), ("active", "#221F2A")])
    st.configure("Accent.TButton", background=ACCENT_DIM,
                 foreground="#F1EBFF", font=(FONT_UI_SEMI, 10))
    st.map("Accent.TButton",
           background=[("pressed", PRESS_ACCENT), ("active", "#764BE2")])
    st.configure("Danger.TButton", background="#4A1F38",
                 foreground="#FFD9EC", font=(FONT_UI_SEMI, 10))
    st.map("Danger.TButton",
           background=[("pressed", PRESS_DANGER), ("active", "#66294C")])


def _elevation_gate():
    """Ask what to do before keys, Bluetooth, audio, or the VM are touched."""
    root = tk.Tk()
    _resolve_brand_fonts(root)
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
            f"{APP_LABEL} is not running as administrator",
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


# EsotericOS coexistence, GUI side. The portal process is the one that holds
# Local\EsotericOS.InputCaptureLease (win/openspan_portal.py, InputCaptureLease)
# and this process never captures anything, so strictly nothing is required
# here: when the portal is hard-killed mid-capture by _terminate_role_process
# above, Windows marks the mutex ABANDONED and the next waiter reclaims it. That
# is exactly why the contract specifies a mutex rather than an event, and
# EsotericOS has verified that path end to end.
#
# What this adds is timing, not correctness. An abandoned mutex is only
# collected when somebody next waits on it, so between the taskkill and
# EsotericOS's next probe the lease reads as held by a process that no longer
# exists. Taking it and handing it straight back COLLECTS the abandonment at the
# moment we know the holder is gone. It costs one mutex round trip on a worker
# thread and it can only ever shorten the window, never lengthen it.
LEASE_NAME = r"Local\EsotericOS.InputCaptureLease"


def _clear_input_capture_lease(why=""):
    """Collect an abandoned input-capture lease. Never raises, never blocks
    the caller: the whole thing runs on a throwaway daemon thread, because a
    contended wait would otherwise stall the Tk thread for up to a second.

    Acquire and release happen on ONE thread -- this one. A Windows mutex is
    thread-owned and ReleaseMutex from anywhere else fails while leaving it
    held, which is the precise failure this is supposed to prevent.
    """
    def work():
        handle = None
        k32 = None
        try:
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateMutexW.restype = ctypes.c_void_p   # HANDLE: the default
            k32.CreateMutexW.argtypes = [ctypes.c_void_p,  # 32-bit restype
                                         ctypes.c_long,    # truncates a 64-bit
                                         ctypes.c_wchar_p]  # handle
            k32.WaitForSingleObject.restype = ctypes.c_ulong
            k32.WaitForSingleObject.argtypes = [ctypes.c_void_p,
                                                ctypes.c_ulong]
            k32.ReleaseMutex.argtypes = [ctypes.c_void_p]
            k32.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = k32.CreateMutexW(None, False, LEASE_NAME)
            if not handle:
                return
            # 1000 ms, not 0: EsotericOS probes the lease by taking it and
            # handing it straight back, so a zero-timeout wait can lose that
            # race and report a free lease as contended.
            rc = k32.WaitForSingleObject(handle, 1000)
            if rc in (0x00000000, 0x00000080):   # WAIT_OBJECT_0/WAIT_ABANDONED
                k32.ReleaseMutex(handle)
                if rc == 0x00000080:
                    _emit("event", "input-capture lease reclaimed after the "
                                   "portal exited without releasing it"
                                   + (f" ({why})" if why else ""))
            # A timeout means something else genuinely holds it -- not ours to
            # break, and not ours to complain about. Stay quiet.
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                if handle and k32 is not None:
                    k32.CloseHandle(handle)
            except Exception:  # noqa: BLE001
                pass
    try:
        threading.Thread(target=work, name="openspan-lease-reset",
                         daemon=True).start()
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
            f"{APP_LABEL} was not started. The administrator request was cancelled "
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
                           encoding="utf-8", errors="replace",
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
        elif key == "address":
            current["address"] = value
        elif key == "current state":
            current["state"] = value
    if current.get("uuid"):
        devices.append(current)
    return _merge_usb_twins(devices)


# VirtualBox's own vendor:product. A captured device is listed a SECOND time
# under these ids, as the proxy stub standing in for it, and the stub's address
# carries the real device's serial number even when the device's own
# SerialNumber field is missing.
_VBOX_PROXY = "vid_80ee&pid_cafe"


def _merge_usb_twins(devices):
    """One entry per physical device.

    `list usbhost` reports a CAPTURED device twice: once as itself and once as
    VirtualBox's proxy stub, with different UUIDs and the same vendor, product
    and port. Counting both said "3 of 5 radios are attached, 2 are missing" on a
    perfectly healthy machine -- a false alarm produced by the very feature meant
    to explain a real one.

    Port is the key, because it is the one field always reported: serial and
    product strings vanish whenever VirtualBox cannot open the device, which is
    most of the time it matters.

    The pair is merged rather than filtered, because each half knows something
    the other does not -- the stub carries the serial, and the original carries
    the UUID that `usbattach` accepts.
    """
    merged, order = {}, []
    for device in devices:
        key = (device.get("vendor"), device.get("product_id"),
               device.get("port"))
        if device.get("port") is None:
            key = (device.get("uuid"),)          # nothing to merge it with
        proxy = _VBOX_PROXY in (device.get("address") or "").lower()
        if not device.get("serial") and proxy:
            # \\?\usb#vid_80ee&pid_cafe#a1b2c3d4e5f6#{...}
            parts = (device.get("address") or "").split("#")
            if len(parts) > 2 and len(parts[2]) == 12:
                device = dict(device, serial=parts[2].upper())
        if key not in merged:
            merged[key] = dict(device, proxy=proxy,
                               uuids={device.get("uuid", "").lower()})
            order.append(key)
            continue
        kept = merged[key]
        # BOTH uuids are kept. `usbattach` takes the real device's, while
        # USBAttachActive reports the PROXY's -- so asking "does the VM hold
        # this?" against a single uuid answers no for a device the VM is
        # holding perfectly well. That is the whole of the false "2 missing".
        kept.setdefault("uuids", set()).add(device.get("uuid", "").lower())
        # the real device's UUID is the one usbattach takes, so a stub never
        # overwrites it; everything else fills in whatever is missing
        if kept.get("proxy") and not proxy:
            seen = kept.get("uuids", set())
            merged[key] = dict(device, **{
                field: value for field, value in kept.items()
                if value and not device.get(field)
                and field not in ("proxy", "uuids")})
            merged[key]["proxy"] = False
            merged[key]["uuids"] = seen
        else:
            for field, value in device.items():
                if value and not kept.get(field) and field != "proxy":
                    kept[field] = value
        if "Captured" in (device.get("state", ""), kept.get("state", "")):
            merged[key]["state"] = "Captured"
    return [merged[key] for key in order]


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


def _usb_serial_key(value):
    """Normalise the address-like serials Bluetooth adapters commonly use."""
    return str(value or "").strip().replace(":", "").replace("-", "").upper()


def _usb_filter_matches(spec, device):
    """Whether one host device satisfies one active VM USB filter.

    Serial-specific filters are deliberately honoured here.  OpenSpan never
    writes or pins filters automatically -- a stopped device does not always
    expose its serial to VirtualBox -- but if the configured VM already has an
    explicit serial filter its ownership audit must not silently broaden that
    filter back to every identical adapter.
    """
    if spec.get("vendor") != device.get("vendor"):
        return False
    if spec.get("product_id") \
            and spec.get("product_id") != device.get("product_id"):
        return False
    wanted = _usb_serial_key(spec.get("serial"))
    return not wanted or wanted == _usb_serial_key(device.get("serial"))


def _ambiguous_usb_filters(filters, devices):
    """Duplicate non-serial filters that match multiple physical adapters.

    This is an audit finding, not an automatic rewrite.  Plain VID:PID filters
    are known to capture the current multi-radio desk reliably, while pinning a
    filter at the wrong moment has also made a working adapter disappear.  The
    safe contribution here is to expose ambiguity and leave the user's VM
    configuration untouched.
    """
    groups = {}
    for spec in filters:
        if spec.get("serial"):
            continue
        key = (spec.get("vendor", ""), spec.get("product_id", ""))
        groups.setdefault(key, []).append(spec)

    findings = []
    for (vendor, product), specs in groups.items():
        if len(specs) < 2:
            continue
        matched = [device for device in devices
                   if _usb_filter_matches(specs[0], device)]
        if len(matched) < 2:
            continue
        findings.append({
            "vendor": vendor,
            "product_id": product,
            "filters": [spec.get("name", "") for spec in specs],
            "devices": matched,
        })
    return findings


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
    filters = parse_usb_filters(info, keep_serial=True)
    held = parse_usb_attached(info)
    devices = parse_usb_host(usbhost)
    # A serial-specific filter is the narrowest statement of ownership, then a
    # VID:PID filter, then a vendor-wide one.  This only chooses which matching
    # filter names a device; one VirtualBox filter may legitimately match more
    # than one device, so filters are not consumed as allocation slots.
    ordered_filters = sorted(
        enumerate(filters),
        key=lambda row: (not bool(row[1].get("serial")),
                         not bool(row[1].get("product_id")), row[0]))
    mine, attached, busy, captured, available, unavailable = [], [], [], [], [], []
    for device in devices:
        matches = [spec for _, spec in ordered_filters
                   if _usb_filter_matches(spec, device)]
        if not matches:
            continue
        device = dict(device, filter=matches[0]["name"],
                      matching_filters=[spec.get("name", "")
                                        for spec in matches])
        mine.append(device)
        uuids = device.get("uuids") or {device["uuid"].lower()}
        if uuids & held:
            attached.append(device)
            continue
        state = str(device.get("state") or "").strip().lower()
        if state == "busy":
            busy.append(device)
        elif state == "captured":
            captured.append(device)
        elif state == "available":
            available.append(device)
        else:
            unavailable.append(device)

    absent = [spec for spec in filters
              if not any(_usb_filter_matches(spec, device)
                         for device in devices)]
    lost = busy + captured + available + unavailable
    return {
        "mine": mine,
        "attached": attached,
        "busy": busy,
        "captured": captured,
        "available": available,
        "unavailable": unavailable,
        "attachable": busy + available,
        "absent": absent,
        "ambiguous_filters": _ambiguous_usb_filters(filters, devices),
        "lost": lost,
        "held": held,
        "filters": filters,
    }


def serial_to_radio(serial):
    """A Bluetooth dongle's USB serial number IS its adapter address.

    The two same-model dongles on the reference desk report distinct twelve-hex
    serials which are exactly their configured radio addresses with the colons
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
    radio = serial_to_radio(device.get("serial"))
    if radio and config:
        for target in config.get("devices", []):
            if str(target.get("radio", "")).upper() == radio:
                return f"{target.get('name') or target.get('id')}’s dongle"
    name = (device.get("name") or device.get("maker") or "").strip()
    if not name:
        # Product and Manufacturer are both absent whenever VirtualBox cannot
        # open the device. The USB port always survives, and "the dongle in USB
        # port 4" is something a person can actually act on.
        port = device.get("port")
        return (f"the dongle in USB port {port}" if port
                else f"{device.get('vendor', '?')}:"
                     f"{device.get('product_id', '?')}")
    port = device.get("port")
    return f"{name} (USB port {port})" if port else name


def usb_filter_label(spec):
    """A VM filter name plus enough identity to diagnose an absent adapter."""
    name = (spec.get("name") or "unnamed filter").strip()
    vidpid = f"{spec.get('vendor', '?')}:{spec.get('product_id') or '*'}"
    serial = (spec.get("serial") or "").strip()
    return (f"filter \u201c{name}\u201d ({vidpid}, serial {serial})" if serial
            else f"filter \u201c{name}\u201d ({vidpid})")


def radio_status_text(state, config=None, vm_name=None):
    """One UI sentence that preserves each materially different USB owner.

    Returns ``(text, repairable_count)``. Busy/Available devices permit one
    attach request. Captured-but-not-delivered, absent, and unknown states are
    deliberately not counted as repairable.
    """
    config, vm_name = config or {}, vm_name or VM
    parts = []
    attached = len(state.get("attached", []))
    total = len(state.get("mine", []))
    if attached:
        parts.append(f"{attached} of {total} present radios are attached to "
                     f"the configured VM \u201c{vm_name}\u201d.")

    busy = state.get("busy", [])
    if busy:
        names = ", ".join(usb_label(device, config) for device in busy)
        parts.append(f"Busy on Windows: {names}. Repair will send exactly one "
                     "attach request per radio.")

    available = state.get("available", [])
    if available:
        names = ", ".join(usb_label(device, config) for device in available)
        parts.append(f"Available on the host: {names}. Repair will send "
                     "exactly one attach request per radio.")

    captured = state.get("captured", [])
    if captured:
        names = ", ".join(usb_label(device, config) for device in captured)
        parts.append(
            f"Captured but not delivered: VirtualBox owns {names}, but the "
            f"configured VM \u201c{vm_name}\u201d does not. Repair will not send "
            "another attach request; replug the adapter (restart Windows for "
            "a built-in radio).")

    absent = state.get("absent", [])
    if absent:
        names = ", ".join(usb_filter_label(spec) for spec in absent)
        parts.append(f"Absent: no host adapter matches {names}. Check that the "
                     "radio is plugged in.")

    unavailable = state.get("unavailable", [])
    if unavailable:
        names = ", ".join(
            f"{usb_label(device, config)} ({device.get('state') or 'unknown'})"
            for device in unavailable)
        parts.append(f"No automatic attach is safe for: {names}.")

    ambiguous = state.get("ambiguous_filters", [])
    if ambiguous:
        groups = []
        for finding in ambiguous:
            names = ", ".join(f"\u201c{name or 'unnamed'}\u201d"
                              for name in finding["filters"])
            groups.append(
                f"{names} all match {len(finding['devices'])} adapters at "
                f"{finding['vendor']}:{finding['product_id'] or '*'}")
        parts.append("Filter audit: " + "; ".join(groups)
                     + ". They omit serial numbers, so the filters are "
                       "indistinguishable; OpenSpan will not rewrite them.")

    if not parts:
        if state.get("filters"):
            parts.append(f"All {total} present radios are attached to the "
                         f"configured VM \u201c{vm_name}\u201d.")
        else:
            parts.append(f"The configured VM \u201c{vm_name}\u201d has no active "
                         "USB radio filters.")
    return " ".join(parts), len(busy) + len(available)


def why_not_ready(config=None):
    """Why the bridge is not up yet, in one sentence a stranger can use.

    "Booting the bridge… (~90s)" is the worst thing this app can say when
    something is actually wrong. It spun for as long as it was left running while
    every fact needed to explain it was one command away, and the honest reading
    from outside is that the app has no idea -- which was true.

    The states are ordered by what has to be true before the next thing can be:
    the VM runs, the guest answers, radios exist, BlueZ sees them, the boot
    helper finishes, the daemons listen. The first unmet one IS the answer.

    Returns (ready, sentence). Cheap calls first; the guest is only asked once
    the VM is actually running.
    """
    config = config or {}
    if not vm_running():
        return False, ("The VM is not running. Start it on the Bridge tab.")

    state = read_radio_state()
    if state["captured"]:
        names = ", ".join(usb_label(d, config) for d in state["captured"])
        return False, (
            f"VirtualBox has captured {names} but never delivered it to the "
            f"configured VM \u201c{VM}\u201d. the app will not retry; replug the "
            f"adapter (restart Windows for a built-in radio).")
    if state["attachable"]:
        names = ", ".join(usb_label(d, config) for d in state["attachable"])
        return False, (
            f"The configured VM \u201c{VM}\u201d does not have {names}. Repair radios "
            f"will make one attach attempt.")
    # An unmatched filter can be stale/redundant while every real radio is
    # healthy. The Bluetooth panel always audits it, but it blocks the global
    # readiness banner only when no configured-filter device is present at all.
    if state["absent"] and not state["mine"]:
        names = ", ".join(usb_filter_label(f) for f in state["absent"])
        return False, f"No host adapter matches {names}; check that it is plugged in."
    if state["unavailable"]:
        names = ", ".join(usb_label(d, config)
                          for d in state["unavailable"])
        return False, f"The host USB state for {names} is not safe to repair."

    probe = ssh_guest(
        "ls /sys/class/bluetooth/ 2>/dev/null | wc -l; "
        "systemctl is-active openspan-btready; "
        "systemctl --no-pager --plain list-units 'openspanble@*' "
        "| grep -c 'active running'; "
        "ss -ltn 2>/dev/null | grep -cE ':995[0-9]'",
        timeout=12, quiet=True, show_result=False)
    if probe.returncode:
        return False, ("The VM is running but not answering yet — it takes "
                       "about 90 seconds from cold.")
    rows = (probe.stdout or "").split()
    adapters, btready, daemons, ports = (rows + ["?"] * 4)[:4]
    wanted = len([d for d in config.get("devices", [])
                  if d.get("enabled", True)]) or 1

    if adapters == "0":
        return False, ("The VM has the radios but BlueZ has not registered any "
                       "adapter yet. If this does not clear in a minute, "
                       "restart the VM.")
    if btready != "active":
        return False, (f"Waiting on the guest's radio-ready helper "
                       f"(openspan-btready, up to 200s). {adapters} adapter(s) "
                       f"are present; the device daemons start behind it.")
    if ports == "0" or daemons == "0":
        return False, (f"The radios are ready and the boot helper has "
                       f"finished, but {daemons} of {wanted} device daemons are "
                       f"running. Try Restart keyboard.")
    return True, (f"Ready — {adapters} radio(s), {daemons} daemon(s), "
                  f"{ports} lane(s) listening.")


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
        # UTF-8 explicitly. text=True decodes with the ANSI codepage, and
        # `systemctl status` prints ● and ○ -- one of those bytes raised
        # UnicodeDecodeError inside subprocess' reader THREAD, where it cannot
        # be caught here: the exception was printed to a console nobody sees and
        # the output came back empty. Every status check built on this was
        # silently blind.
        r = subprocess.run(
            _ssh_argv(cmd),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, creationflags=NO_WINDOW)
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
# request against it. Retrying cannot clear it -- the host device object has to
# be re-created by a physical replug or a Windows restart. Restarting only the VM
# risks spreading the ownership fault to radios that are still healthy.
_USB_WEDGED = "busy with a previous request"
# Once VirtualBox accepts a request but does not land it, the same host object
# must not be offered again during this app run. A physical replug creates a new
# UUID and is therefore a new object; an unchanged UUID is the same unsafe one.
_USB_ATTACH_BLOCKED = set()
_USB_REPAIR_LOCK = threading.Lock()  # a double-click cannot start two attachers
WEDGED_ADVICE = (
    "VirtualBox still holds an unfinished request for it — what a dongle "
    "unplugged while the VM had it leaves behind. No command can clear it: even "
    "usbdetach refuses, because as far as the VM is concerned the device was "
    "never attached. The app will not retry the request. "
)
ATTACH_SETTLE = 1.5     # VirtualBox moves a device between owners asynchronously


REPLUG_ADVICE = (
    "Unplug it and plug it back in — a fresh arrival is a new device object, "
    "and the armed filter completes the capture the moment it appears. One "
    "replug often completes every pending capture, not just its own. This is "
    "the one recovery that has never failed; a Windows restart merely re-runs "
    "the same boot-time race and can wedge a different radio. For a built-in "
    "radio, which has no plug, restart Windows. Do NOT restart the VM — that "
    "spreads the fault to radios that are still working."
)


def _captured_advice():
    return (
        f"VirtualBox has captured it from Windows but the configured VM “{VM}” "
        "does not hold it. the app sent no attach request because another one "
        "can only deepen this state. Unplug that adapter and plug it back in "
        "— a fresh arrival completes the capture. For a built-in radio, "
        "restart Windows. Do not restart the VM."
    )


def _not_landed_advice():
    return (
        f"VirtualBox accepted one attach request, but the configured VM “{VM}” "
        "still does not hold the device. The app will not retry. Unplug that "
        "adapter and plug it back in — a fresh arrival completes the capture. "
        "For a built-in radio, restart Windows. Do not restart the VM."
    )


# One PnP kick per device per app run. The kick is the missing half of
# VirtualBox's own capture: the filter arms, the Windows stack comes down,
# and the re-add as a VBox proxy silently never runs -- observed on every
# boot of 2026-08-08. A restart of the device node is a fresh arrival, and
# the armed filter takes a fresh arrival reliably (often completing every
# OTHER pending capture in the same stroke). One kick only: a device the
# kick cannot save is a phantom, and phantoms take a physical replug.
_PNP_KICKED = set()


def _pnp_kick(device, runner=None):
    """Restart one radio's Windows device node so the armed filter captures
    it as a fresh arrival. Returns (ok, detail). Generic: the instance ID is
    built from the device's own VID/PID/serial, or discovered by prefix when
    the adapter (like a built-in Intel) exposes no serial."""
    runner = runner or (lambda args: subprocess.run(
        args, capture_output=True, text=True, timeout=30,
        creationflags=NO_WINDOW))

    def norm(value):
        return str(value or "").strip().upper().removeprefix("0X")

    vid, pid = norm(device.get("vendor")), norm(device.get("product_id"))
    serial = str(device.get("serial") or "").strip()
    if not vid or not pid:
        return False, "device reports no VID/PID to kick by"
    prefix = f"USB\\VID_{vid}&PID_{pid}\\"
    if serial:
        instances = [prefix + serial]
    else:
        r = runner(["pnputil", "/enum-devices"])
        instances = [line.split(":", 1)[1].strip()
                     for line in (r.stdout or "").splitlines()
                     if ":" in line and prefix in line.upper()]
        if not instances:
            return False, "no Windows device node matches the adapter"
    iid = instances[0]
    r = runner(["pnputil", "/restart-device", iid])
    out = ((r.stdout or "") + (r.stderr or ""))
    if r.returncode == 0 and "successfully" in out.lower():
        return True, iid
    if "not connected" in out.lower():
        return False, ("its Windows node is a phantom -- only a physical "
                       "replug re-enumerates it")
    return False, (out.strip()[-160:] or "pnputil would not restart it")


def explicit_handoff(state=None, verify=None, kick=None, settle=None,
                     config=None, log=None):
    """The app performs the delivery VirtualBox failed to finish.

    For each Captured-but-not-delivered radio: ONE PnP kick (per app run),
    a settle, then the only verification that counts -- the VM's own
    attached list. Returns (delivered_labels, failed_pairs). Anything the
    kick cannot deliver keeps the fail-closed replug coaching; nothing here
    retries, loops, or touches healthy radios.
    """
    state = state if state is not None else read_radio_state()
    verify = verify or (lambda: parse_usb_attached(
        vbox("showvminfo", VM, "--machinereadable", quiet=True).stdout))
    kick = kick or _pnp_kick
    settle = ATTACH_SETTLE * 4 if settle is None else settle
    emit = log or (lambda m: _emit("event", m))
    delivered, failed = [], []
    for device in state.get("captured", []):
        label = usb_label(device, config)
        uuids = {str(value).lower() for value in
                 (device.get("uuids") or {device.get("uuid")}) if value}
        identity = tuple(sorted(uuids))
        if identity in _PNP_KICKED:
            failed.append((label, _captured_advice()))
            continue
        _PNP_KICKED.add(identity)
        emit(f"performing the delivery VirtualBox left unfinished: {label}…")
        ok, detail = kick(device)
        if not ok:
            _USB_ATTACH_BLOCKED.update(uuids)
            failed.append((label, f"kick failed -- {detail}. "
                           + REPLUG_ADVICE))
            continue
        time.sleep(settle)
        now_held = {str(value).lower() for value in verify()}
        if uuids & now_held or not uuids:
            _USB_ATTACH_BLOCKED.difference_update(uuids)
            delivered.append(label)
            emit(f"{label} delivered to the VM.")
        else:
            _USB_ATTACH_BLOCKED.update(uuids)
            failed.append((label, "the kick re-enumerated it but the VM "
                           "still does not hold it. " + REPLUG_ADVICE))
    return delivered, failed


def gentle_release(vbox_run=None, verify=None, settle=0.5, log=None):
    """Detach each VM-held radio, one verified detach at a time, BEFORE the
    VM powers off.

    A poweroff with radios still attached releases them all in one PnP
    storm. That storm is the documented 2026-08-08 injury event: the first
    radio-port fault in 149 hours appeared eight seconds after exactly this
    mass release. An ordered detach per radio hands Windows a calm
    re-enumeration instead. One attempt per device -- a radio that will not
    detach simply keeps the power-off fate it already had.
    """
    vbox_run = vbox_run or (lambda *a: vbox(*a, quiet=True))
    verify = verify or (lambda: parse_usb_attached(
        vbox("showvminfo", VM, "--machinereadable", quiet=True).stdout))
    emit = log or (lambda m: _emit("event", m))
    released, kept = [], []
    for uuid in sorted(verify()):
        vbox_run("controlvm", VM, "usbdetach", uuid)
        time.sleep(settle)
        if uuid in verify():
            kept.append(uuid)
        else:
            released.append(uuid)
    if released:
        emit(f"released {len(released)} radio(s) to Windows ahead of "
             "the power-off")
    if kept:
        emit(f"{len(kept)} radio(s) would not detach -- the power-off "
             "will drop them the old way")
    return released, kept


def repair_radios(config=None, settle=None):
    """Everything the app can do about a missing radio, cheapest first.

    Written for someone who does not have the person who wrote it sitting next
    to them. Each rung either fixes it or hands back a sentence naming the one
    physical thing left to do — never "it failed".

      1. ATTACH what the VM has lost, and verify the VM took it. This is the
         whole fix whenever a dongle is merely Busy -- Windows holding a device
         VirtualBox has not been asked for yet.
      2. Anything still missing gets named by the machine it serves, with the
         one action that remains. A dongle VirtualBox has CAPTURED but never
         delivered cannot be rescued from here by anything: `usbdetach` refuses
         it ("not attached to this machine"), restarting the VM spreads the
         fault to radios that were working, and stopping VBoxSVC releases the
         capture without restoring delivery. Restarting Windows fixes it every
         time. Saying that plainly is the whole job.

    Returns a dict the UI turns into one line and a few log entries.
    """
    config = config or {}
    # Filter pinning USED to be step one here, on the theory that two filters
    # matching the same vendor:product raced each other. A clean-boot run
    # disproved it: with plain vendor+product filters, all three radios were
    # captured AND delivered in 32 seconds, twins and all. Pinning a filter to a
    # serial VirtualBox often cannot read stopped the filter matching at all,
    # which cost a working desk. It is gone.
    pinned, pin_failed = [], []
    recovered, failed = reclaim_radios(
        config=config, **({} if settle is None else {"settle": settle}))
    state = read_radio_state()
    return {
        "pinned": pinned, "pin_failed": pin_failed,
        "recovered": recovered, "failed": failed,
        "total": len(state["mine"]), "still_lost": [
            usb_label(d, config) for d in state["lost"]],
        "repairable": len(state["attachable"]),
        "captured": len(state["captured"]),
        "absent": len(state["absent"]),
    }


def reclaim_radios(settle=ATTACH_SETTLE, verify=None, config=None, kick=None):
    """Serialize repair clicks, then run exactly one ownership pass."""
    with _USB_REPAIR_LOCK:
        return _reclaim_radios_once(settle=settle, verify=verify,
                                    config=config, kick=kick)


def _reclaim_radios_once(settle=ATTACH_SETTLE, verify=None, config=None,
                         kick=None):
    """Resolve one ownership report without retrying. Returns two result lists.

    **Success is the VM holding the device, not VBoxManage returning zero.**
    The first version of this believed the exit code, and the exit code is about
    whether the *request* was accepted. On Doug's desk the request was accepted,
    the dongles were taken off Windows, the handoff to the guest never completed,
    and the app reported that it had attached them -- so the panel went on saying
    "1 of 3" while claiming success, and the honest reading of that from outside
    is "nothing happened". Which is what he said.

    The transfer is asynchronous, so it is given time and then checked, and what
    is checked is the configured VM's own list of attached devices. There is no
    retry loop: an accepted request that does not land has transitioned to the
    exact Captured-but-not-delivered state where another attach is unsafe.

    Nothing here scans, pairs or connects. It puts the USB device where the guest
    can see it, and then says to press Connect.
    """
    verify = verify or (lambda: parse_usb_attached(
        vbox("showvminfo", VM, "--machinereadable", quiet=True).stdout))
    recovered, failed = [], []
    state = read_radio_state()

    for spec in state.get("absent", []):
        failed.append((usb_filter_label(spec),
                       "No matching host adapter is present. OpenSpan sent no "
                       "attach request; check that the radio is plugged in."))
    # Captured-but-not-delivered: perform the delivery VirtualBox left
    # unfinished (one PnP kick per device per run) instead of only advising.
    # Whatever the kick cannot save keeps the fail-closed replug coaching.
    kicked_ok, kicked_failed = explicit_handoff(
        state=state, verify=verify, config=config,
        settle=settle * 4 if settle else settle,
        **({"kick": kick} if kick else {}))
    recovered.extend(kicked_ok)
    failed.extend(kicked_failed)
    for device in state.get("unavailable", []):
        failed.append((
            usb_label(device, config),
            f"The host reports USB state {device.get('state') or 'unknown'}; "
            "the app sent no attach request because that state is not known "
            "to be safe."))

    # Busy means Windows owns it; Available means nobody does. Those are the
    # only two states in which OpenSpan is allowed to ask the configured VM for
    # the device, once. De-duplicate defensively even if a malformed report puts
    # the same record in both buckets.
    attempted = set()
    for device in state.get("attachable", []):
        label = usb_label(device, config)
        uuids = {str(value).lower() for value in
                 (device.get("uuids") or {device["uuid"]}) if value}
        identity = tuple(sorted(uuids))
        if identity in attempted:
            continue
        attempted.add(identity)

        # A prior worker or automatic filter capture may have won the race
        # since read_radio_state(). Do not issue a redundant attach.
        now_held = {str(value).lower() for value in verify()}
        if uuids & now_held:
            _USB_ATTACH_BLOCKED.difference_update(uuids)
            recovered.append(label)
            continue
        if uuids & _USB_ATTACH_BLOCKED:
            failed.append((label, _captured_advice()))
            continue

        result = vbox("controlvm", VM, "usbattach", device["uuid"])
        text = ((result.stderr or "") + " "
                + (result.stdout or "")).strip().lower()
        if _USB_WEDGED in text:
            _USB_ATTACH_BLOCKED.update(uuids)
            failed.append((label, WEDGED_ADVICE + REPLUG_ADVICE))
            continue
        if result.returncode:
            detail = ((result.stderr or result.stdout or "").strip()[-200:]
                      or "VBoxManage would not attach it")
            failed.append((label, detail + " Not retried."))
            continue

        time.sleep(settle)
        if uuids & {str(value).lower() for value in verify()}:
            _USB_ATTACH_BLOCKED.difference_update(uuids)
            recovered.append(label)
        else:
            _USB_ATTACH_BLOCKED.update(uuids)
            failed.append((label, _not_landed_advice()))
    return recovered, failed


def stop_virtualbox_backend(timeout=20.0):
    """Wait for the VM to actually be off, then close VirtualBox down.

    `_full_stop` used to fire `poweroff` and close 400ms later, which claimed in
    its own docstring that "nothing lingers". Two things lingered:

    * **The power-off is asynchronous.** The app was gone before the VM was, so
      whether the machine ended cleanly depended on timing nobody controlled.
    * **VBoxSVC and VBoxSDS keep running.** They are what Windows names when it
      says an application is preventing a restart, and VBoxSVC is where the host
      USB state lives -- so shutting the app down could never clear a stuck
      capture, however many times it was tried.

    VBoxSVC is a COM server VirtualBox re-launches on demand, so ending it costs
    nothing and takes the wedged USB state with it. VBoxSDS is a Windows service
    and is left alone.

    Returns the state it managed to reach, for the log.
    """
    deadline = time.monotonic() + timeout
    state = ""
    while time.monotonic() < deadline:
        info = vbox("showvminfo", VM, "--machinereadable", quiet=True).stdout
        state = next((line.split("=", 1)[1].strip('"')
                      for line in (info or "").splitlines()
                      if line.startswith("VMState=")), "")
        if state in ("poweroff", "aborted", "saved", ""):
            break
        time.sleep(0.5)
    try:
        subprocess.run(["taskkill", "/IM", "VBoxSVC.exe", "/F"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=15,
                       creationflags=NO_WINDOW)
    except Exception:  # noqa: BLE001
        pass
    return state or "unknown"


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
    """One device's daemon status as a DICT, or None.

    The return is normalised to a dict on purpose. Every caller does
    `status.get(...)` after testing `status is not None`, and json.loads of a
    legal JSON scalar or array -- `5`, `"ok"`, `[]`, which is what any other
    process listening on that port would send -- passes that test and then
    raises AttributeError. Both readers of this are inside the closure
    _drain_ui swallows, at the very top of _apply_poll, so the whole status
    surface below simply stops repainting with nothing in the console. A
    non-dict reply is not a status; it is the same thing as no reply.
    """
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
        reply = json.loads(data.split(b"\n", 1)[0].decode())
        return reply if isinstance(reply, dict) else None
    except Exception:  # noqa: BLE001
        return None


def daemon_status():
    """Status of the FIRST configured device's daemon -- used only for the
    global 'is the bridge up' banner. No device id is assumed; per-device state
    comes from _poll_device_status()."""
    first = first_device_id()
    return target_daemon_status(first) if first else None


def target_daemon_cmd(target, obj, timeout=2):
    """Send one command to the daemon and return its reply DICT (or None).

    Same normalisation as target_daemon_status, for the same reason: every
    caller here does `reply and reply.get("ok")`, and a JSON scalar or array
    reply satisfies the truthiness test and then raises.
    """
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
        reply = json.loads(data.split(b"\n", 1)[0].decode())
        return reply if isinstance(reply, dict) else None
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


# ---- clipboard privacy markers -----------------------------------------
#
# Every byte set_clipboard_text() writes came off an iPad over the LAN relay,
# and CLIPBOARD_DESIGN.md 4 is explicit that it carries passwords. Windows'
# own Win+V history, its cloud-clipboard sync, and any third-party clipboard
# manager would otherwise record it. Four documented opt-out FORMATS say "do
# not keep this", and they go on the clipboard alongside the data.
#
#   Clipboard Viewer Ignore                        presence flag
#   ExcludeClipboardContentFromMonitorProcessing   presence flag
#   CanIncludeInClipboardHistory                   DWORD, 0 = do not keep
#   CanUploadToCloudClipboard                      DWORD, 0 = do not sync
#
# The first three are what EsotericOS's clipboard-history gate reads, and that
# gate fails closed -- see D:\EsotericOS\docs\INTEROP.md, "Clipboard". The
# fourth is not in their gate; it is what stops Windows itself syncing the
# payload to the Microsoft account.
#
# Registered ONCE, here at import. RegisterClipboardFormatW is idempotent, is
# case-insensitive, and returns the same id for the same name in every process
# on the session -- so our ids and EsotericOS's agree by construction rather
# than by arrangement. Doing it here rather than inside the Open/Close pair
# keeps atom-table work off the global clipboard lock.
#
# THE VISIBLE CONSEQUENCE, stated because it is a behaviour change and not a
# side effect: text copied on the iPad no longer appears in Win+V on this PC
# and no longer syncs to the Microsoft account. That is the correct trade for
# password-bearing relay traffic, and it is the only thing this changes.
_CLIP_MARK_NAMES = [
    ("Clipboard Viewer Ignore", 0),
    ("ExcludeClipboardContentFromMonitorProcessing", 0),
    ("CanIncludeInClipboardHistory", 0),
    ("CanUploadToCloudClipboard", 0),
]

try:
    import ctypes as _ct_mark
    _ct_mark.windll.user32.RegisterClipboardFormatW.restype = _ct_mark.c_uint
    _ct_mark.windll.user32.RegisterClipboardFormatW.argtypes = [
        _ct_mark.c_wchar_p]
    CLIPBOARD_PRIVACY_FORMATS = [
        (_ct_mark.windll.user32.RegisterClipboardFormatW(name), value)
        for name, value in _CLIP_MARK_NAMES]
except Exception:  # noqa: BLE001
    CLIPBOARD_PRIVACY_FORMATS = []

# All four must have registered. A zero id is a format that does not exist.
CLIPBOARD_MARKING_AVAILABLE = (
    len(CLIPBOARD_PRIVACY_FORMATS) == len(_CLIP_MARK_NAMES)
    and all(fmt for fmt, _ in CLIPBOARD_PRIVACY_FORMATS))


def set_clipboard_text(text, private=True):
    """Put Unicode text on the Windows clipboard (stdlib ctypes).

    private=True marks the write so clipboard-history tools skip it. The relay
    always wants that; it is a parameter rather than a constant only so that a
    future non-relay caller can opt out deliberately, in code, at its own call
    site -- never from config, and never by accident.
    """
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

    if private and not CLIPBOARD_MARKING_AVAILABLE:
        # FAIL CLOSED. An unmarked write of relayed text is the exact outcome
        # this mechanism exists to prevent, so it is better to drop the paste
        # than to record a password in clipboard history. The relay surfaces
        # False as a 500 and the iPad shortcut can simply be run again.
        return False

    def _alloc_dword(value):
        h = k.GlobalAlloc(GMEM_MOVEABLE, 4)
        if not h:
            return None
        p = k.GlobalLock(h)
        if not p:
            k.GlobalFree(h)
            return None
        ctypes.memmove(p, ctypes.byref(ctypes.c_uint32(value)), 4)
        k.GlobalUnlock(h)
        return h

    buf = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(buf)
    for _ in range(5):
        if u.OpenClipboard(None):
            break
        time.sleep(0.02)
    else:
        return False
    try:
        # EmptyClipboard wipes EVERYTHING, markers from a previous write
        # included, so it comes first and the markers are re-placed every time.
        u.EmptyClipboard()
        # MARKERS BEFORE THE PAYLOAD, in the SAME Open/Close pair. Listeners are
        # notified once, on CloseClipboard, which is what makes markers and text
        # atomic to an observer -- their order among themselves is invisible.
        # Placing them first is for the abort path: a failure midway leaves
        # markers and no text, rather than unmarked password text.
        if private:
            for fmt, value in CLIPBOARD_PRIVACY_FORMATS:
                # REAL DATA, never NULL. SetClipboardData(fmt, NULL) means
                # delayed rendering: the format shows as available and the
                # OWNER WINDOW is asked for the bytes later, via
                # WM_RENDERFORMAT. OpenClipboard(NULL) leaves the clipboard
                # with no owner, so nothing can ever render it -- and
                # EsotericOS reads CanIncludeInClipboardHistory's DWORD rather
                # than only testing that the format is present.
                h = _alloc_dword(value)
                if not h:
                    return False
                if not u.SetClipboardData(fmt, h):
                    k.GlobalFree(h)  # ownership passes only on SUCCESS
                    return False
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
                # THE ONLY CLIPBOARD WRITE IN THE TREE. private defaults to
                # True and is not overridden here: relayed text is exactly what
                # must stay out of clipboard history.
                if set_clipboard_text(body):
                    self._plain(200, b"ok")
                    _emit("event", "clipboard received from the iPad "
                                   f"({len(body)} chars)")
                else:
                    self._plain(500)
                    _emit("err", "clipboard write failed "
                          + ("(clipboard busy?)"
                             if CLIPBOARD_MARKING_AVAILABLE else
                             "— the privacy markers would not register, so "
                             "the write was refused rather than land "
                             "unmarked in clipboard history"))

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
        self._nid.uFlags = 0x87  # NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP
        self._nid.uCallbackMessage = self._WM_TRAY
        self._nid.hIcon = hicon
        self._nid.szTip = tip[:127]
        _TRAY["active"] = self  # before NIM_ADD: callbacks may fire at once
        self._nim_add_ok = bool(
            sh.Shell_NotifyIconW(0, ctypes.byref(self._nid)))  # NIM_ADD
        if not self._nim_add_ok:
            _TRAY["active"] = None
            u32.DestroyWindow(self.hwnd)  # class/thunk stay: immortal
            raise OSError("tray: Shell_NotifyIconW failed")
        self._nid.uVersion = 4  # NOTIFYICON_VERSION_4
        self._nim_setversion_ok = bool(
            sh.Shell_NotifyIconW(4, ctypes.byref(self._nid)))  # NIM_SETVERSION

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
                    if msg == TrayIcon._WM_TRAY:
                        evt = l & 0xFFFF  # V4: event is LOWORD(lParam)
                    else:
                        evt = None
                    if evt in (0x0202, 0x0203, 0x0400, 0x0401):
                        # WM_LBUTTONUP / WM_LBUTTONDBLCLK / NIN_SELECT /
                        # NIN_KEYSELECT on the icon
                        try:
                            t.on_restore()
                        except Exception:  # noqa: BLE001
                            pass
                        return 0
                    if evt in (0x0205, 0x007B) and t.on_menu:
                        # WM_RBUTTONUP / WM_CONTEXTMENU on the icon
                        try:
                            t.on_menu()
                        except Exception:  # noqa: BLE001
                            pass
                        return 0
                    if msg == _TRAY["taskbar_created"]:
                        # explorer restarted and forgot every tray icon: re-add
                        try:
                            if t._sh.Shell_NotifyIconW(
                                    0, ctypes.byref(t._nid)):  # NIM_ADD
                                t._nid.uVersion = 4
                                t._sh.Shell_NotifyIconW(
                                    4, ctypes.byref(t._nid))  # NIM_SETVERSION
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
            if not self._sh.Shell_NotifyIconW(
                    0, self._ct.byref(self._nid)):  # NIM_ADD
                return False
            self._nid.uVersion = 4
            self._sh.Shell_NotifyIconW(
                4, self._ct.byref(self._nid))  # NIM_SETVERSION
            return True
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
                             text=f"{tag}{m['w']}x{m['h']}", fill="#F5F5F8",
                             justify="center", font=(FONT_UI, 9, "bold"))
        ix0, iy0 = self.w2c(self.ipad["x"], self.ipad["y"])
        ix1, iy1 = self.w2c(self.ipad["x"] + self.ipad["w"],
                            self.ipad["y"] + self.ipad["h"])
        # Colour reflects the real link: GREEN when live (connected + routing),
        # AMBER when paired but not driving, GREY when not paired. Kept in sync
        # by _apply_poll -> set_ipad_state.
        if self.ipad_state == "live":
            _fill, _line, _txt = IPAD_FILL, IPAD_LINE, "#D8C8FF"
        elif self.ipad_state == "idle":
            _fill, _line, _txt = IPAD_IDLE_FILL, IPAD_IDLE_LINE, "#ffe9b0"
        else:
            _fill, _line, _txt = IPAD_OFF_FILL, IPAD_OFF_LINE, MUTED
        self.create_rectangle(ix0, iy0, ix1, iy1, fill=_fill,
                              outline=_line, width=3)
        self.create_text((ix0 + ix1) / 2, (iy0 + iy1) / 2,
                         text=f"iPad\n{self.ipad['w']}x{self.ipad['h']}",
                         fill=_txt, justify="center",
                         font=(FONT_UI, 9, "bold"))
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
        # Re-entry guard for _fit_height. configure(height=) raises <Configure>,
        # whose handler calls _fit_height again; without this the two chase each
        # other. Set BEFORE adopt(), which fits on its way out.
        self._fitting = False
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
        self.bind("<Configure>", self._on_configure)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        self._hover = None
        self._hover_item = None
        # Where the pointer is, and where the card was last drawn. The card
        # follows the pointer now, and _draw_hover has two callers that carry no
        # event at all (redraw and <Leave>), so both live on the instance.
        self._hover_xy = None
        self._hover_drawn_xy = None
        self.bind("<Motion>", self._on_hover)
        self.bind("<Leave>", lambda _e: (setattr(self, "_hover", None),
                                         setattr(self, "_hover_item", None),
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
        # The hover card is a derived handle too, and the same hazard applies to
        # it as to `selected` and `ipad`: it holds a KEY and the display dict it
        # was drawn from. _fit_height below can configure(height=), which raises
        # <Configure> -> redraw() -> _draw_hover(), and _detail_lines on a key
        # whose device no longer exists does target["name"] on None -- a
        # TypeError raised inside a Tk callback, where there is nothing to catch
        # it.
        self._hover = self._hover_item = None
        # A different arrangement is a different world aspect, and switching one
        # in fires no <Configure> at all. Fit here or the canvas keeps the
        # previous desk's height until something else happens to resize it.
        self._fit_height()

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

    # set_ipad_size() is GONE, with the "iPad model" combobox that was its only
    # caller. It wrote through `self.ipad` -- "the first display of the first
    # device" -- so picking a model resized whatever screen happened to be
    # first, which on this desk is not necessarily an iPad at all. The named
    # iPad geometries are now offered on the right-click menu OF the screen
    # being pointed at, which is the same feature aimed at the right rectangle.

    def set_target_state(self, target_id, live, paired, portal_on=True):
        """This device's box colour, from the SAME truth table as its card.

        `live` is CONNECTED, not connected-and-routing. The caller used to
        collapse the portal into it -- `set_target_state(id, live and portal_on,
        paired)` -- which threw away the distinction the whole suppressed
        register exists to draw: with the portal down, a connected device and a
        merely-paired one both arrived here as `live=False, paired=True` and
        both came out full-strength amber, on the largest element in the window
        and directly contradicting the card underneath.

        portal_on defaults True so the three-argument form still means what it
        always meant (live -> green, paired -> amber, else grey) for any caller
        that has no portal opinion to offer.
        """
        state = target_state_name(portal_on, live, paired)
        if self.target_states.get(target_id) != state:
            self.target_states[target_id] = state
            if target_id == "ipad":
                self.ipad_state = state
            self.redraw()

    def set_ipad_state(self, live, paired, portal_on=True):
        self.set_target_state("ipad", live, paired, portal_on)

    # rotate() is GONE for the same reason as set_ipad_size(): it turned
    # targets[0]["displays"][0] and nothing else, so the global "Rotate" button
    # could not rotate the screen you had just clicked on. Rotation is now a
    # per-screen entry on that screen's own right-click menu.

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

    # The drawing is an aspect-fit of the desk, and at every width this window
    # has ever had it is WIDTH-bound: the scale comes from width / world-width,
    # and extra canvas height buys nothing but dead PANEL above and below the
    # picture. This canvas was nonetheless the only expanding thing in the left
    # column, so it collected 100% of the window's surplus height and could not
    # use a pixel of it. _fit_height asks for exactly the height the drawing
    # occupies, so the surplus never forms.
    FIT_MIN_H = 240
    FIT_MAX_H = 560

    def _fit_height(self):
        """Request the height that exactly fits the drawing at this width.

        No 0.94 here. _scale already applies that inset to the DRAWING; taking
        it a second time on the container would shrink the picture 6% while this
        change claims to leave it pixel-identical.
        """
        if getattr(self, "_fitting", False):
            return
        if getattr(self, "tk", None) is None:
            return  # config-only instance: adopt() is driven that way by tests
        self._world_bounds()
        avail = max(self.winfo_width(), 100)
        height = round(avail * (self.wy1 - self.wy0) /
                       max(1, self.wx1 - self.wx0))
        height = max(self.FIT_MIN_H, min(self.FIT_MAX_H, height))
        # 2px tolerance: rounding alone must not be able to start a
        # configure -> <Configure> -> configure loop.
        if abs(height - self.winfo_reqheight()) <= 2:
            return
        self._fitting = True
        try:
            self.configure(height=height)
        finally:
            self._fitting = False

    def _on_configure(self, _event):
        self._fit_height()
        self.redraw()

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
        """(fill, outline, label) for one rectangle.

        No second truth table: the state token was derived from
        device_state_colour on the way in, and TARGET_BOX_COLOURS is the only
        place a box colour is chosen. The outline IS the colour that device's
        dot wears on its card, so the canvas and the card cannot disagree in
        any state.
        """
        if key[0] == "local":
            return MON_FILL, MON_LINE, "#F5F5F8"
        state = self.target_states.get(key[1], "off")
        box = TARGET_BOX_COLOURS.get(state)
        if box is not None and state != "off":
            return box
        if key[1] == "mac":
            return "#262038", "#8A5CFF", "#D8C8FF"
        return TARGET_BOX_COLOURS["off"]

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
            # ONE SHORT LINE. Resolution, refresh, rotation and size used to
            # be stamped on every rectangle, which on a desk of portrait panels
            # meant four lines of text in a box narrower than the text. The
            # arrangement is a picture of where things ARE; the numbers belong
            # where they are asked for, which is the hover card.
            self.create_text(
                (x0 + x1) / 2, (y0 + y1) / 2, text=self._short_label(key, item),
                fill=text_color, justify="center", width=max(40, x1 - x0 - 10),
                font=(FONT_UI, 9, "bold"))
            if chosen:
                self.create_rectangle(
                    x1 - 10, y1 - 10, x1 + 2, y1 + 2,
                    fill=PORTAL, outline=PORTAL)
        self._draw_portals()
        self._draw_hint()
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
        # No "@ 60 Hz" unless a real 60 was read. For a local monitor the value
        # comes from Windows (EnumDisplaySettingsW) and is simply absent when
        # the adapter reports a default sentinel; for a managed device it is
        # what the user typed. An app that does not know a number says nothing
        # about it.
        hz = hz_label(item.get("refresh_hz"))
        lines = [f"{res_w} × {res_h}" + (f" @ {hz}" if hz else "")]
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
        # _draw_hover is ALSO called from redraw() and from <Leave>, neither of
        # which has an event, so the pointer position is remembered here rather
        # than passed down.
        drawn_at = self._hover_drawn_xy
        self._hover_xy = (event.x, event.y)
        key, item = self._hit_key(event)
        if key == self._hover:
            # Same surface: redraw only once the card is meaningfully behind the
            # pointer. Measured against where it was DRAWN, not against the last
            # motion event -- otherwise a slow drift of 2px at a time never
            # accumulates and the card never follows at all.
            if key is None or (drawn_at
                               and abs(event.x - drawn_at[0]) <= 24
                               and abs(event.y - drawn_at[1]) <= 24):
                return
        self._hover = key
        self._hover_item = item
        self._draw_hover()

    # How far off the pointer the card sits. It used to be pinned to the
    # canvas's bottom-left corner, which on this desk is 350-600px from the
    # rectangle being pointed at: you read the numbers in one corner about a
    # screen in another.
    HOVER_OFFSET = 16

    def _draw_hover(self):
        self.delete("hovercard")
        if not self._hover or not self._hover_item:
            return
        title, lines = self._detail_lines(self._hover, self._hover_item)
        pad = 9
        px, py = self._hover_xy or (10, int(self.winfo_height()) - 10)
        self._hover_drawn_xy = (px, py)
        text_id = self.create_text(
            0, 0, anchor="nw", justify="left",
            text=title + "\n" + "\n".join(lines),
            fill=FG, font=(FONT_UI, 8), tags="hovercard")
        bx0, by0, bx1, by1 = self.bbox(text_id)
        card_w = (bx1 - bx0) + 2 * pad
        card_h = (by1 - by0) + 2 * pad
        width = max(int(self.winfo_width()), 1)
        height = max(int(self.winfo_height()), 1)
        # Flip to the other side of the pointer at an edge rather than being
        # clipped by it, then clamp so a card wider than the canvas still starts
        # on screen.
        left = px + self.HOVER_OFFSET
        top = py + self.HOVER_OFFSET
        if left + card_w > width - 4:
            left = px - self.HOVER_OFFSET - card_w
        if top + card_h > height - 4:
            top = py - self.HOVER_OFFSET - card_h
        left = max(4, min(left, max(4, width - card_w - 4)))
        top = max(4, min(top, max(4, height - card_h - 4)))
        self.move(text_id, left + pad - bx0, top + pad - by0)
        self.create_rectangle(
            left, top, left + card_w, top + card_h,
            fill=HOVER_FILL, outline=HOVER_LINE, width=1, tags="hovercard")
        self.tag_raise(text_id)

    def _draw_hint(self):
        """The tell that this canvas answers a right-click.

        Drawn INSIDE the canvas, which is why it is here and not a Label: the
        left column is what sets this window's height, and a packed widget costs
        it real pixels. A canvas item costs none. BtPanel gives itself the same
        kind of tell."""
        self.create_text(
            8, max(12, int(self.winfo_height()) - 6), anchor="sw",
            text="drag to arrange  ·  right-click a screen to edit it",
            fill=MUTED, font=(FONT_UI, 8), tags="hint")

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
        # Dragging a screen moves the world bbox, so the aspect the canvas is
        # sized for is now stale -- and a drag fires no <Configure>. Fit HERE
        # and not in redraw(): _drag calls redraw() on every motion tick, and
        # changing the height mid-drag changes `oy` in _scale, so c2w maps the
        # cursor to a different world point and the rectangle jumps under it.
        self._fit_height()
        self.redraw()
        self.save()

    def save(self):
        # Every caller of save() has just changed the desk's shape.
        self._fit_height()
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
            bg=BG, fg=FG, font=(FONT_UI_SEMI, 15)).pack(
                anchor="w", padx=18, pady=(16, 2))
        tk.Label(
            self.top,
            text="Resolution, rotation, and refresh rate are independent from "
                 "the physical width used on the drag canvas.",
            bg=BG, fg=MUTED, font=(FONT_UI, 9)).pack(
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
                     font=(FONT_UI, 8, "bold")).pack(
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
                font=(FONT_UI, 9)).pack(side="left", padx=(0, 5), ipady=5)
        ttk.Combobox(
            frame, textvariable=values["rotation"], width=7,
            values=("0°", "90°", "180°", "270°"),
            state="readonly").pack(side="left", padx=(0, 5))
        tk.Entry(
            frame, textvariable=values["refresh_hz"], width=10,
            bg=CARD, fg=FG, insertbackground=FG, relief="flat",
            font=(FONT_UI, 9)).pack(side="left", padx=(0, 5), ipady=5)
        tk.Entry(
            frame, textvariable=values["physical_width"], width=13,
            bg=CARD, fg=FG, insertbackground=FG, relief="flat",
            font=(FONT_UI, 9)).pack(side="left", padx=(0, 5), ipady=5)
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
        self._refresh_lock = threading.Lock()
        self._conn_busy = False  # one connect-retry loop at a time
        self._connected = set()
        self._connected_names = []  # display names, for the compact view
        self._seen = {}  # mac -> (name, icon, controller), seen this session
        #                  kept in the list even after BlueZ purges an un-bonded
        #                  device, so a failed Connect never drops it from view.
        self._audio_levels = {}       # bluez sink levels, MAC -> 0..100
        self._audio_controls = {}     # live inline controls, keyed by MAC
        self._audio_level_pending = {}  # last release wins while SSH is busy
        self._audio_level_workers = set()
        self._audio_level_lock = threading.Lock()
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

        # wraplength is BOUND, not written down. The literal 500 that stood
        # here was measured against a 1120px window; at this app's own minsize
        # the column is ~460px and the same sentence quietly cost another line.
        _scan_help = tk.Label(
            self, text="Put headphones in pairing mode, Scan, then "
                       "RIGHT-CLICK a device: Connect, Rename, Blacklist, "
                       "Forget. Renames + blacklist are saved and survive "
                       "re-pairing.",
            bg=BG, fg=MUTED, font=(FONT_UI, 9), justify="left")
        _scan_help.pack(anchor="w", fill="x", padx=12, pady=(10, 0))
        bind_wraplength(_scan_help)

        options = ttk.LabelFrame(self, text="Radio options", padding=7)
        options.pack(fill="x", padx=12, pady=(7, 2))
        mode_row = tk.Frame(options, bg=BG)
        mode_row.pack(fill="x")
        tk.Label(mode_row, text="Setup", bg=BG, fg=FG,
                 font=(FONT_UI, 9)).pack(side="left")
        self.mode_combo = ttk.Combobox(
            mode_row, textvariable=self.radio_mode, state="readonly",
            values=("Single radio (recommended)", "Multiple radios"),
            width=25)
        self.mode_combo.pack(side="right")
        self.mode_combo.bind("<<ComboboxSelected>>", self._on_mode_changed)

        assign_row = tk.Frame(options, bg=BG)
        assign_row.pack(fill="x", pady=(6, 0))
        tk.Label(assign_row, text="iPad keyboard", bg=BG, fg=FG,
                 font=(FONT_UI, 9)).pack(side="left")
        self.hid_combo = ttk.Combobox(
            assign_row, textvariable=self.hid_radio, state="disabled",
            width=25)
        self.hid_combo.pack(side="right")
        self.hid_combo.bind("<<ComboboxSelected>>", self._on_hid_radio)

        mac_row = tk.Frame(options, bg=BG)
        mac_row.pack(fill="x", pady=(4, 0))
        tk.Label(mac_row, text="Managed Mac", bg=BG, fg=FG,
                 font=(FONT_UI, 9)).pack(side="left")
        self.mac_combo = ttk.Combobox(
            mac_row, textvariable=self.mac_radio, state="disabled",
            width=25)
        self.mac_combo.pack(side="right")
        self.mac_combo.bind("<<ComboboxSelected>>", self._on_mac_radio)

        scan_row = tk.Frame(options, bg=BG)
        scan_row.pack(fill="x", pady=(4, 0))
        tk.Label(scan_row, text="Scan from", bg=BG, fg=FG,
                 font=(FONT_UI, 9)).pack(side="left")
        self.scan_combo = ttk.Combobox(
            scan_row, textvariable=self.scan_radio, state="disabled",
            width=25)
        self.scan_combo.pack(side="right")
        self.scan_combo.bind("<<ComboboxSelected>>", self._on_scan_radio)
        self.radio_note = tk.StringVar(
            value="Single-radio compatibility is active.")
        _note_lbl = tk.Label(options, textvariable=self.radio_note, bg=BG,
                             fg=MUTED, font=(FONT_UI, 8), anchor="w",
                             justify="left")
        _note_lbl.pack(fill="x", pady=(5, 0))
        bind_wraplength(_note_lbl)
        # ---- which radios the VM actually holds -------------------------
        # A dongle that has been unplugged and plugged back in is claimed by
        # Windows, and VirtualBox only auto-captures at the moment a device
        # ARRIVES. From in here that was indistinguishable from a device that
        # simply would not connect -- nothing in the app had ever looked at the
        # host's USB list. Now it says so, and offers the one action that fixes
        # it. Nothing about this scans, pairs or connects anything.
        self.radio_usb = tk.StringVar(value="Checking which radios the VM "
                                            "holds…")
        _usb_lbl = tk.Label(options, textvariable=self.radio_usb, bg=BG,
                            fg=MUTED, font=(FONT_UI, 8), anchor="w",
                            justify="left")
        _usb_lbl.pack(fill="x", pady=(6, 0))
        bind_wraplength(_usb_lbl)
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
        # expand=False on BOTH the body and the tree, so the height=8 declared
        # just below is the height actually taken (189px measured). Expanding
        # made that number decorative and let the tree eat the right column's
        # whole surplus. 8 rows is more than the devices present, so nothing is
        # hidden by capping it. Deliberately NOT dynamic per refresh -- a tree
        # that resizes while a scan lands moves the row under the cursor.
        body.pack(fill="both", expand=False, padx=12, pady=PAD_MD)
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
        # expand stays TRUE here, and only here. pack's expand is not
        # axis-specific: with side="left" it is what gives the tree the leftover
        # WIDTH beside the scrollbar. Measured with a 800px-wide column,
        # expand=False opens a 216px hole between the tree and the scrollbar.
        # The height cap is already fully delivered by body above, which no
        # longer expands -- so this parcel is exactly reqheight either way.
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(body, command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.config(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", lambda e: self.connect())
        self.tree.bind("<Button-3>", self._popup)

        # A connected Bluetooth sink gets one compact DEVICE-level control.
        # This is deliberately separate from App.c_vol_var: that existing
        # slider remains OpenSpan's global Windows-stream gain. Device sliders
        # change only their exact PipeWire bluez sink, and send on release.
        self.audio_level_box = tk.Frame(self, bg=BG)
        self.audio_level_box.pack(fill="x", padx=12)
        # deferred: two VBoxManage calls must not sit in front of the first
        # paint, and the answer is worth having before anything is clicked
        self.after(1200, self._radio_usb_check)

        self.menu = tk.Menu(self, tearoff=0, bg=CARD, fg=FG,
                            activebackground=ACCENT_DIM,
                            activeforeground="#F1EBFF", bd=0)

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=12, pady=(0, 4))
        self.btn_scan = ttk.Button(bar, text="🔍 Scan", command=self.scan)
        self.btn_scan.pack(side="left")
        ttk.Button(bar, text="↻ Refresh", command=self.refresh).pack(
            side="left", padx=6)
        ttk.Checkbutton(bar, text="Show blacklisted", variable=self.show_blk,
                        command=self.refresh).pack(side="left", padx=8)
        # kept by name: restart_everything paints the wait on whichever button
        # was actually pressed, and this is the second of the two
        self.btn_restart_audio = ttk.Button(bar, text="⟳ Restart audio",
                                            command=self._restart_all)
        self.btn_restart_audio.pack(side="right")

        self.out = tk.Text(self, bg="#06090E", fg="#B8B3C2", height=5, bd=0,
                           font=("Consolas", 9), wrap="word",
                           insertbackground=FG)
        self.out.pack(fill="both", expand=False, padx=12, pady=(4, 10))
        self._log("Ready. Right-click a device for its actions.")
        self._set_radio_controls()
        self.refresh()

    # ---- radios the VM has lost ------------------------------------------
    def _radio_usb_apply(self, text, repairable):
        def apply():
            self.radio_usb.set(text)
            want = (f"Repair {repairable} radio"
                    + ("s" if repairable != 1 else "")
                    if repairable else "Repair radios")
            # A repair in flight owns this button's label. Writing the new
            # count STRAIGHT onto it would clobber "Repairing radios…" from
            # inside the very job that is doing the repairing; parking it
            # instead means the button comes back with the fresh count.
            if not rebase_button_busy(self.reclaim_btn, want):
                self.reclaim_btn.config(text=want)
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
            text, repairable = radio_status_text(state, config, VM)
            self._radio_usb_apply(text, repairable)
        threading.Thread(target=work, daemon=True).start()

    def _reclaim_radios(self):
        """Hand every radio the VM has lost back to it, and say what happened."""
        # Was a bare state="disabled": the button went flat and stayed flat for
        # however long the repair took, with nothing to say the app was working
        # rather than that the button had simply gone dead.
        done = (self.app.busy(self.reclaim_btn, "Repairing radios…")
                if self.app else None)

        def work():
            try:
                if not vm_running():
                    self._log("radios: the VM is not running — start it first.")
                    self._radio_usb_check()
                    return
                state = read_radio_state()
                if not state["filters"]:
                    self._log(f"radios: the configured VM “{VM}” has no "
                              "active USB filters.")
                    self._radio_usb_check()
                    return
                if not state["lost"] and not state["absent"]:
                    self._log(f"radios: all {len(state['mine'])} are already "
                              f"attached to the configured VM “{VM}” — "
                              "nothing to reclaim.")
                    self._radio_usb_check()
                    return
                config = self.app.canvas.config if self.app else {}
                for spec in state["absent"]:
                    self._log(f"radios: ABSENT — no host adapter matches "
                              f"{usb_filter_label(spec)}; no attach will be sent.")
                for device in state["captured"]:
                    self._log(
                        f"radios: CAPTURED but not delivered — "
                        f"{usb_label(device, config)}; no attach will be sent.")
                for device in state["unavailable"]:
                    self._log(
                        f"radios: {usb_label(device, config)} has unsupported "
                        f"host state {device.get('state') or 'unknown'}; no "
                        "attach will be sent.")
                for device in state["attachable"]:
                    self._log(f"radios: {usb_label(device, config)} is "
                              f"{device.get('state', '?')} on the host "
                              f"(filter “{device.get('filter', '?')}”, serial "
                              f"{device.get('serial') or 'none'}) — one attach "
                              "attempt allowed")
                for finding in state["ambiguous_filters"]:
                    names = ", ".join(finding["filters"])
                    self._log(
                        f"radios: FILTER AUDIT — {names} are identical active "
                        "filters without serials; reporting only, not rewriting.")
                self._radio_usb_apply(
                    f"Auditing ownership; {len(state['attachable'])} radio(s) "
                    "permit one attach attempt…", 0)
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
                                          outcome["repairable"])
                    self._log("radios: accepted and Captured requests are "
                              "never retried. Replug a Captured-but-not-"
                              "delivered adapter (restart Windows for a "
                              "built-in radio); do not restart the VM.")
                else:
                    self._radio_usb_check()
                if outcome["recovered"]:
                    self._log("radios: give BlueZ a few seconds to enumerate, "
                              "then Connect each device.")
                    if self.app:
                        self.app.ui(lambda: self.after(6000, self.refresh))
            finally:
                if done:
                    done()
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

    def _preview_audio_level(self, mac, raw):
        """Paint a slider value locally. Drag motion never performs SSH."""
        control = self._audio_controls.get(mac)
        if not control:
            return
        try:
            level = max(0, min(100, int(round(float(raw)))))
        except (TypeError, ValueError):
            return
        control["value_text"].set(f"{level}%")

    def _begin_audio_level_drag(self, mac):
        control = self._audio_controls.get(mac)
        if control:
            control["dragging"] = True

    def _release_audio_level_drag(self, mac):
        control = self._audio_controls.get(mac)
        if control:
            control["dragging"] = False
        self._commit_audio_level(mac)

    def _release_audio_level_key(self, event, mac):
        if event.keysym in {
                "Left", "Right", "Up", "Down", "Home", "End",
                "Prior", "Next"}:
            self._commit_audio_level(mac)

    def _sync_audio_controls(self, connected, levels=None):
        """Make one inline level slider for each visible connected headset."""
        if levels is not None:
            self._audio_levels = dict(levels)
        active = {mac for mac, _name in connected}
        for mac in list(self._audio_controls):
            if mac not in active:
                self._audio_controls.pop(mac)["row"].destroy()

        for mac, name in connected:
            level = self._audio_levels.get(mac)
            control = self._audio_controls.get(mac)
            if control is None:
                row = tk.Frame(self.audio_level_box, bg=BG)
                name_var = tk.StringVar()
                value_var = tk.DoubleVar(value=level if level is not None else 0)
                value_text = tk.StringVar()
                tk.Label(row, textvariable=name_var, bg=BG, fg=FG,
                         font=(FONT_UI, 9), anchor="w", width=22).pack(
                             side="left")
                tk.Label(row, text="device volume", bg=BG, fg=MUTED,
                         font=(FONT_UI, 8)).pack(side="left", padx=(2, 7))
                value_label = tk.Label(
                    row, textvariable=value_text, bg=BG, fg=ACCENT,
                    font=("Consolas", 9), width=4, anchor="e")
                value_label.pack(side="right")
                scale = ttk.Scale(
                    row, from_=0, to=100, variable=value_var,
                    command=lambda raw, address=mac:
                        self._preview_audio_level(address, raw))
                scale.pack(side="right", fill="x", expand=True, padx=(0, 7))
                scale.bind(
                    "<ButtonPress-1>",
                    lambda _event, address=mac:
                        self._begin_audio_level_drag(address))
                scale.bind(
                    "<ButtonRelease-1>",
                    lambda _event, address=mac:
                        self._release_audio_level_drag(address))
                scale.bind(
                    "<KeyRelease>",
                    lambda event, address=mac:
                        self._release_audio_level_key(event, address))
                control = {
                    "row": row, "name": name_var, "variable": value_var,
                    "value_text": value_text, "scale": scale,
                    "value_label": value_label, "dragging": False,
                }
                self._audio_controls[mac] = control
            control["name"].set(f"🎧 {name}"[:24])
            with self._audio_level_lock:
                local_change = control["dragging"] \
                    or mac in self._audio_level_workers \
                    or mac in self._audio_level_pending
            if level is None:
                if not local_change:
                    control["value_text"].set("--")
                    control["scale"].state(["disabled"])
            else:
                control["scale"].state(["!disabled"])
                if not local_change:
                    control["variable"].set(level)
                    control["value_text"].set(f"{level}%")
            # Repacking keeps the controls in the same order as the tree.
            control["row"].pack_forget()
            control["row"].pack(fill="x", pady=(2, 0))

    def _commit_audio_level(self, mac):
        """Queue one exact per-sink write after a slider/key release.

        If another release happens while SSH is in flight, keep only the latest
        value and send it immediately after the current request completes.
        """
        control = self._audio_controls.get(mac)
        if not control or mac not in self._connected or not re.fullmatch(
                r"[0-9A-F]{2}(?::[0-9A-F]{2}){5}", mac):
            return
        level = max(0, min(100, int(round(control["variable"].get()))))
        self._preview_audio_level(mac, level)
        with self._audio_level_lock:
            self._audio_level_pending[mac] = level
            if mac in self._audio_level_workers:
                return
            self._audio_level_workers.add(mac)

        def work():
            restart = False
            try:
                while True:
                    with self._audio_level_lock:
                        value = self._audio_level_pending.pop(mac, None)
                        if value is None:
                            return
                    command = (
                        "python3 /opt/openspan/openspan_bt.py "
                        "set-audio-level "
                        f"--device {mac} --level {value}")
                    failed = ""
                    try:
                        result = ssh_guest(
                            command, timeout=15, quiet=True,
                            show_result=False)
                        if result.returncode:
                            failed = (result.stderr or result.stdout
                                      or "").strip() \
                                or "guest command failed"
                    except Exception as exc:  # noqa: BLE001
                        failed = str(exc) or "guest command failed"
                    if failed:
                        self._log(f"headset level FAILED for {mac} — "
                                  + failed[-220:])
                        # Re-read the real sink level so a rejected write does
                        # not leave the optimistic preview looking committed.
                        if self.app:
                            self.app.ui(lambda: self.refresh(quiet=True))
                    else:
                        def remember(address=mac, committed=value):
                            self._audio_levels[address] = committed
                        if self.app:
                            self.app.ui(remember)
                        else:
                            remember()
                        self._log(f"headset level {mac}: {value}%")
            finally:
                # Never strand this MAC in the busy set. If anything outside
                # the expected SSH failure path raised after another release
                # queued a value, hand that last value to a replacement worker.
                with self._audio_level_lock:
                    self._audio_level_workers.discard(mac)
                    if mac in self._audio_level_pending:
                        self._audio_level_workers.add(mac)
                        restart = True
                if restart:
                    threading.Thread(target=work, daemon=True).start()
        threading.Thread(target=work, daemon=True).start()

    def _multi(self):
        return multi_radio_enabled(self.prefs)

    def _radio_display(self, radio):
        address = radio.get("address", "")
        label = self.prefs["radio_labels"].get(address)
        if not label:
            # Hardware identity only -- never the BlueZ alias. The alias is
            # the lane name the last assignment stamped onto the controller
            # ("OpenSpan iPad"), and this list is where lanes get picked, so
            # an alias here labels a radio with the job it happens to hold.
            label = radio.get("hardware") or "Bluetooth controller"
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
                    activeforeground="#F1EBFF", bd=0)
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
            self.app.restart_everything(log=self._log,
                                        button=self.btn_restart_audio)

    def refresh(self, quiet=False):
        with self._refresh_lock:
            if self._refreshing:
                # Never swallow a refresh: an in-flight pass may carry a
                # PRE-link snapshot; queue one trailing rerun instead.
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
                        self._sync_audio_controls([], None)
                        self.after(5000, self.refresh)  # retry until reachable
                    ui(apply_unreachable)
                    return
                rows = []
                radios = []
                audio_levels = {}
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
                if any(conn and "audio" in (icon or "").lower()
                       for _mac, _name, _paired, conn, icon, _ctrl in rows):
                    level_result = ssh_guest(
                        "python3 /opt/openspan/openspan_bt.py audio-levels",
                        timeout=12, quiet=True, show_result=False)
                    if level_result.returncode == 0:
                        try:
                            raw_levels = json.loads(
                                level_result.stdout or "{}")
                            if isinstance(raw_levels, dict):
                                parsed = {}
                                for address, value in raw_levels.items():
                                    address = str(address).upper()
                                    if re.fullmatch(
                                            r"[0-9A-F]{2}"
                                            r"(?::[0-9A-F]{2}){5}", address) \
                                            and isinstance(value, int) \
                                            and not isinstance(value, bool) \
                                            and 0 <= value <= 100:
                                        parsed[address] = value
                                audio_levels = parsed
                        except (TypeError, ValueError):
                            pass
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
                ui(lambda: self._apply_rows(rows, radios, audio_levels))
            finally:
                with self._refresh_lock:
                    self._refreshing = False
                    rerun = self._refresh_pending
                    self._refresh_pending = False
                if rerun:
                    self.refresh(quiet=True)  # the queued trailing rerun
        threading.Thread(target=work, daemon=True).start()

    def _apply_rows(self, rows, radios=None, audio_levels=None):
        """Rebuild the device list. UI thread only."""
        if radios is not None:
            self._radios = radios
            self._refresh_radio_choices()
        keep = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self._connected = set()
        self._device_radios = {}
        names = []
        connected_audio = []
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
            audio = "audio" in (icon or "").lower()
            typ = ("🎧 audio" if audio
                   else (icon or "device"))
            if blk:
                status, tag = "⛔ Blacklisted", "blacklisted"
            elif conn:
                status, tag = "● Connected", "connected"
                nconn += 1
                self._connected.add(mac)
                names.append(nm)
                if audio:
                    connected_audio.append((mac, nm))
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
        self._sync_audio_controls(connected_audio, audio_levels)
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
                      relief="flat", font=(FONT_UI, 10))
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
        # Provisional, so the window does not flash at a silly size while it is
        # being built. BOTH heights are replaced at the end of __init__ with the
        # measured content height -- see the layout-budget block down there.
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
        # Which thread IS the Tk thread. busy() and the row repaints are called
        # from both sides -- a click handler is already on it, a worker never is
        # -- and queueing a repaint that could just happen now costs up to a
        # 50ms _drain_ui tick on the one path the user is watching.
        self._ui_thread = threading.get_ident()
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
        self._ui_faults = 0     # how many queued closures have raised; see
        #                         _drain_ui -- capped so reporting cannot loop
        self._rederive_faults = 0   # ...and the same, for _rederive_height
        root.after(50, self._drain_ui)
        self._theme()

        # The whole UI lives inside self._full: a pinned header, then ONE pane
        # at a time beside a labelled rail. See PANE_SPEC for why every pane is
        # built here and merely hidden rather than created on demand.
        self._pane = None            # which pane is showing right now
        self._prev_pane = None       # where the Console button returns to
        self._panes = {}             # pane key -> its frame; ALL are built
        self._rail = {}              # pane key -> (accent bar, rail button)
        # Every portal control in the window, in build order. There is more than
        # one of them now -- see _portal_button -- and this list is the ONLY
        # thing _render_portal_button and _busy_portal iterate, so a surface
        # cannot exist that the single writer does not reach.
        self._portal_btns = []
        self._clip_warned = set()    # panes already reported as screen-clipped
        self._ready_state = None
        self._ipad_conn = None
        self._vol_ok = True
        self._vol_now = None
        self._vol_target = None
        full = tk.Frame(root, bg=BG)
        full.pack(fill="both", expand=True)
        self._full = full

        head = tk.Frame(full, bg=BG)
        # flush to the very top (frameless) + extra height = a full title-bar
        # drag band, not a thin strip. Whole band is bound to _drag_* below.
        # ipady=7 stays a literal on purpose: it is the frameless window's drag
        # band, deliberately oversized, and not part of the vertical rhythm.
        head.pack(fill="x", padx=16, pady=(0, PAD_SM), ipady=7)
        # The wordmark lockup: "Esoteric" in Lunar, "OS" in Arcane (the kit's
        # gradient rendered as its light stop -- Tk has no text gradients).
        # A custom app_label in settings still renders as one plain label.
        if APP_LABEL == "EsotericOS":
            _t1 = tk.Label(head, text="Esoteric", bg=BG, fg=FG,
                           font=(FONT_UI_SEMI, 18))
            _t1.pack(side="left")
            _t1b = tk.Label(head, text="OS", bg=BG, fg=PORTAL,
                            font=(FONT_UI_SEMI, 18))
            _t1b.pack(side="left")
        else:
            _t1 = tk.Label(head, text=APP_LABEL, bg=BG, fg=FG,
                           font=(FONT_UI_SEMI, 18))
            _t1.pack(side="left")
        _t2 = tk.Label(head, text="PC → iPad + Mac bridge", bg=BG, fg=MUTED,
                       font=(FONT_UI, 10))
        _t2.pack(side="left", padx=(10, 0), pady=(8, 0))
        # window controls: the caption is stripped (frameless), so THIS row is
        # the title bar. Tk buttons -> commands on the Tk thread (R1-safe); the
        # drag is the SetWindowPos header binding below (callback-free).
        _cl = tk.Button(head, text="✕", command=self._confirm_close, bg=BG,
                        fg=MUTED, bd=0, relief="flat", width=3, cursor="hand2",
                        font=(FONT_UI, 12), activebackground=DANGER,
                        activeforeground="#ffffff")
        _cl.pack(side="right", padx=(6, 0))
        _cl.bind("<Enter>", lambda e: _cl.config(bg=DANGER, fg="#ffffff"))
        _cl.bind("<Leave>", lambda e: _cl.config(bg=BG, fg=MUTED))
        _mn = tk.Button(head, text="—", command=self._minimize, bg=BG, fg=MUTED,
                        bd=0, relief="flat", width=3, cursor="hand2",
                        font=(FONT_UI, 11), activebackground=PANEL,
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
        # INDICATOR_ORDER, not a literal tuple: this row overflows its cavity at
        # the app's minimum width, and with no scrolling the packer drops the
        # tail. The order IS the priority, and the constant is where the reason
        # for it is written down and what the measurement in test_panes.py
        # reads. Do not reorder this loop; reorder the constant.
        for _k in INDICATOR_ORDER:
            _lb = tk.Label(indrow, text="", bg=BG, fg=MUTED,
                           font=("Consolas", 10))
            # The admin token LEADS the row and is empty whenever the app is
            # elevated -- and is_elevated() is resolved once per process and
            # cached, so on an elevated run that token is empty for the whole
            # session, not just this tick. Its trailing gap would then be a
            # permanent 14px indent that pushes the entire row out of line with
            # the title above it, which shares this padx=16. No text, no gap.
            _pad = (0, 0) if (_k == "admin" and is_elevated()) else (0, 14)
            _lb.pack(side="left", padx=_pad)
            self._ind[_k] = _lb

        # THE READINESS BANNER, and there is exactly one of it, HERE, in the
        # pinned header. One writer: _apply_poll, on a state change.
        #
        # It used to live in the Audio & status panel, which under a rail
        # becomes the Bluetooth pane -- so it would have vanished the moment any
        # other pane was showing. That is the W4 fatal verbatim: the banner was
        # then inside the console frame, which `_console_open = False` meant was
        # CONSTRUCTED and never MAPPED, and the default window had no readiness
        # surface at all. It cost a wave to find and it is one commit old.
        #
        # The header is the only place in a one-pane window where "is the bridge
        # up?" can be answered without first choosing where to look, so that is
        # where it goes, and the ~26px it costs is paid once rather than by the
        # pane that happens to be tallest.
        self.ready_lbl = tk.Label(full, text="◌  Starting…", bg=BG, fg=MUTED,
                                  font=(FONT_UI_SEMI, 11), anchor="w")
        self.ready_lbl.pack(fill="x", padx=16, pady=(0, PAD_XS))
        # transient / call-to-action line (Broadcasting…, errors, hints)
        self.status = tk.StringVar(value="Checking…")
        tk.Label(full, textvariable=self.status, bg=BG, fg=ACCENT,
                 font=("Consolas", 10), anchor="w").pack(
            fill="x", padx=16, pady=(0, PAD_MD))

        # ---- the rail, and the ONE pane it shows ---------------------------
        # The two-column split is gone. Everything below the header is a narrow
        # labelled rail plus exactly one pane; the window's height follows the
        # pane on show and is re-derived on every switch (_rederive_height).
        main = tk.Frame(full, bg=BG)
        main.pack(fill="both", expand=True, padx=10, pady=PAD_SM)
        # main / bridge_col / bridge keep expand=True. They are the cavity, not
        # the sponge: the surplus has to reach the designated spacer at the
        # bottom of `bridge`, and taking expand off any of them would strand it
        # in `full` instead.
        #
        # The rail deliberately does NOT expand and its height is NOT propagated
        # away: it is measured alongside the panes, so the window can never be
        # sized shorter than the control that navigates it.
        rail = tk.Frame(main, bg=PANEL)
        rail.pack(side="left", fill="y", padx=(0, PAD_LG))
        for _key, _label in PANE_SPEC:
            # WORDS, not a glyph alone. This app is opened rarely enough that a
            # bare icon is a memory test every single time.
            _row = tk.Frame(rail, bg=RAIL_REST)
            _row.pack(fill="x")
            _bar = tk.Frame(_row, bg=RAIL_REST, width=3)
            _bar.pack(side="left", fill="y")
            # activebackground=RAIL_HOVER, not CARD. For a tk.Button the ACTIVE
            # state is the pointer being over it, so activebackground IS the
            # hover colour -- and CARD is what select_pane paints the SELECTED
            # item, so hovering an inactive pane used to make it look exactly
            # like the pane you were already on.
            _btn = tk.Button(_row, text=_label, bg=RAIL_REST, fg=MUTED, bd=0,
                             relief="flat", anchor="w", width=13, padx=10,
                             pady=6, cursor="hand2", font=(FONT_UI, 10),
                             highlightthickness=0, activebackground=RAIL_HOVER,
                             activeforeground=FG,
                             command=lambda k=_key: self.select_pane(k))
            # PRESSED feedback, which these had none of. ttk buttons got theirs
            # from a style map (see PRESS); a tk.Button has no map, and its own
            # press handling only sets relief -- which is "flat" here, so a
            # click moved nothing at all. Tk does put the widget in the ACTIVE
            # state while the button is held, so swapping activebackground for
            # the duration of the press is what actually gets drawn. Navigation
            # needs this as much as any action does: these five are the only
            # controls that are guaranteed to be on screen.
            _btn.bind("<ButtonPress-1>",
                      lambda _e, b=_btn: b.config(activebackground=RAIL_PRESS))
            _btn.bind("<ButtonRelease-1>",
                      lambda _e, b=_btn: b.config(activebackground=RAIL_HOVER))
            _btn.pack(side="left", fill="x", expand=True)
            self._rail[_key] = (_bar, _btn)
        bridge_col = tk.Frame(main, bg=BG)
        bridge_col.pack(side="left", fill="both", expand=True)
        bridge = tk.Frame(bridge_col, bg=BG)
        bridge.pack(fill="both", expand=True)
        # No pane heading label. The rail already names the pane in words, in
        # the one place that is always visible, and a second copy of the same
        # word inside the pane is precisely the "too much at once" this wave
        # exists to remove.
        pane_desk = tk.Frame(bridge, bg=BG)
        pane_devices = tk.Frame(bridge, bg=BG)
        pane_bluetooth = tk.Frame(bridge, bg=BG)
        pane_system = tk.Frame(bridge, bg=BG)
        pane_console = tk.Frame(bridge, bg=PANEL)
        self._panes = {"desk": pane_desk, "devices": pane_devices,
                       "bluetooth": pane_bluetooth, "system": pane_system,
                       "console": pane_console}

        # ---- console pane --------------------------------------------------
        # Was a fixed 390px strip pinned to the right edge, toggled by widening
        # the whole window from 1120 to 1520. It is an ordinary pane now: the
        # width literals and _set_win_width are gone with it, and the console is
        # finally a thing you can look at rather than a thing you make room for.
        chead = tk.Frame(pane_console, bg=PANEL)
        chead.pack(fill="x", padx=10, pady=(PAD_MD, 0))
        tk.Label(chead, text="Console — every command the app runs", bg=PANEL,
                 fg=MUTED, font=(FONT_UI, 9, "bold")).pack(
            side="left", pady=(0, 4))
        ttk.Button(chead, text="Clear", width=6,
                   command=self._console_clear).pack(side="right")
        cwrap = tk.Frame(pane_console, bg=PANEL)
        cwrap.pack(fill="both", expand=True, padx=10, pady=(0, 12))
        self.console = tk.Text(cwrap, bg="#05080D", fg="#B8B3C2", bd=0,
                               font=("Consolas", 9), wrap="word",
                               state="disabled", insertbackground=FG)
        csb = ttk.Scrollbar(cwrap, command=self.console.yview)
        csb.pack(side="right", fill="y")
        self.console.config(yscrollcommand=csb.set)
        self.console.pack(side="left", fill="both", expand=True)
        self.console.tag_config("ts", foreground="#6E687A")
        self.console.tag_config("cmd", foreground="#B28DFF")
        self.console.tag_config("ok", foreground=ACCENT)
        self.console.tag_config("err", foreground=DANGER)
        self.console.tag_config("bt", foreground=PORTAL)
        self.console.tag_config("event", foreground=FG)
        self.console.tag_config("info", foreground=MUTED)
        set_log_sink(self._log_sink)
        self.log("event", f"{APP_LABEL} started.")

        # ---- Bluetooth pane ------------------------------------------------
        # Built BEFORE the canvas, exactly as it was: BtPanel.__init__ ends in
        # refresh(), whose worker can reach App._refresh_all_device_paired --
        # so its construction order relative to self.canvas is load-bearing and
        # is left alone. It carries no heading label of its own any more; the
        # rail says "Bluetooth".
        self._build_audio_panel(pane_bluetooth)
        self.bt_panel = BtPanel(pane_bluetooth, app=self)
        self.bt_panel.pack(fill="both", expand=False)

        # ---- Desk pane: the arrangement ------------------------------------
        #
        # arr_wrap and the canvas inside it were the ONLY expanding chain in the
        # left column, so 100% of the window's surplus height landed in a canvas
        # whose aspect-fit drawing is width-bound and cannot grow into it. That
        # is the mechanical reason this window was 2120px tall. Capping only the
        # canvas would just move the void from PANEL-coloured to CARD-coloured;
        # both flags come off together or the change delivers nothing.
        arr_wrap = tk.Frame(pane_desk, bg=CARD, bd=0)
        arr_wrap.pack(fill="both", expand=False, padx=8, pady=PAD_MD)
        # The Label that used to sit here is gone. Its whole content was a
        # pointer to a button ("set them in Screen sizes...") -- a packed widget
        # in the column that sets this window's height, spending real pixels to
        # name another widget. The canvas now draws its own one-line tell,
        # inside itself, for free.
        self.canvas = MultiArrangeCanvas(
            arr_wrap, on_change=self._portal_changed, height=270)
        # Right-click is bound HERE, not inside MultiArrangeCanvas: the handler
        # opens MacDisplayEditor, dark_prompt and the Windows re-read, none of
        # which the canvas knows about. The canvas keeps only its on_change
        # contract.
        self.canvas.bind("<Button-3>", self._canvas_menu)
        # Built ONCE and kept -- these two, and every cascade hung off them (see
        # _fill_surface_menu). Tk garbage-collects an unreferenced cascade out
        # from under a posted menu; BtPanel keeps `self.assign_menu` for exactly
        # this reason.
        self._surface_menu = tk.Menu(self.root, tearoff=0,
                                     font=(FONT_UI, 10), **MENU_STYLE)
        self._desk_menu = tk.Menu(self.root, tearoff=0,
                                  font=(FONT_UI, 10), **MENU_STYLE)
        # The three cascades are built ONCE here too, and repopulated in place.
        # tkinter's Menu.delete deletes the entries' Tcl command objects but
        # NOT a cascaded submenu widget, and the submenu stays in its master's
        # children dict forever -- so building a fresh tk.Menu per popup
        # stranded about two Menu widgets and twenty Tcl commands on every
        # right-click, for the life of the process.
        self._res_menu = tk.Menu(self._surface_menu, tearoff=0, **MENU_STYLE)
        self._hz_menu = tk.Menu(self._surface_menu, tearoff=0, **MENU_STYLE)
        self._device_menu = tk.Menu(self._desk_menu, tearoff=0, **MENU_STYLE)
        # The device CARDS' menu, and it is mastered on ROOT for a reason that
        # is not stylistic: _rebuild_device_rows destroys and recreates every
        # card frame, and it is reached from _apply_device_rows on the 3-second
        # poll tick -- which can fire inside tk_popup's own nested event loop.
        # A menu whose master is destroyed underneath a posted menu is a crash,
        # not a cosmetic glitch. Built once and kept, like the two above.
        self._card_menu = tk.Menu(self.root, tearoff=0,
                                  font=(FONT_UI, 10), **MENU_STYLE)

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
        prow.pack(fill="x", padx=8, pady=(PAD_MD, 0))
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
        # Vertical stack, so expand=False costs no width: fill="both" still
        # gives the canvas the whole card width, and _fit_height sizes it to
        # exactly the drawing it can actually render there.
        self.canvas.pack(fill="both", expand=False, padx=8, pady=PAD_MD)

        # ---- the portal control, a SECOND time, floating on the Desk ---------
        # Doug: *"Duplicate start portal button linked to same backend and place
        # floating in field of Desk at bottom"*
        #
        # The Desk is where he actually works -- dragging screens and
        # right-clicking them -- and W7 moved the ctl grid that owns the original
        # button into the System pane. So both the control AND the full-strength
        # amber that explains why nothing is bridging (see _render_portal_button)
        # went two clicks away from the work.
        #
        # A duplicate control surface has broken this app before: the old global
        # device row kept its own paired-state, so acting through one left the
        # other showing the device as still paired (the comment on the ctl frame
        # below records it). The rule that came out of that is ONE WRITER and one
        # builder feeding every surface -- which is exactly what _portal_button
        # and _render_portal_button are. This button forms no opinion of its own
        # about anything: not its text, not its style, not the backend.
        #
        # place(), not pack(): the placer never propagates a size to its master,
        # so this costs the Desk pane -- and therefore the W1 height budget --
        # exactly zero pixels. It lands in the letterbox strip the aspect fit
        # leaves below the drawing: _fit_height asks for the height the picture
        # occupies, _scale then insets the drawing a further 6%, and _world_bounds
        # carries a >=180-unit pad on every side on top of that. MEASURED on
        # this desk at the default 1120px window: a 940x493 canvas whose lowest
        # screen rectangle ends at y=393, against a button occupying y=454..485.
        # 61px of clearance, and no rectangle overlapped at all. test_panes.py
        # re-measures it against the live arrangement rather than trusting this
        # paragraph, and asserts the pane's height is unchanged either way.
        #
        # It is a CHILD of the canvas so it travels with it, and it is a widget
        # rather than a create_window item so redraw()'s delete("all") -- which
        # removes canvas items only -- cannot take it. The hint line
        # ("drag to arrange · right-click a screen to edit it") is anchored at
        # the bottom-LEFT corner; this is centred, and stays clear of it at every
        # width the window can have.
        self.desk_portal_btn = self._portal_button(self.canvas, width=17)
        self.desk_portal_btn.place(relx=0.5, rely=1.0, anchor="s", y=-8)

        # The global row that stood here -- "iPad: [model]  Rotate  Configure
        # Mac displays...  Screen sizes..." -- is DELETED, and two standing bugs
        # went with it rather than moving somewhere else:
        #
        #   * Rotate called canvas.rotate(), which only ever turned
        #     targets[0]["displays"][0]. The button could not rotate the screen
        #     you had just clicked on.
        #   * "Configure Mac displays..." passed no device_id, so it resolved to
        #     the FIRST device whichever one you meant.
        #
        # Both are now per-screen entries on that screen's own right-click menu,
        # which cannot address the wrong rectangle because it is opened ON the
        # right one. "Screen sizes..." survives on the empty-canvas menu: the
        # hit test returns only the TOPMOST rect, so a device display parked
        # over a monitor makes that monitor unreachable by right-click, and the
        # all-surfaces table is the escape hatch.

        # ---- System pane: bridge controls ----------------------------------
        # The four connection verbs live ONLY on each device's own row in the
        # Devices pane -- there is deliberately no second global copy of them
        # here. A duplicate row kept its own separate paired-state, so unpairing
        # via one left the other still showing the device as paired.
        ctl = tk.Frame(pane_system, bg=BG)
        ctl.pack(fill="x", padx=16, pady=(PAD_XS, PAD_SM))
        self.vm_btn = ttk.Button(ctl, text="Start VM", command=self.toggle_vm)
        self.vm_btn.grid(row=0, column=0, sticky="ew", padx=3, pady=PAD_XS)
        # Built through the same factory as the floating Desk copy, so both are
        # registered with the one writer and both carry the one command.
        self.portal_btn = self._portal_button(ctl)
        self.portal_btn.grid(row=0, column=1, sticky="ew", padx=3, pady=PAD_XS)
        ttk.Button(ctl, text="Edit keymap",
                   command=lambda: os.startfile(KEYMAP)).grid(
            row=0, column=2, sticky="ew", padx=3, pady=PAD_XS)
        self.invert_scroll = tk.BooleanVar(
            value=bool(load_setting("scroll_invert", False)))
        ttk.Checkbutton(ctl, text="⇅  Invert scroll wheel",
                        variable=self.invert_scroll,
                        command=self._on_invert_scroll).grid(
            row=1, column=0, columnspan=3, sticky="w", padx=5,
            pady=(PAD_XS, PAD_XS))
        self.cross_button = tk.BooleanVar(
            value=bool(self.canvas.config.get(
                "cross_requires_side_button", False)))
        ttk.Checkbutton(
            ctl, text="🖱  Hold a mouse side button to move between machines",
            variable=self.cross_button,
            command=self._on_cross_button).grid(
            row=2, column=0, columnspan=3, sticky="w", padx=5,
            pady=(0, PAD_XS))
        self.button_jumps = tk.BooleanVar(
            value=bool(self.canvas.config.get(
                "side_button_jumps_nearest", False)))
        ttk.Checkbutton(
            ctl, text="↦  …and jump straight to the nearest screen  "
                      "(recommended for complex arrangements)",
            variable=self.button_jumps,
            command=self._on_button_jumps).grid(
            row=3, column=0, columnspan=3, sticky="w", padx=(26, 5),
            pady=(0, PAD_XS))
        for c in range(3):
            ctl.columnconfigure(c, weight=1)

        # ---- Devices pane: one row per device, built from the config --------
        # Nothing here is per-device-type. Every device the user has added gets
        # the identical four verbs against its own radio, port and bonds.
        self._dev_frame = _section(pane_devices, "Devices",
                                   pady=(PAD_XS, PAD_XS))
        self._dev_rows = {}
        self._dev_body = tk.Frame(self._dev_frame, bg=BG)
        self._dev_body.pack(fill="x")
        addrow = tk.Frame(self._dev_frame, bg=BG)
        addrow.pack(fill="x", pady=(PAD_MD, 0))
        ttk.Button(addrow, text="＋  Add device",
                   command=self._add_device_dialog).pack(side="left")
        tk.Label(addrow,
                 text="Each device gets its own radio, its own advertisement "
                      "and its own bonds. No software is installed on it.",
                 bg=BG, fg=MUTED, font=(FONT_UI, 8)).pack(
            side="left", padx=(10, 0))
        self._rebuild_device_rows()

        # ---- System control: every backend action, nothing hidden ----
        # The title names what the line under it actually reports. It used to
        # say "System control" over a readout claiming five things, four of
        # which were said better somewhere else and one of which ("Mac ● up")
        # could never be true. What is left is the daemon roll-up, so that is
        # what the title says.
        sysf = _section(pane_system, "System control — device daemons")
        self.sys_status = tk.StringVar(value="…")
        tk.Label(sysf, textvariable=self.sys_status, bg=BG, fg=MUTED,
                 font=("Consolas", 8), anchor="w", justify="left").pack(
            fill="x", pady=(0, PAD_SM))
        sg = tk.Frame(sysf, bg=BG)
        sg.pack(fill="x")
        sysbtns = [("Stop VM", self.stop_vm),
                   ("Cold-restart VM", self.cold_restart_vm),
                   ("Restart keyboard", self.restart_keyboard),
                   ("Restart audio", self.restart_audio_btn),
                   ("⏻ Shut down everything", self.shutdown_all)]
        # Kept by label, not built anonymously: every one of these spawns a
        # worker that takes seconds, and busy() needs the widget to say so on.
        self._sysbtn = {}
        for i, (label, fn) in enumerate(sysbtns):
            _b = ttk.Button(sg, text=label, command=fn)
            _b.grid(row=i // 3, column=i % 3, sticky="ew", padx=3, pady=PAD_XS)
            self._sysbtn[label] = _b
        for c in range(3):
            sg.columnconfigure(c, weight=1)

        # ---- Radio ownership mode (switched via a clean reboot) ----
        mode = _section(pane_system, "Bluetooth radio")
        self.mode_lbl = tk.Label(mode, bg=BG, fg=FG, font=(FONT_UI, 10),
                                 anchor="w")
        self.mode_lbl.pack(fill="x")
        # ONE line. The two-line version spent 32px to say in 152 characters
        # what 81 say, and "(iPad bridge + command station, near-bare-metal)"
        # was the parenthesis doing the spending. The label right above already
        # names whichever mode is active; this only has to name the other one.
        _mode_note = tk.Label(
            mode, bg=BG, fg=MUTED, font=(FONT_UI, 8), anchor="w",
            justify="left",
            text="Station = the app owns the radio. Windows = Bluetooth + "
                 "audio. Switching reboots.")
        _mode_note.pack(fill="x", pady=(PAD_XS, PAD_MD))
        bind_wraplength(_mode_note)
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

        # ---- Window management: the ported EsotericOS feature set ----------
        # OFF until asked. The portal already captures this keyboard and a
        # rival KVM hooks it too; a third low-level hook arriving unannounced
        # is how a working desk breaks. The switch is the whole disclosure.
        wm = _section(pane_system, "Window management")
        self.wm_state = tk.StringVar(value="off — chords are not intercepted")
        wmrow = tk.Frame(wm, bg=BG)
        wmrow.pack(fill="x")
        self.wm_btn = ttk.Button(wmrow, text="Turn on window chords",
                                 command=self._toggle_window_chords)
        self.wm_btn.pack(side="left")
        tk.Label(wmrow, textvariable=self.wm_state, bg=BG, fg=MUTED,
                 font=(FONT_UI, 8), anchor="w").pack(side="left", padx=(10, 0))
        self.wm_binds = tk.StringVar(value="")
        _wmb = tk.Label(wm, textvariable=self.wm_binds, bg=BG, fg=MUTED,
                        font=("Consolas", 8), anchor="w", justify="left")
        _wmb.pack(fill="x", pady=(PAD_XS, 0))
        bind_wraplength(_wmb)

        # Zoom rides its own switch: it installs a MOUSE hook, which is a
        # different risk from the keyboard one and worth failing separately.
        # Spaces hides windows, which is the only thing in this section that
        # can lose work. Its switch says so, and turning it OFF restores
        # everything before it releases anything.
        srow = tk.Frame(wm, bg=BG)
        srow.pack(fill="x", pady=(PAD_SM, 0))
        self.spaces_state = tk.StringVar(
            value="off — every window stays visible")
        self.spaces_btn = ttk.Button(srow, text="Turn on separate Spaces",
                                     command=self._toggle_spaces)
        self.spaces_btn.pack(side="left")
        tk.Label(srow, textvariable=self.spaces_state, bg=BG, fg=MUTED,
                 font=(FONT_UI, 8), anchor="w").pack(side="left", padx=(10, 0))

        zrow = tk.Frame(wm, bg=BG)
        zrow.pack(fill="x", pady=(PAD_SM, 0))
        self.zoom_state = tk.StringVar(value="off — hold Alt and scroll "
                                             "does nothing yet")
        self.zoom_btn = ttk.Button(zrow, text="Turn on Alt+scroll zoom",
                                   command=self._toggle_screen_zoom)
        self.zoom_btn.pack(side="left")
        tk.Label(zrow, textvariable=self.zoom_state, bg=BG, fg=MUTED,
                 font=(FONT_UI, 8), anchor="w").pack(side="left", padx=(10, 0))

        # ---- Modules -------------------------------------------------------
        # EsotericOS's optional modules, drawn by the HOST. A module publishes
        # rows and never touches Tk, so nothing built here can be reached by
        # module code -- which is the only reason "an optional module can fail
        # without taking the host down" is a true statement rather than a hope.
        # The AI usage readout used to be two hardcoded labels right here; it
        # is now the agent-monitor module, and this section would draw a second
        # module the same way without knowing anything about it.
        self.modules_box = _section(pane_system, "Modules")
        self.module_rows = {}
        self._module_host = None

        # THE designated spacer, and the only expanding child of `bridge`.
        # Nothing is drawn in it. It exists so the window can still be dragged
        # taller without any panel distorting to absorb the extra height, and so
        # `bridge` never has zero expanding children -- a packer cavity with no
        # expanding slave hands its surplus back up the tree, which is how the
        # sponge moved around the last time this was tuned.
        #
        # It is packed LAST and never unpacked, which is also what makes it the
        # anchor for the panes: select_pane packs the visible pane with
        # `before=self._bridge_spacer`, so a pane selected an hour into the
        # session still lands ABOVE the spacer rather than under it. The five
        # panes themselves never expand -- the surplus is the spacer's, whichever
        # pane is showing.
        self._bridge_spacer = tk.Frame(bridge, bg=BG, height=0)
        self._bridge_spacer.pack(fill="both", expand=True)
        # ...and now open on the pane he was last using.
        self.select_pane(load_last_pane(), rederive=False, remember=False)

        # ---- footer: which build am I looking at? --------------------------
        # It lives in the window CHROME, not in a pane, so it is on every pane
        # by construction rather than by five copies that drift apart. Blue is
        # reserved for it: nothing else in this palette is blue, so a version
        # string can never be misread as a state colour.
        foot = tk.Frame(full, bg=BG)
        foot.pack(side="bottom", fill="x", padx=16, pady=PAD_MD)
        _stamp, _is_test = build_stamp()
        if _is_test:
            tk.Label(foot, text="TEST BUILD", bg=BG, fg=BUILD_TEST_YELLOW,
                     font=(FONT_UI_SEMI, 8)).pack(side="left", padx=(0, 6))
        self.build_lbl = tk.Label(foot, text=_stamp, bg=BG, fg=BUILD_BLUE,
                                  font=("Consolas", 8))
        self.build_lbl.pack(side="left")
        tk.Label(foot, text="open source · MIT · nothing phones home",
                 bg=BG, fg="#6E687A", font=(FONT_UI, 8)).pack(side="right")

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
        # ---- layout budget: the window's height follows MEASURED content ----
        # Nothing in this file used to set a height. The 1120x930 at the top was
        # a number chosen once; the console toggle parsed the height back out of
        # geometry() and put it straight back, so it never changed again. And
        # minsize(940, 680) permitted a window far SHORTER than the content
        # actually needs -- at which size "System control" and "Bluetooth radio"
        # simply do not get packed. There is no scrolling anywhere by design, so
        # there is no scrollbar, no clipped edge, and no way to find out they
        # exist. Deriving both numbers from the built content is what closes
        # that. Width behaviour is untouched.
        #
        # What this measures now is the HEADER plus ONE PANE, whichever pane was
        # restored above -- not the sum of every panel in the app. The identical
        # measurement runs again on every pane switch; see _rederive_height,
        # which is the same three steps against the same two functions.
        self.root.update_idletasks()      # let the packer place everything
        self.canvas._fit_height()         # canvas now knows its real width
        self.root.update_idletasks()      # ...and its height propagates upward
        content_h = full.winfo_reqheight()
        avail_h = work_area_height(self.root.winfo_screenheight())
        geom_h, min_h, over_budget, clipped = window_height_plan(
            content_h, avail_h)
        # ...and the floor no pane may go under, so the shortest pane in the app
        # cannot clip the tallest modal its own menu opens. One policy, one
        # function, both call sites (see pane_height_floor).
        _floor = pane_height_floor(avail_h)
        geom_h, min_h = max(geom_h, _floor), max(min_h, _floor)
        self._content_h = content_h
        self._content_clipped = clipped
        # minsize FIRST, for the same reason _rederive_height does it in that
        # order: the provisional minsize(940, 680) at the top of __init__ is
        # TALLER than a short pane's derived height, and Tk clamps geometry() to
        # whatever minsize is in force when the call is made. The window is
        # already on screen while it is being built, so the wrong order is a
        # visible one-frame jump, not a theoretical one.
        self.root.minsize(940, min_h)
        self.root.geometry(f"1120x{geom_h}")
        _emit("info", f"layout: pane '{self._pane}' content height "
                      f"{content_h}px, canvas "
                      f"{self.canvas.winfo_reqheight()}px" +
                      (f" — OVER the {LAYOUT_MAX_CONTENT_H}px budget"
                       if over_budget else ""))
        if clipped:
            # Loud on purpose. The alternative -- sizing to the content and
            # letting the window hang off the screen -- left the bottom panels
            # unreachable with no way to shrink to them.
            self._clip_warned.add(self._pane)
            _emit("err", f"This screen is {avail_h}px tall and the window needs "
                         f"{content_h}px. The bottom {content_h - avail_h}px "
                         "cannot be shown — panels below the fold are cut off.")
        self._tick()
        threading.Thread(target=self._module_worker,
                         name="esotericos-modules", daemon=True).start()
        self._wm_host = None
        self._zoom = None
        self._spaces = None
        # None means "no reading yet", so the first poll records rather than
        # reports. Otherwise every launch would announce a change against
        # nothing.
        self._display_sig = None
        self.ui(self._watch_displays)
        self.ui(self._show_window_chords)
        self.ui(self._autostart_window_features)

    # Chords and zoom come up ON. That reverses the shipping default, and
    # deliberately: Doug drove both live and asked for the program to start
    # in the state he tested. Spaces stays opt-in because it HIDES windows,
    # and a feature that can make work vanish should be a decision, not a
    # side effect of launching. Every switch still turns off in one click,
    # and WINDOW_FEATURE_AUTOSTART=0 in the environment disables the lot.
    def _autostart_window_features(self):
        if os.environ.get("WINDOW_FEATURE_AUTOSTART", "1").strip() == "0":
            _emit("info", "window features left off (WINDOW_FEATURE_AUTOSTART=0).")
            return
        for arm in (self._toggle_window_chords, self._toggle_screen_zoom,
                    self._toggle_spaces):
            try:
                arm()
            except Exception as exc:  # noqa: BLE001
                # One feature failing to arm must never cost the other one,
                # and must never stop the app from finishing startup.
                _emit("err", f"window feature did not start: {exc}")

    # ---- window management (ported EsotericOS features) --------------------
    # The host is built lazily and started ONLY from the button below. Nothing
    # here installs a hook at startup: see the section comment in the pane.

    def _window_host(self):
        """The hotkey host, built on first use. None when unavailable."""
        if self._wm_host is None:
            try:
                import hotkey_host
                self._wm_host = hotkey_host.HotkeyHost(
                    hotkey_host.WindowActions())
            except Exception as exc:  # noqa: BLE001
                _emit("err", f"window chords unavailable: {exc}")
                return None
        return self._wm_host

    def _show_window_chords(self):
        """Paint the binding table and any collisions. Reads only."""
        host = self._window_host()
        if host is None:
            self.wm_binds.set("")
            self.wm_state.set("unavailable on this build")
            self.wm_btn.state(["disabled"])
            return
        by_command = {}
        for chord, command in host.bindings().items():
            by_command.setdefault(command.rsplit(".", 1)[-1], []).append(chord)
        self.wm_binds.set("   ".join(
            f"{name}: {' / '.join(chords)}"
            for name, chords in sorted(by_command.items())))
        collisions = host.collisions()
        if collisions:
            _emit("err", f"window chords: {len(collisions)} collision(s) — "
                         "two commands claim one chord.")

    def _toggle_window_chords(self):
        """Start or stop the hook. The ONE place either happens."""
        host = self._window_host()
        if host is None:
            return
        if host.is_running:
            host.stop()
            self.wm_state.set("off — chords are not intercepted")
            self.wm_btn.config(text="Turn on window chords")
            _emit("ok", "window chords off — the keyboard is untouched.")
            return
        # start() refuses rather than stacking when another owner holds the
        # hook; report that refusal instead of pretending it worked.
        if not host.start():
            self.wm_state.set("refused — another hook owner holds the keyboard")
            _emit("err", "window chords refused: this process already has a "
                         "keyboard hook owner. Stop the portal and retry.")
            return
        self.wm_state.set("ON — chords are intercepted")
        self.wm_btn.config(text="Turn off window chords")
        _emit("ok", "window chords on — try Ctrl+Win+Alt+Numpad 4 on a "
                    "focused window.")

    def _toggle_spaces(self):
        """Enable or disable separate Spaces. Disable ALWAYS restores."""
        try:
            import spaces
        except Exception as exc:  # noqa: BLE001
            _emit("err", f"Spaces unavailable: {exc}")
            self.spaces_btn.state(["disabled"])
            return
        if self._spaces is not None and self._spaces.enabled:
            restored = self._spaces.disable()
            self.spaces_state.set("off — every window stays visible")
            self.spaces_btn.config(text="Turn on separate Spaces")
            _emit("ok", "separate Spaces off — every hidden window is back "
                        f"({restored} restored).")
            return
        try:
            if self._spaces is None:
                self._spaces = spaces.SpacesModule()
            monitors = spaces._live_monitors()
            windows = spaces._live_windows(monitors)
            # enable() wants monitor IDENTITIES, not the live records; the
            # records carry the work areas that _live_windows needs.
            self._spaces.enable([m.id for m in monitors], windows)
        except Exception as exc:  # noqa: BLE001
            # Never leave the feature half-enabled: anything hidden during a
            # failed enable comes straight back.
            try:
                if self._spaces is not None:
                    self._spaces.disable()
            except Exception:  # noqa: BLE001
                pass
            self.spaces_state.set("refused — see the console")
            _emit("err", f"separate Spaces refused: {exc}")
            return
        # Attach the switch chords to the live module. Without this, Alt+<n>
        # is inert and a hidden space has NO keyboard route back -- which is
        # the one way this feature could actually cost work.
        self._attach_space_chords()
        self.spaces_state.set(
            f"ON — {len(monitors)} displays, Alt+1/Alt+2 switch spaces")
        self.spaces_btn.config(text="Turn off separate Spaces")
        _emit("ok", "separate Spaces on — Alt+1 and Alt+2 switch the space on "
                    "the display under the pointer. Turning it off restores "
                    "every window.")

    def _confine(self, spaces_mod, window, monitors):
        """Pull a straddling window fully onto the display that owns it.

        Doug's observation, and it is the one that makes the rest coherent:
        macOS forbids a window spanning two screens while Displays Have
        Separate Spaces is on. A window lying across a boundary has no
        unambiguous owner, so every ownership question this feature asks
        gets two defensible answers -- which is the source of the
        'Alt+1 moved something that isn't on this screen' class of bug.
        """
        owner = next((m for m in monitors
                      if m.id == window.monitor), None)
        if owner is None or not spaces_mod.straddles(window.bounds,
                                                     owner.bounds):
            return
        target = spaces_mod.confine_to_work_area(window.bounds, owner.bounds)
        if target == window.bounds:
            return
        try:
            from window_tracker import WindowService
            WindowService().place(window.handle, target)
        except Exception:  # noqa: BLE001
            pass

    def _attach_space_chords(self):
        """Point the hotkey host's space verbs at the live Spaces module."""
        host = self._window_host()
        if host is None or self._spaces is None:
            return

        def _dragged_window():
            """The window under a held mouse button, or None.

            macOS carries the window you are dragging when you change space.
            There is no drag EVENT to hook here -- a title-bar drag runs
            inside Windows' own modal move loop -- but the physical button
            state is enough: if the left button is down when the chord
            fires, the foreground window is the one in hand.
            """
            import ctypes
            u32 = ctypes.windll.user32
            if not (u32.GetAsyncKeyState(0x01) & 0x8000):  # VK_LBUTTON
                return None
            u32.GetForegroundWindow.restype = ctypes.c_void_p
            return u32.GetForegroundWindow() or None

        def switch(ordinal):
            import spaces as _spaces
            module = self._spaces
            if module is None or not module.enabled:
                return
            monitors = _spaces._live_monitors()
            live = _spaces._live_windows(monitors)
            # Re-sync BEFORE switching, in two parts:
            #   appeared -- enable() snapshots once, so anything opened later
            #     was never in the model and did not participate at all.
            #   moved    -- a window dragged from one screen to another still
            #     belonged to the FIRST screen's space, so that screen's
            #     Alt+n kept reclaiming a window that had left it. rehome()
            #     only fires on a real monitor change, so this cannot disturb
            #     a window's space on the screen it is already on.
            handle = _dragged_window()
            for window in live:
                try:
                    if not module.window_appeared(window):
                        module.window_moved(window)
                    # Confine, unless it is the window in hand -- snapping a
                    # window the user is actively dragging would fight them.
                    if window.handle != handle:
                        self._confine(_spaces, window, monitors)
                except Exception:  # noqa: BLE001
                    pass
            target = _spaces._pointer_monitor(monitors)
            if target is None:
                target = monitors[0] if monitors else None
            if target is None:
                return
            # Carry the dragged window, so hold-a-title-bar + Alt+n takes it
            # with you instead of switching out from under it.
            if handle is not None:
                carried = next((w for w in live if w.handle == handle), None)
                if carried is not None:
                    space = next(
                        (s for s in module.model.workspaces_on(target.id)
                         if s.ordinal == ordinal), None)
                    if space is not None:
                        try:
                            module.model.assign(carried.key, space.id)
                        except Exception:  # noqa: BLE001
                            pass
            module.switch_to_ordinal(target.id, ordinal)

        host.switch_space_hook = switch

    def _screen_zoom(self):
        """The zoom module, built on first use. None when unavailable."""
        if self._zoom is None:
            try:
                import screen_zoom
                self._zoom = screen_zoom.ScreenZoomModule()
            except Exception as exc:  # noqa: BLE001
                _emit("err", f"screen zoom unavailable: {exc}")
                return None
        return self._zoom

    def _toggle_screen_zoom(self):
        """Start or stop the MOUSE hook. The ONE place either happens."""
        zoom = self._screen_zoom()
        if zoom is None:
            self.zoom_state.set("unavailable on this machine")
            self.zoom_btn.state(["disabled"])
            return
        if getattr(zoom, "is_running", False):
            zoom.stop()   # restores 1.0x before releasing, by contract
            self.zoom_state.set("off — hold Alt and scroll does nothing yet")
            self.zoom_btn.config(text="Turn on Alt+scroll zoom")
            _emit("ok", "screen zoom off — the wheel is untouched.")
            return
        if not zoom.start():
            self.zoom_state.set(
                f"refused — {zoom.last_error or 'magnification unavailable'}")
            _emit("err", "screen zoom refused: the Magnification API would "
                         "not initialize; nothing was installed.")
            return
        self.zoom_state.set("ON — hold Alt and scroll to zoom")
        self.zoom_btn.config(text="Turn off Alt+scroll zoom")
        _emit("ok", "screen zoom on — hold Alt and scroll the wheel.")

    def _module_settings_path(self):
        """Where a module's settings persist.

        On ROOT, which is the exe's own folder -- never __file__, which in a
        frozen build points into a temp bundle Windows deletes on exit. A
        module that records what it observed must find it again next launch.
        """
        return os.path.join(ROOT, "module_settings.json")

    def _load_module_settings(self):
        try:
            with open(self._module_settings_path(), encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _save_module_settings(self, settings):
        try:
            path = self._module_settings_path()
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(settings, fh, indent=2, sort_keys=True)
            os.replace(tmp, path)
        except Exception:  # noqa: BLE001
            pass

    def _module_worker(self):
        """Run the optional modules and draw what they publish.

        Every module call below is already behind plugin_system's fault
        barrier, so a module that raises becomes a faulted row rather than an
        exception on this thread. The broad except here is for the mods's own
        mistakes -- discovery, disk, Tk teardown mid-refresh -- not the
        modules'.
        """
        settings = self._load_module_settings()
        try:
            import module_host
            import plugin_system
            faulted = plugin_system.PluginState.FAULTED
            mods = module_host.ModuleHost(
                settings=settings,
                enabled=settings.get("_enabled", {}),
                on_log=lambda line: _emit("event", line))
            mods.discover()
            mods.start()
            self._module_host = mods
        except Exception as exc:  # noqa: BLE001
            self.ui(lambda: self._draw_modules(
                [("Modules", f"could not start: {type(exc).__name__}")]))
            return

        while not self._closing:
            rows = []
            try:
                for record, reported in mods.reports():
                    rows.append((record.display_name.upper(), ""))
                    for label, value in reported or []:
                        rows.append((label, value))
                    if reported is None:
                        rows.append(("", "reported nothing this cycle"))
                for record in mods.records:
                    # A module that failed to load is SHOWN, with its reason.
                    # Silently omitting it is how a module you rely on goes
                    # missing without anyone noticing.
                    if record.state is faulted:
                        rows.append((record.display_name.upper(),
                                     record.reason or "faulted"))
                if not rows:
                    rows = [("Modules", "none installed")]
            except Exception as exc:  # noqa: BLE001
                rows = [("Modules", f"refresh failed: {type(exc).__name__}")]
            if self._closing:
                return
            self.ui(lambda r=rows: self._draw_modules(r))
            self._save_module_settings(settings)
            time.sleep(600)

    def _draw_modules(self, rows):
        """Paint module rows. The ONLY place module output reaches a widget."""
        box = getattr(self, "modules_box", None)
        if box is None:
            return
        try:
            for child in box.winfo_children():
                child.destroy()
            for label, value in rows:
                text = f"{label:<10}{value}" if label else f"{'':<10}{value}"
                tk.Label(box, text=text, bg=BG, fg=MUTED,
                         font=("Consolas", 8), anchor="w",
                         justify="left").pack(fill="x")
        except tk.TclError:
            pass          # the window went away mid-refresh; nothing to draw on

    # ---- the rail: one pane at a time ------------------------------------
    def select_pane(self, key, rederive=True, remember=True):
        """Show exactly one pane, and re-derive the window's height for it.

        pack_forget, never destroy. Every pane -- and in particular BtPanel and
        MultiArrangeCanvas, which the 3-second poll calls into whether or not
        they are visible -- stays alive and configurable while hidden. See
        PANE_SPEC.
        """
        if key not in self._panes:
            key = DEFAULT_PANE
        changed = (key != self._pane)
        for other, frame in self._panes.items():
            if other != key:
                frame.pack_forget()
        # `before` the spacer, always: the spacer is packed once and never
        # unpacked, so without this a pane selected later in the session would
        # pack UNDER it and be pushed off the bottom of the cavity.
        #
        # W1 restated for a rail, not broken: the invariant is that `bridge` has
        # EXACTLY ONE expanding child, so the window's surplus height has a
        # single named destination. Which child that is depends on the pane. The
        # console is a log and vertical room is the whole point of it, so while
        # it is showing it IS the designated expanding child and the spacer
        # stands down; for the other four the spacer is the sponge as before.
        # The count is one either way, and test_layout_budget.py counts it.
        grows = key in PANE_EXPANDS
        self._panes[key].pack(fill="both", expand=grows,
                              before=self._bridge_spacer)
        self._bridge_spacer.pack_configure(expand=not grows)
        # Where "back" means, written on EVERY change rather than by the Console
        # button alone. It used to be set only in _toggle_console, so reaching
        # the console from the RAIL left it stale and the title-bar button then
        # returned you to whichever pane you last pressed the BUTTON from --
        # possibly an hour earlier -- or to the default. It is "the last pane
        # that was not the console", which is the only thing "back" can mean
        # from a button that lives in the title bar.
        if changed and self._pane not in (None, "console"):
            self._prev_pane = self._pane
        self._pane = key
        for other, (bar, btn) in self._rail.items():
            live = (other == key)
            bar.config(bg=ACCENT if live else RAIL_REST)
            btn.config(bg=RAIL_LIVE if live else RAIL_REST,
                       fg=FG if live else MUTED)
        # ...and the title bar's Console button says which way it will go.
        #
        # It was frozen at "▸  Console" on every pane, so with two controls
        # driving one pane only the rail said which was active. It is a genuine
        # TOGGLE -- a second press returns you -- so it reflects state rather
        # than being demoted to a plain navigation button. The caret alone
        # carries it: ◂ and ▸ are the same width, so the label cannot reflow the
        # title bar as panes change. Written HERE, from the one place that knows
        # the current pane, so the button and the rail cannot disagree.
        self._cons_btn.config(
            text="◂  Console" if key == "console" else "▸  Console")
        if remember and changed:
            # One tiny JSON write, on a deliberate gesture, never on a tick.
            save_setting("last_pane", key)
        if rederive:
            self._rederive_height()

    def _rederive_height(self):
        """Re-run the W1 height budget against the pane that is showing NOW.

        The same three steps as the block at the end of __init__ -- settle the
        packer, re-fit the canvas, measure `full` -- against the same two
        functions. It has to run on EVERY switch, and minsize has to move in
        both directions: left at the tallest pane's height, the window could
        never be shrunk to a short one, which is the exact failure
        window_height_plan exists to prevent, one level up.

        Wrapped, because this is reached from a rail click: a fault here must
        not leave the pane switched but the window the wrong size with a
        traceback on stderr nobody reads. NOT silent, though -- see the handler.
        """
        try:
            self.root.update_idletasks()
            # The canvas measures ITSELF from its allocated width, and a
            # pack_forget'd canvas has no useful one. Re-fit only once the desk
            # pane is actually packed and the packer has run, or it fits to a
            # width the canvas does not have.
            if self._pane == "desk":
                self.canvas._fit_height()
                self.root.update_idletasks()
            content_h = self._full.winfo_reqheight()
            avail_h = work_area_height(self.root.winfo_screenheight())
            geom_h, min_h, _over, clipped = pane_window_plan(content_h, avail_h)
            self._content_h = content_h
            self._content_clipped = clipped
            # minsize FIRST, always. Tk clamps geometry() to the current
            # minsize, so lowering the window without lowering the floor first
            # is a no-op and the window stays stuck at the tall pane's height.
            self.root.minsize(940, min_h)
            if self.root.state() != "zoomed":
                # Height only. Width is the user's -- a pane switch has never
                # been a reason to undo a window he dragged wider. The 940 floor
                # is the minsize width: geometry() is read back before the
                # window is ever mapped as "1x1+0+0", and putting a 1px-wide
                # window on screen for one frame is not a thing to ship.
                match = re.match(r"(\d+)x(\d+)", self.root.geometry())
                width = max(940, int(match.group(1)) if match else 1120)
                self.root.geometry(f"{width}x{geom_h}")
            if clipped and self._pane not in self._clip_warned:
                # Once per pane per session. The warning is the point; repeating
                # it on every visit to the same pane is not.
                self._clip_warned.add(self._pane)
                _emit("err", f"This screen is {avail_h}px tall and the "
                             f"'{self._pane}' pane needs {content_h}px. The "
                             f"bottom {content_h - avail_h}px cannot be shown.")
        except Exception:  # noqa: BLE001
            # NOT a bare pass. This is the exact shape _apply_poll had until it
            # was deleted, on the stated grounds that the wrap made the fault
            # invisible while everything below it kept running -- and the
            # argument is the same here. If the re-derivation throws, the pane
            # switches and the window silently keeps whatever height the
            # PREVIOUS pane needed: too tall, or short enough that Tk drops the
            # bottom panels with no scrollbar to say so. Nothing in the console,
            # nothing on stderr.
            #
            # The swallow stays -- this is a Tk command handler on a rail click,
            # and raising out of one abandons the switch half-done -- but it
            # reports what it swallows, the same way _drain_ui does: full
            # traceback to stderr, one line to the console, capped so a fault
            # cannot loop through _emit, and re-armed below on a clean run.
            self._rederive_faults = getattr(self, "_rederive_faults", 0) + 1
            if getattr(self, "_closing", False) \
                    or self._rederive_faults > self.UI_FAULT_REPORTS:
                return
            detail = traceback.format_exc()
            try:
                sys.stderr.write(detail)
                _emit("err", f"the '{self._pane}' pane is showing but the "
                             "window height could not be re-derived for it — "
                             "the window is the wrong size and may be hiding "
                             "the bottom of the pane:\n"
                             + detail.strip().splitlines()[-1])
            except Exception:  # noqa: BLE001
                pass
        else:
            # A clean re-derivation RE-ARMS the reporting, for the same reason
            # _drain_ui re-arms on a clean drain: without this the cap is a
            # LIFETIME one, and three faults in a session -- including three
            # benign ones during shutdown -- would permanently re-silence the
            # very thing this handler exists to stop being silent.
            self._rederive_faults = 0

    # ---- Audio & status panel (the Bluetooth pane) -----------------------
    def _build_audio_panel(self, parent):
        """Headphones, volume and balance: the head of the Bluetooth pane.

        THE READINESS BANNER IS NOT HERE ANY MORE, and that is the whole point
        of the move. It was here because this was the RIGHT COLUMN of a
        two-column window and the right column did not bind the window's height,
        so a banner here was free. Under a rail there is no right column: this
        panel is the top of ONE pane out of five, and a readiness banner inside
        it would be invisible from the other four.

        That is the W4 fatal verbatim -- the banner then lived in the console
        frame, which was constructed and never mapped, so the default window
        could not say whether the bridge was up. It now lives in the pinned
        header in App.__init__, which is the only place in a one-pane window
        that is always on screen. Exactly one banner, exactly one writer
        (_apply_poll).

        What stays here is what belongs to the pane the rail calls "Bluetooth":
        which headphones are connected (`c_buds` -- nothing else in the app
        carries that fact) and the two sliders that act on them.

        The row of five status dots (`c_stat`: VM / iPad / Mac / Audio /
        Portal) that also stood here stays deleted, and that one WAS a
        duplicate: it restated the indicator row at the top of the window token
        for token, and its "Mac" dot was wired to the dead two-device model, so
        it was permanently grey whatever the Managed Mac was doing.
        """
        p = ttk.LabelFrame(parent, text="Audio & status", padding=8)
        p.pack(fill="x", padx=12, pady=(0, 6))

        self.c_buds = tk.Label(p, text="🎧  —", bg=BG, fg=MUTED,
                               font=(FONT_UI, 10), anchor="w")
        self.c_buds.pack(fill="x", pady=(0, 0))

        self._vol_drag = False
        self._vol_syncing = False
        vr = tk.Frame(p, bg=BG)
        vr.pack(fill="x", pady=(8, 0))
        tk.Label(vr, text="Volume", bg=BG, fg=MUTED, width=9, anchor="w",
                 font=(FONT_UI, 9, "bold")).pack(side="left")
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
                 font=(FONT_UI, 9, "bold")).pack(side="left")
        self.c_bal_var = tk.DoubleVar(value=self._load_balance() * 100)
        self.c_bal = ttk.Scale(br, from_=-100, to=100, variable=self.c_bal_var,
                               command=self._bal_changed)
        self.c_bal.pack(side="left", fill="x", expand=True)
        self.c_bal.bind("<Double-1>", self._bal_center)
        tk.Label(p, text="double-click balance to center", bg=BG,
                 fg="#6E687A", font=(FONT_UI, 8), anchor="w").pack(
            fill="x", pady=(2, 0))

    def _toggle_console(self):
        """The title bar's Console button: jump to the console pane, or back.

        WIDTH NO LONGER MOVES. The console used to be a fixed 390px strip
        pinned to the right edge of the window, and opening it widened the whole
        window from 1120 to 1520 -- with a <Configure> handler to re-apply that
        width whenever the window came out of a maximized state, because Tk
        ignores geometry() while zoomed. All of that machinery
        (_set_win_width, App._on_configure, _was_zoomed, _console_open,
        _cons_anchor, and the 1120/1520 literals) is gone: the console is one of
        the five panes now, and a pane switch changes height, not width.

        A second press comes back to where you were rather than stranding you in
        the console, and the caret says which press you are about to make -- the
        button's label is written by select_pane, from the pane that is actually
        showing, so the rail and the button cannot disagree about it.

        WHERE "BACK" MEANS IS NOT THIS METHOD'S ANY MORE. _prev_pane was written
        here and nowhere else, so selecting Console from the RAIL left it stale
        and this button then returned you to whichever pane you had last pressed
        the BUTTON from -- possibly an hour earlier -- or to the default.
        select_pane maintains it on every change now, so "back" is genuinely the
        last pane that was not the console however you got here.
        """
        if self._pane == "console":
            self.select_pane(self._prev_pane or DEFAULT_PANE)
        else:
            self.select_pane("console")

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
        # padding=(10, 3), not 8. Symmetric 8 spends the same generous pad
        # vertically as horizontally, and this column stacks button ROWS:
        # measured at 96 DPI with Segoe UI 10, "Restart keyboard" is 39px tall
        # at padding=8 and 29px at padding=(10, 3) -- 10px back per stacked row,
        # with the horizontal pad slightly widened so the labels do not tighten.
        st.configure("TButton", background=CARD, foreground=FG,
                     bordercolor=CARD, focuscolor=CARD, relief="flat",
                     padding=(10, 3), font=(FONT_UI, 10))
        # NOTE: the TButton map here is superseded a few lines down, where the
        # disabled colours are added. Both carry "pressed" so neither can be the
        # one that silently drops it.
        st.map("TButton",
               background=[("pressed", PRESS), ("active", "#221F2A")])
        st.configure("Accent.TButton", background=ACCENT_DIM,
                     foreground="#F1EBFF", font=(FONT_UI_SEMI, 10))
        st.map("Accent.TButton",
               background=[("pressed", PRESS_ACCENT), ("active", "#764BE2")])
        st.configure("Danger.TButton", background="#4A1F38",
                     foreground="#FFD9EC", font=(FONT_UI_SEMI, 10))
        st.map("Danger.TButton",
               background=[("pressed", PRESS_DANGER), ("active", "#66294C")])
        # THE ALARM, and the only place full-strength amber is allowed while the
        # portal is down. A stopped portal is the CAUSE of every idle device in
        # this window, so the alarm sits on the control that fixes it and the
        # device area drops to ACCENT_SUPPRESSED / WARN_SUPPRESSED. Foreground is
        # near-black because #dfe4ee on #f5c451 is unreadable.
        # Same state order as every other button map here: disabled first (a
        # disabled button must never look pressed), then pressed, then hover --
        # ttk takes the FIRST match and a held button is pressed AND active.
        st.configure("Warn.TButton", background=WARN, foreground="#2a2205",
                     font=(FONT_UI_SEMI, 10))
        st.map("Warn.TButton",
               foreground=[("disabled", "#6E687A")],
               background=[("disabled", PANEL), ("pressed", PRESS_WARN),
                           ("active", "#f8d276")])
        # sliders (compact mode's volume/balance)
        st.configure("Horizontal.TScale", background=BG, troughcolor=CARD,
                     bordercolor=CARD, lightcolor=ACCENT_DIM,
                     darkcolor=ACCENT_DIM)
        # THE EFFECTIVE MAP: this replaces the TButton map set above, so a
        # "pressed" entry that exists only up there would be dead. Order is
        # load-bearing -- disabled first (a disabled button must never look
        # pressed), then pressed, then hover.
        st.map("TButton",
               foreground=[("disabled", "#6E687A")],
               background=[("disabled", PANEL), ("pressed", PRESS),
                           ("active", "#221F2A")])
        # LabelFrame (the panel that was glaringly light)
        st.configure("TLabelframe", background=BG, bordercolor="#221F2A",
                     relief="solid", borderwidth=1)
        st.configure("TLabelframe.Label", background=BG, foreground=MUTED,
                     font=(FONT_UI, 9, "bold"))
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
                     foreground=FG, arrowcolor=FG, bordercolor="#221F2A",
                     selectbackground=CARD, selectforeground=FG)
        st.map("TCombobox", fieldbackground=[("readonly", CARD)],
               foreground=[("readonly", FG)])
        self.root.option_add("*TCombobox*Listbox.background", CARD)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_DIM)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#F1EBFF")
        # Treeview (the Bluetooth device list)
        st.configure("Treeview", background=CARD, foreground=FG,
                     fieldbackground=CARD, bordercolor=CARD, borderwidth=0,
                     rowheight=26, font=(FONT_UI, 10))
        st.configure("Treeview.Heading", background=PANEL, foreground=MUTED,
                     relief="flat", font=(FONT_UI, 9, "bold"))
        st.map("Treeview.Heading", background=[("active", "#221F2A")])
        st.map("Treeview", background=[("selected", ACCENT_DIM)],
               foreground=[("selected", "#F1EBFF")])

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

    def _on_ui(self, fn):
        """Run fn on the Tk thread -- NOW if we are already on it, queued if
        not. Never lets a worker touch Tk directly: a background after()
        racing the UI thread hard-crashes the interpreter, and a reentrant Tk
        call from a foreign context is the ucrtbase 0xC0000409 fail-fast this
        codebase has hit before."""
        if threading.get_ident() == getattr(self, "_ui_thread", None):
            fn()
        else:
            self.ui(fn)

    def busy(self, button, label):
        """Say, ON THE BUTTON, that its work is in flight. Returns the restore.

        Doug: *"when i click a button i need visual indication it has been
        clicked on the button itself. it needs to react in some way, even in a
        pending state while the action runs."*

        Usage is deliberately one line at each end, because the actions this
        wraps all have a failure path and a success path and only a `finally`
        catches both:

            done = self.busy(self.some_btn, "Restarting…")
            def work():
                try:
                    ...
                finally:
                    done()

        Safe from any thread at BOTH ends -- the restore is normally called
        from the worker.

        NOT used for the four per-device verbs. Those are re-derived from
        _dev_state by _apply_device_rows every three seconds, so a busy state
        parked on them here would be stomped by the next tick; there the busy
        presentation is part of the state model instead. Both paths paint
        through paint_button_busy, so they look the same.
        """
        if button is None:
            return lambda: None
        self._on_ui(lambda: set_button_busy(button, label))
        return lambda: self._on_ui(lambda: clear_button_busy(button))

    UI_FAULT_REPORTS = 3

    def _drain_ui(self):
        """UI-thread pump for ui(): run queued closures, reschedule.

        THE SILENT-FREEZE POINT. `_poll` marshals the whole 200-line
        `_apply_poll` through here as one closure. A KeyError or
        AttributeError anywhere inside it therefore did not crash and did not
        log: the closure aborted at the fault, every surface painted BELOW that
        line simply stopped updating, and the app went on ticking every three
        seconds looking half alive. There was nothing in the console, nothing
        on stderr and no traceback anywhere. That is how W3's two verb tables
        earned their import-time coverage check, and it is why the sys_status
        block above no longer carries a blanket try of its own.

        So the swallow stays -- a widget really can be destroyed under a queued
        closure during shutdown, and raising out of an `after` callback would
        take the pump down with it -- but it is no longer silent. The first few
        faults print a full traceback to stderr and one line to the console.
        The cap is what makes that safe: _emit routes back through ui(), so an
        unbounded report of a fault in the logger itself would be an infinite
        loop inside this very while.
        """
        try:
            faulted = False
            while True:
                try:
                    fn = self._uiq.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    faulted = True
                    self._ui_faults = getattr(self, "_ui_faults", 0) + 1
                    if self._closing or self._ui_faults > self.UI_FAULT_REPORTS:
                        continue   # shutdown noise, or already said 3 times
                    detail = traceback.format_exc()
                    try:
                        sys.stderr.write(detail)
                        _emit("err",
                              "a UI update failed — the status surfaces below "
                              "it have stopped refreshing:\n"
                              + detail.strip().splitlines()[-1])
                    except Exception:  # noqa: BLE001
                        pass
            if not faulted:
                # A clean drain RE-ARMS the reporting. Without this the counter
                # is a lifetime cap: three faults anywhere in a session --
                # including one benign widget-destroyed-at-shutdown -- would
                # permanently re-silence the pump, which is the exact silent
                # mode this reporting exists to close. The cap still does its
                # real job, which is bounding a burst WITHIN one drain: _emit
                # routes back through ui(), so an unbounded report of a fault in
                # the logger would loop inside this very while.
                self._ui_faults = 0
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
    def restart_everything(self, log=None, button=None):
        """Restart ONLY the audio pipeline: the PipeWire/WirePlumber services in
        the VM plus the Windows sender. Deliberately does NOT touch the VM or the
        keyboard daemon (openspanble) -- audio and the iPad keyboard are
        independent, so restarting audio must never drop the keyboard.

        `button` is whichever control the user actually pressed: there are two
        (System control's "Restart audio" and the Bluetooth panel's "⟳ Restart
        audio"), and the one that waits should be the one that was clicked."""
        done = self.busy(button, "Restarting audio…")

        def say(m):
            try:
                if log:
                    log(m)
            except Exception:  # noqa: BLE001
                pass
            self.ui(lambda: self.status.set(m))

        def work():
            try:
                say("restarting the audio pipeline (keyboard untouched)…")
                # audio-only: these never touch bluetoothd/the radio/openspanble
                ssh_guest("systemctl restart openspan-wireplumber "
                          "openspan-pipewire-pulse openspan-udprecv",
                          timeout=45)
                try:
                    if self.audio_proc and self.audio_proc.poll() is None:
                        _terminate_role_process(self.audio_proc)
                except Exception:  # noqa: BLE001
                    pass
                self.audio_proc = None
                self._ensure_audio()
                say("audio restarted — wake your headphones to reconnect. "
                    "Keyboard was not touched.")
            finally:
                done()
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
        done = self.busy(self._sysbtn["Stop VM"], "Stopping VM…")

        def work():
            try:
                ssh_guest("journalctl --sync; sync", timeout=12, quiet=True)
                gentle_release()
                vbox("controlvm", VM, "poweroff")
            finally:
                done()
        threading.Thread(target=work, daemon=True).start()

    def cold_restart_vm(self):
        if not dark_confirm(
                self.root, "Cold-restart VM?",
                "Reboot the bridge VM cleanly (~2min): radios handed back "
                "one at a time, guest shut down properly, then a fresh boot "
                "and re-delivery. The radios themselves never power-cycle — "
                "that takes a replug or a host reboot.\n\nRestart now?"):
            return
        self.status.set("Cold-restarting VM…")
        done = self.busy(self._sysbtn["Cold-restart VM"], "Restarting VM…")

        def work():
            try:
                if vm_running():
                    ssh_guest("journalctl --sync; sync", timeout=12, quiet=True)
                    # Ordered radio handback first, then a REAL guest
                    # shutdown. The old flow pulled the virtual plug with
                    # radios attached: a mass USB release (the 2026-08-08
                    # injury event) plus a hard filesystem stop, in exchange
                    # for a "cold boot" the radios never actually got.
                    gentle_release()
                    vbox("controlvm", VM, "acpipowerbutton")
                    for _ in range(30):
                        if not vm_running():
                            break
                        threading.Event().wait(1)
                    if vm_running():
                        _emit("event", "guest ignored ACPI — hard poweroff")
                        vbox("controlvm", VM, "poweroff")
                        for _ in range(15):
                            if not vm_running():
                                break
                            threading.Event().wait(1)
                start_vm_clean()
            finally:
                done()
        threading.Thread(target=work, daemon=True).start()

    def restart_keyboard(self):
        self.status.set("Restarting keyboard daemon…")
        done = self.busy(self._sysbtn["Restart keyboard"], "Restarting…")

        def work():
            try:
                ssh_guest("systemctl restart openspanble", timeout=25)
                self.ui(lambda: self.status.set(
                    "Keyboard restarted — forget + re-pair on the iPad."))
            finally:
                done()
        threading.Thread(target=work, daemon=True).start()

    def restart_audio_btn(self):
        self.restart_everything(button=self._sysbtn["Restart audio"])

    def shutdown_all(self):
        if not dark_confirm(
                self.root, "Shut down everything?",
                "Power off the VM and close the app. Audio, keyboard, portal, "
                "and sender all stop — nothing keeps running.\n\nShut down "
                "now?"):
            return
        self._full_stop()

    # ---- close / tray ----
    def _boot_why_probe(self, force=False):
        """Refresh the one-sentence reason the bridge is not up. Throttled."""
        now = time.monotonic()
        if not force and now - getattr(self, "_boot_why_at", 0.0) < 12.0:
            return
        self._boot_why_at = now
        if getattr(self, "_boot_why_busy", False):
            return
        self._boot_why_busy = True

        def work():
            try:
                ready, why = why_not_ready(self.canvas.config)
                self.ui(lambda: setattr(self, "_boot_why",
                                        "" if ready else why))
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._boot_why_busy = False
        threading.Thread(target=work, daemon=True).start()

    def _full_stop(self):
        """The FULL STOP: portal, audio sender, and the VM all go down, then
        the app closes — nothing lingers, next launch is a clean cold boot."""
        self._closing = True  # stop the ui() pump rescheduling past destroy
        # The keyboard hook comes out FIRST. A low-level hook outliving its
        # process is how a machine ends up with dead chords until a reboot.
        _host = getattr(self, "_wm_host", None)
        if _host is not None and _host.is_running:
            try:
                _host.stop()
            except Exception:  # noqa: BLE001
                pass
        # Zoom too, and for a sharper reason: stop() puts magnification back
        # to 1.0x. Exiting while zoomed would leave the screen magnified with
        # nothing left running to undo it.
        _zoom = getattr(self, "_zoom", None)
        if _zoom is not None and getattr(_zoom, "is_running", False):
            try:
                _zoom.stop()
            except Exception:  # noqa: BLE001
                pass
        # And Spaces, for the sharpest reason of the three: exiting while it
        # holds windows hidden would leave them invisible with nothing left
        # running to show them again.
        _spc = getattr(self, "_spaces", None)
        if _spc is not None and getattr(_spc, "enabled", False):
            try:
                _spc.disable()
            except Exception:  # noqa: BLE001
                pass
        # Modules get their deactivate() called, and their settings written.
        # A module records what it OBSERVED -- the agent monitor remembers
        # resets it actually watched happen -- and losing that on exit would
        # leave only the provider's claims, which is the thing it exists to
        # cross-check.
        _mods = getattr(self, "_module_host", None)
        if _mods is not None:
            try:
                _mods.stop()
                self._save_module_settings(_mods._settings)
            except Exception:  # noqa: BLE001
                pass
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
        # The portal was just hard-killed and may have been holding the
        # coexistence lease. Nothing depends on this -- abandonment heals it --
        # but the whole point of a full stop is that nothing lingers.
        _clear_input_capture_lease("full stop")
        try:
            if vm_running():
                gentle_release()
                vbox("controlvm", VM, "poweroff")
        except Exception:  # noqa: BLE001
            pass
        stop_virtualbox_backend()
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
                        activebackground=ACCENT_DIM, activeforeground="#F1EBFF",
                        font=(FONT_UI, 10))
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

    def _portal_changed(self, ok):
        if not ok:
            self.status.set(
                "⚠ No managed device touches a PC monitor — no portal")
        if self._portal_live():
            # The portal reads geometry and input settings once at process
            # start. Apply a drag, resize, rotation, resolution, sensitivity or
            # acceleration edit immediately.
            _terminate_role_process(self.portal_proc)
            self.portal_proc = None
            # THIS is the path EsotericOS's spec names by hand: a geometry
            # change is two clicks away in normal use and it taskkills the
            # portal outright, so a capture in progress ends with the lease
            # held by a dead process. Collect it before the replacement starts
            # -- the new portal creates the same named mutex and would
            # otherwise inherit a stale abandonment on its first crossing.
            _clear_input_capture_lease("portal restarted for a geometry change")
            self._start_portal_process()
            self.log("event", "portal geometry reloaded from the arrangement.")

    def toggle_vm(self):
        # Both branches go through busy() and both run on a worker. The stop
        # branch used to call vbox() straight from the click, on the UI thread,
        # after writing "Stopping VM…" -- so Tk could not repaint until the
        # VBoxManage call it was reporting had already finished. A pending label
        # the UI thread is too blocked to draw is not feedback.
        if vm_running():
            if dark_confirm(self.root, "Stop VM?",
                            "Stop the bridge VM? The iPad will disconnect "
                            "until you start it again."):
                done = self.busy(self.vm_btn, "Stopping VM…")

                def stop():
                    try:
                        gentle_release()
                        vbox("controlvm", VM, "acpipowerbutton")
                    finally:
                        done()
                threading.Thread(target=stop, daemon=True).start()
        else:
            done = self.busy(self.vm_btn, "Starting VM…")

            def start():
                try:
                    start_vm_clean()
                finally:
                    done()
            threading.Thread(target=start, daemon=True).start()

    def _portal_button(self, master, **kw):
        """Build a portal control AND register it with the one writer.

        Every portal button in this window is made here, so there is no way to
        add a surface the renderer does not drive and no way to give one of them
        a second route to the backend: the command is bound once, in this
        method, to the same toggle_portal every other copy calls.

        There are two of them today -- the ctl grid's, in the System pane, and
        the floating one over the Desk arrangement. The list is what
        _render_portal_button and _busy_portal iterate; with a single element
        their behaviour is identical to what shipped before the second button
        existed.
        """
        button = ttk.Button(master, text="Start portal",
                            command=self.toggle_portal, **kw)
        self._portal_btns.append(button)
        return button

    def _busy_portal(self, label):
        """Park one wait across EVERY portal button. Returns the single restore.

        Stopping the portal is a real wait -- _terminate_role_process runs
        taskkill /T /F and then waits on the handle, two 4-second timeouts, so
        up to ~8s -- and the 3-second tick calls _render_portal_button the whole
        way through. With more than one button on screen the wait has to land on
        ALL of them: one reading "Stopping portal…" while the other still offers
        "Stop portal" is precisely the two-surfaces-one-state failure this app
        has already shipped once.
        """
        dones = [self.busy(button, label) for button in self._portal_btns]

        def restore():
            for done in dones:
                done()
        return restore

    def _render_portal_button(self, on):
        """Text AND register for EVERY portal button. The ONE writer.

        Doug: *"when the portal is not going, all that orange color let's just
        put it around the Start Portal button and make that thing prominent
        that that is why nothing is happening."*

        So while the portal is down these buttons carry the full-strength amber
        that the device area has given up (see ACCENT_SUPPRESSED /
        WARN_SUPPRESSED), and while it is running they are ordinary buttons
        again. Every caller passes the boolean from _portal_live(); nothing here
        forms a second opinion about whether the portal is up.

        It writes the WHOLE registry, every time, from one text and one style --
        so the copy floating on the Desk pane and the copy in the System pane's
        ctl grid cannot drift apart. They are not a hardcoded pair: whatever
        _portal_button has built is what this drives.

        The busy guard is live, not decoration: toggle_portal's stop branch
        parks "Stopping portal…" on the buttons while _terminate_role_process
        runs taskkill /T /F and then waits on the handle -- two 4-second
        timeouts, so up to ~8s -- and the 3-second poll tick calls this method
        the whole time. The guard is ANY, not the first button's: _busy_portal
        parks the wait on all of them together, and a half-painted pair is the
        exact bug a second surface exists to avoid.

        Writing to a button whose pane is pack_forget'd is harmless -- a hidden
        Tk widget still accepts config -- which is what lets the tick keep both
        copies correct while only one of them is on screen.
        """
        if any(button_is_busy(button) for button in self._portal_btns):
            return   # a wait is showing; the tick must not paint over it
        for button in self._portal_btns:
            button.config(text="Stop portal" if on else "Start portal")
            button.configure(style="TButton" if on else "Warn.TButton")

    def toggle_portal(self):
        if self._portal_live():
            # Stopping is a REAL wait and it used to run on the UI thread
            # straight off the click: taskkill /T /F plus a wait on the handle,
            # two 4-second timeouts, during which Tk could not repaint. So it
            # goes to a worker and says so on the button, like every other
            # threaded action in this file.
            #
            # The handle is taken and cleared HERE, before the thread starts,
            # so _portal_live() reports the portal down from this instant --
            # the poll tick must not spend eight seconds insisting it is up.
            proc, self.portal_proc = self.portal_proc, None
            # EVERY portal button, not just the ctl grid's -- see _busy_portal.
            done = self._busy_portal("Stopping portal…")

            def stop():
                try:
                    _terminate_role_process(proc)
                finally:
                    # Stopping the portal mid-capture leaves the lease held by
                    # a dead process until somebody waits on it. Collect it now
                    # so EsotericOS comes back the moment the bridge is down,
                    # not whenever it next happens to probe.
                    _clear_input_capture_lease("portal stopped")
                    done()
                    self.ui(lambda: self._render_portal_button(
                        self._portal_live()))
            threading.Thread(target=stop, daemon=True).start()
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
        self._render_portal_button(self._portal_live())

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
                (os.path.join("..", "guest", "start-ble-lane.sh"),
                 "/opt/openspan/start-ble-lane.sh"),
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
            # Before bringing lanes up, finish any delivery VirtualBox left
            # hanging at VM start -- otherwise every lane on an undelivered
            # radio fails its bringup and the fix is a human with a dongle.
            try:
                usb_state = read_radio_state()
                if usb_state.get("captured"):
                    _, undelivered = explicit_handoff(
                        state=usb_state, config=self.canvas.config)
                    for label, reason in undelivered:
                        _emit("err", f"{label}: {reason}")
            except Exception as exc:  # noqa: BLE001
                _emit("err", f"explicit handoff pass failed: {exc}")
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
                    # 90s, not 45: a real preflight recovery (3 spaced probes,
                    # lane stop, bluetoothd kill+restart, lane restore) runs
                    # ~50-60s guest-side. A caller that hangs up at 45s reports
                    # failure for work that then completes anyway.
                    timeout=90, quiet=True)
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
        """Mutable per-device pairing state, created on demand.

        "verb" is which of the four connection verbs owns this lane right now
        ("" for none). It lives HERE rather than on the button because
        _apply_device_rows re-derives all four verbs from this dict every three
        seconds -- anything painted on the widget instead is stomped by the next
        tick. It is deliberately NOT folded into inflight/broadcasting: those
        two gate which verbs are OFFERED and that predicate is unchanged, while
        this one only decides which button says it is working.
        """
        return self._dev_states.setdefault(device_id, {
            "inflight": False, "broadcasting": False, "paired": False,
            "gen": 0, "started": 0.0, "verb": "", "lock": threading.Lock(),
        })

    def _device_verb_facts(self, device):
        """Every fact the four connection verbs are decided from, for ONE
        device record. THE ONLY PRODUCER -- device_verb_offer's input.

        This used to be assembled inline in _apply_device_rows, which was fine
        while the card was the only surface offering the verbs. It is not fine
        now that the arrangement canvas offers them too: two producers reading
        the same _dev_state can disagree about `up` or `busy` for a whole poll
        interval and both be defensible, and the visible result is a menu that
        offers Connect while the card three inches away has it greyed out.
        One producer, one gate table (device_verb_offer), two renderers.

        Returns the six keys DEVICE_VERB_GATES reads, plus three the RENDERERS
        need and would otherwise re-derive:

            verb            which verb owns this lane right now, "" for none.
                            Both surfaces let it OVERRIDE the gate -- the card
                            paints that button busy, the menu shows the same
                            present participle disabled.
            radio           the assigned controller address, "" if none
            radio_missing   assigned, but that dongle is not present

        The pair/connect self-heal lives here rather than in the caller, and it
        WRITES: _pair_device_worker clears inflight down half a dozen separate
        failure paths, so the label is cleared from whatever reads the state
        next instead of from every one of them. Callers are on the Tk thread
        (the poll marshals through ui(); the menu is an event handler), which
        is the same thread that owned this write before.
        """
        device_id = device["id"]
        state = self._dev_state(device_id)
        status = self._dev_status.get(device_id)
        # isinstance, not `status and` / `is not None`. A daemon status is
        # whatever json.loads made of the bytes on the socket, and JSON's top
        # level is legally a scalar or an array -- so `5` short-circuits PAST
        # the `status and` guard straight into `5.get(...)`, and `is not None`
        # counts that stray byte as a reachable daemon. The AttributeError
        # would land inside _apply_poll, whose closure _drain_ui swallows:
        # every surface below it silently stops refreshing.
        if not isinstance(status, dict):
            status = None
        busy = bool(state["inflight"] or state["broadcasting"])
        verb = state.get("verb", "")
        if verb in ("pair", "connect") and not busy:
            state["verb"] = verb = ""
        radio = str(device.get("radio", "") or "")
        # Is this device's assigned radio actually PRESENT? A dongle that
        # vanished (unplugged, or claimed-but-not-attached by VirtualBox) left
        # the row showing its last known "paired" forever, because every query
        # errored with "controller not available" and the state simply never
        # updated. A frozen yes is worse than an honest "cannot tell" -- it
        # hides the real fault.
        known = {str(r.get("address", "")).upper()
                 for r in (getattr(self.bt_panel, "_radios", []) or [])}
        radio_missing = bool(radio) and bool(known) and radio not in known
        return {
            "usable": bool(radio) and not radio_missing,
            "vm": bool(self._vm_reachable),
            "up": status is not None,
            "busy": busy,
            "paired": bool(state["paired"]),
            "live": bool(status and status.get("kbd_subscribed")),
            "verb": verb,
            "radio": radio,
            "radio_missing": radio_missing,
        }

    def _portal_live(self):
        """Is the input portal running? The single source of truth, read
        straight off the process handle. Every renderer of portal state calls
        THIS -- a second opinion is how the button and the device area end up
        disagreeing about why nothing is happening."""
        return bool(self.portal_proc and self.portal_proc.poll() is None)

    def _refresh_device_rows_now(self):
        """Repaint the device rows immediately, from the same portal liveness
        _apply_poll reads.

        Called the instant a verb is clicked. This is the SAME writer invoked
        EARLY, not a second one: without it the busy label would not appear
        until the next 3-second tick, which is most of the way through some of
        these actions."""
        self._apply_device_rows(self._portal_live())

    def _pair_device(self, device_id, reset=False, confirm=True, verb="pair"):
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
        # Which button is waiting. Recorded BEFORE the thread starts and painted
        # in the same breath, so the wait is visible on the click rather than at
        # the next poll tick up to three seconds later. Cleared by
        # _apply_device_rows the moment inflight/broadcasting both fall.
        state["verb"] = verb
        self.status.set(f"Working — preparing the Bluetooth radio for {label}…")
        self._refresh_device_rows_now()
        threading.Thread(target=self._pair_device_worker,
                         args=(device_id, reset), daemon=True).start()

    def _pair_device_worker(self, device_id, reset=False):
        """Run one pair/connect attempt, and NEVER leave the lane claiming to
        be in flight if it dies.

        Every failure the attempt itself anticipates clears inflight on its own
        way out. This wrapper is for the ones it does not: an unexpected
        exception used to escape the thread with inflight still True, so
        _apply_device_rows kept re-deriving "Pairing…" from _dev_state and the
        button lied for the full 300 seconds until the advertising-window sweep
        cleared it. Pre-W3 the same crash produced a merely-disabled button;
        the busy label made a silent failure into a confident false statement,
        which is worse.

        The exception is LOGGED, not swallowed. A crash nobody can see is how
        this shipped.
        """
        try:
            self._pair_device_attempt(device_id, reset)
        except Exception:  # noqa: BLE001
            # Captured FIRST: everything below can raise on its own, and the
            # crash worth reporting is this one.
            detail = traceback.format_exc().strip()[-400:]
            state = self._dev_state(device_id)
            with state["lock"]:
                state["broadcasting"] = False
                state["inflight"] = False
            state["verb"] = ""
            try:
                set_target_advertising(device_id, False)
            except Exception:  # noqa: BLE001
                pass          # the radio may be exactly what just failed
            _emit("err", f"{device_id} pair worker CRASHED — {detail}")
            self.ui(lambda: self.status.set(
                f"{device_id} pair failed unexpectedly — see console."))
            self.ui(self._refresh_device_rows_now)

    def _pair_device_attempt(self, device_id, reset=False):
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
            # 90s: preflight recovery alone can spend ~60s before the 25s
            # port wait even starts. See the boot-time bringup timeout note.
            r = ssh_guest(command, timeout=90)
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
        self._pair_device(device_id, reset=False, confirm=False, verb="connect")

    def _disconnect_device(self, device_id):
        record, _controller, _port, _name = self.device_lane(device_id)
        label = record.get("name", device_id)
        state = self._dev_state(device_id)
        # Disconnect and unpair CLEAR inflight rather than setting it -- they
        # are also the cancel for a pair attempt -- so their wait cannot be read
        # off those flags and has to say so itself. The try/finally is the
        # point: an ssh that times out must still hand the button back.
        state["verb"] = "disconnect"
        self._refresh_device_rows_now()

        def work():
            try:
                with state["lock"]:
                    state["broadcasting"] = False
                    state["inflight"] = False
                set_target_advertising(device_id, False)
                reply = target_daemon_cmd(device_id, {"cmd": "disconnect"})
                if reply and reply.get("ok"):
                    _emit("event", f"{label} DISCONNECTED "
                                   f"({reply.get('disconnected', 0)} link) — "
                                   "advertising off, its on-screen keyboard "
                                   "returns.")
                else:
                    _emit("err", f"couldn't disconnect {label}.")
                self._refresh_device_paired(device_id)
            finally:
                state["verb"] = ""
                self.ui(self._refresh_device_rows_now)
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
        # Set AFTER the confirm: a cancelled dialog must not leave the button
        # saying "Unpairing…". The guest's forget-hid is a 25s ssh, which is
        # exactly the wait this exists to make visible.
        state["verb"] = "unpair"
        self._refresh_device_rows_now()

        def work():
            try:
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
            finally:
                state["verb"] = ""
                self.ui(self._refresh_device_rows_now)
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
        N devices; no branch anywhere depends on what kind of device it is.

        THE ONLY WRITER of a device verb's label and enabled state, including
        its BUSY presentation. This method re-derives all four verbs from
        _dev_state on every 3-second poll tick, so a busy label painted over
        the top by a click handler was guaranteed to be stomped within three
        seconds. "In flight" is therefore read out of _dev_state here, exactly
        like "paired" and "connected" -- one writer, one source of truth. The
        click path gets its instant feedback by calling this method EARLY (see
        _refresh_device_rows_now), never by painting around it.
        """
        devices = self.canvas.devices()
        if set(self._dev_rows) != {d["id"] for d in devices}:
            self._rebuild_device_rows()
        for device in devices:
            device_id = device["id"]
            row = self._dev_rows.get(device_id)
            if not row:
                continue
            # ONE producer, shared with the canvas's right-click verb section.
            # Everything below -- the dot, the state text, the radio readout
            # and all four buttons -- is read out of this single dict, so the
            # card cannot describe a different lane than the menu offers verbs
            # for. Its docstring carries the guard against re-deriving any of
            # it here. It also performs the pair/connect self-heal on
            # state["verb"], which used to live in this loop.
            facts = self._device_verb_facts(device)
            live = facts["live"]
            paired = facts["paired"]
            up = facts["up"]
            verb = facts["verb"]
            radio = facts["radio"]
            radio_missing = facts["radio_missing"]
            # Two RADIO faults outrank the state table, because in both the
            # device's own state is unknowable rather than merely idle. The five
            # rows that remain are device_state_colour -- the same pure function
            # the indicator row uses, so the top of the window and the device
            # area cannot disagree about what is wrong.
            #
            # `up` is now READ OUT here as well as gating the verbs. Until this
            # wave it only ever gated: a card whose daemon did not answer
            # printed "not paired", exactly as if the device were genuinely
            # unbonded, and the only surface in the window that could tell the
            # two apart was a global "Mac ● up / ○ down" line about a singleton
            # device -- a line that was itself structurally stuck on "down".
            # device_reach_state is the composition, and it goes THROUGH
            # device_state_colour and suppressed() rather than beside them.
            if radio_missing:
                colour, text = DANGER, "radio not present"
                paired = False
            else:
                colour, text = device_reach_state(portal_on, up, live, paired)
                if not (live or paired) and not radio:
                    colour, text = MUTED, "no radio assigned"   # the more
                    #                    useful of the three grey readings
            row["dot"].config(fg=colour)
            row["name"].config(text=f"{device.get('name', device_id)}  ·  {text}")
            row["radio"].config(
                text=(f"{radio}  :{device.get('port')}" if radio
                      else f":{device.get('port')}"))
            buttons = row["buttons"]
            # device_verb_offer is the ONLY caller of DEVICE_VERB_GATES, which
            # is checked against DEVICE_VERBS at import. This was a
            # hand-written four-key dict right here, indexed by the
            # DEVICE_VERB_SPEC loop below: a fifth verb in the spec was a
            # KeyError raised inside the poll, where _drain_ui swallows it and
            # the whole status surface freezes. The canvas menu calls the same
            # function on the same facts, so the two surfaces cannot disagree.
            enabled = device_verb_offer(facts)
            for key, resting, in_flight in DEVICE_VERB_SPEC:
                button = buttons[key]
                if key == verb:
                    # The busy presentation WINS over the gate: this verb's own
                    # work is running, and offering it again (or, for
                    # disconnect-as-cancel, offering to cancel the cancel) is
                    # not a thing the user can usefully do.
                    paint_button_busy(button, in_flight)
                else:
                    button.config(text=resting)
                    button.state(["!disabled"] if enabled[key] else ["disabled"])
            # portal_on goes THROUGH to the canvas rather than being folded
            # into `live` first. Collapsing it here is what let the biggest
            # element in the window paint full-strength amber for a stopped
            # portal while the card three inches below it said "suppressed".
            self.canvas.set_target_state(device_id, live, paired, portal_on)

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
                     bg=BG, fg=MUTED, font=(FONT_UI, 9)).pack(
                anchor="w", pady=4)
            return
        for device in devices:
            self._build_device_row(device)

    def _build_device_row(self, device):
        """ONE row per device: dot · name · state · radio/port · the four verbs.

        This card used to be TWO stacked rows carrying NINE buttons, and spent
        about 83px per card -- 250 across the three -- to expose at most two
        live actions. Five of those nine (Radio…, Input…, Rename, Displays…,
        Remove) appear nowhere in _apply_device_rows: all fifteen across three
        cards were permanently enabled whether or not the device even had a
        radio assigned. They are not actions on the LANE at all, they are
        per-object property editors -- exactly the shape the Bluetooth tree and
        (since the arrangement menus landed) the canvas already handle with a
        right-click. So they are a right-click here too, and the row that held
        them is gone.

        The four CONNECTION verbs stay visible and gated. They are the opposite
        kind of control: real state decides which of them is live, and in both
        the paired-idle and the live state TWO of them are.

        Returns the row dict as well as storing it, so a test can build one
        without an assembled window.
        """
        device_id = device["id"]
        head = tk.Frame(self._dev_body, bg=BG)
        head.pack(fill="x", pady=(PAD_XS, PAD_XS))
        dot = tk.Label(head, text="●", bg=BG, fg=MUTED, font=(FONT_UI, 11))
        name = tk.Label(head, text=device.get("name", device_id), bg=BG, fg=FG,
                        font=(FONT_UI_SEMI, 10))
        radio = tk.Label(head, text="", bg=BG, fg=MUTED, font=("Consolas", 8))
        # The verbs are packed FIRST, from the right, so the packer allocates
        # their natural widths before the labels take what is left. In a single
        # row that ordering is load-bearing: a squeezed button is unclickable,
        # a squeezed label merely runs out of view. Packing right-to-left means
        # walking the spec in reverse to read Pair · Connect · Disconnect ·
        # Unpair left-to-right on screen.
        # Built off DEVICE_VERB_HANDLERS, which is checked against DEVICE_VERBS
        # at import. This was a hand-written four-key dict indexed by the
        # DEVICE_VERB_SPEC loop below it -- the same silent-KeyError shape as
        # the gate in _apply_device_rows, and the same fix.
        commands = {key: getattr(self, DEVICE_VERB_HANDLERS[key])
                    for key in DEVICE_VERBS}
        buttons = {}
        for key, resting, _in_flight in reversed(DEVICE_VERB_SPEC):
            button = ttk.Button(
                head, text=resting,
                command=lambda d=device_id, fn=commands[key]: fn(d))
            button.pack(side="right", padx=(PAD_SM, 0))
            button.state(["disabled"])
            buttons[key] = button
        # The "⋯" affordance, packed LAST so it lands immediately left of Pair.
        # side="right" allocates first-packed rightmost, and the loop above runs
        # the spec reversed, so Pair is the leftmost verb -- this then sits just
        # inside it, where the eye arrives before reading the verbs.
        #
        # It is never disabled. The five editors behind it (Rename, Radio,
        # Input, Displays, Remove) are per-object properties, not lane actions:
        # unlike the verbs there is no device state in which they are all
        # meaningless, and a greyed-out hint would advertise nothing.
        more = ttk.Button(head, text="⋯", width=3)
        more.configure(command=lambda b=more, d=device_id:
                       self._card_menu_from_button(b, d))
        more.pack(side="right", padx=(PAD_SM, 0))
        dot.pack(side="left")
        name.pack(side="left", padx=(4, 8))
        radio.pack(side="left")
        # A tk.Frame does NOT receive its children's events -- FrameModal's own
        # docstring documents that bubbling trap -- so the card's menu is bound
        # to the frame AND to each label individually. Bind only the frame and
        # right-clicking the device's own name, the thing you are aiming at,
        # does nothing at all.
        for widget in (head, dot, name, radio):
            widget.bind("<Button-3>",
                        lambda e, d=device_id: self._device_card_menu(e, d))
        # Built off DEVICE_ROW_KEYS, not off a literal: this dict and the
        # subscripts in _apply_device_rows are three thousand lines apart and
        # their disagreement is SILENT (see the constant's comment).
        parts = {"dot": dot, "name": name, "radio": radio, "buttons": buttons,
                 "more": more}
        row = {key: parts[key] for key in DEVICE_ROW_KEYS}
        self._dev_rows[device_id] = row
        return row

    # ---- the device card's right-click ------------------------------------
    def _device_card_menu(self, event, device_id):
        """Right-click a device card: everything about the OBJECT.

        Mirrors _canvas_menu exactly, including the grab_release in a finally
        -- a tk_popup that raises with the grab still held leaves the window
        mouse-dead.
        """
        self._post_card_menu(device_id, event.x_root, event.y_root)

    def _post_card_menu(self, device_id, x_root, y_root):
        """Post the card menu at absolute screen coordinates.

        Split out of _device_card_menu so the "⋯" button can reach the SAME
        menu without a synthetic event. One filler, one menu, one grab_release
        -- a second posting path would be a second place for the grab to leak,
        and a tk_popup that raises while holding it leaves the window
        mouse-dead.
        """
        menu = self._card_menu
        self._fill_card_menu(menu, device_id)
        try:
            menu.tk_popup(int(x_root), int(y_root))
        finally:
            menu.grab_release()

    def _card_menu_from_button(self, button, device_id):
        """The ⋯ button's action: post the card menu under the button itself.

        Doug: *"put a '...' floating next to the Pair button for all devices --
        this will make it obvious there are other options to be had"*.

        Five per-object editors moved onto a right-click when the card collapsed
        to one row, and a right-click advertises itself to nobody. The Bluetooth
        panel has the same problem and solves it in prose ("right-click a device
        for actions"); a card has no room for a sentence, so it gets a glyph
        that opens the very same menu. Anyone who finds the button learns the
        right-click exists; anyone who already knows never needs the button.
        """
        self._post_card_menu(device_id,
                             button.winfo_rootx(),
                             button.winfo_rooty() + button.winfo_height())

    def _fill_card_menu(self, menu, device_id):
        """The five editors that used to be five permanently-enabled buttons.

        EVERY entry here opens a modal, and every entry therefore goes through
        _deferred. FrameModal.grab_set records grab_current() as _prev_grab and
        hands the grab back when the modal closes; opened inline from a posted
        menu it captures the MENU, then returns the grab to an unposted widget
        and leaves the whole window mouse-dead. dark_prompt, dark_confirm and
        MacDisplayEditor are all FrameModals.
        """
        record = self.device_record(device_id) or {}
        menu.delete(0, "end")
        menu.add_command(label=record.get("name", device_id), state="disabled")
        # The lane, spelled out. The card shows the same two facts, but the card
        # is now a single row whose labels are the last thing the packer serves:
        # a long device name eats the radio readout. Here it cannot be squeezed.
        _radio = str(record.get("radio", "") or "")
        menu.add_command(
            label="   " + (f"{_radio}   port {record.get('port')}" if _radio
                           else f"no radio assigned   port {record.get('port')}"),
            state="disabled")
        menu.add_separator()
        menu.add_command(
            label="Rename…   (a label only — radio, port and bonds are kept)",
            command=self._deferred(self._rename_device, device_id))
        # The DEVICE record is the single writer of its radio. Assigning it per
        # device (rather than in a fixed global slot) is what lets a device
        # added at runtime ever get one.
        menu.add_command(
            label="Radio…   (this device's own Bluetooth radio)",
            command=self._deferred(self._assign_device_radio, device_id))
        menu.add_command(
            label="Input…   (pointer speed, scroll, modifier keys)",
            command=self._deferred(self._device_input_dialog, device_id))
        menu.add_command(
            label="Displays…   (saving restarts input ~8s)",
            command=self._deferred(self._edit_device_displays, device_id))
        menu.add_separator()
        menu.add_command(
            label="Remove…   (unpair it first if it is still bonded)",
            command=self._deferred(self._remove_device, device_id))

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
                 font=(FONT_UI_SEMI, 12)).pack(
            anchor="w", padx=18, pady=(16, 2))
        tk.Label(win,
                 text="Enter each screen's diagonal in inches. Everything is "
                      "drawn to the same physical scale, so a 32\" really is "
                      "about twice the width of a 17\". Resolution is not "
                      "changed.",
                 bg=CARD, fg=MUTED, font=(FONT_UI, 9), wraplength=440,
                 justify="left").pack(anchor="w", padx=18, pady=(0, 10))
        rows = []

        def add_row(parent, label, detail, current):
            box = tk.Frame(parent, bg=CARD)
            box.pack(fill="x", padx=18, pady=2)
            tk.Label(box, text=label, bg=CARD, fg=FG, width=22, anchor="w",
                     font=(FONT_UI, 10)).pack(side="left")
            var = tk.StringVar(value=f"{float(current or 0):g}"
                               if current else "")
            ttk.Entry(box, textvariable=var, width=7).pack(side="left")
            tk.Label(box, text="in", bg=CARD, fg=MUTED,
                     font=(FONT_UI, 9)).pack(side="left", padx=(4, 10))
            tk.Label(box, text=detail, bg=CARD, fg=MUTED,
                     font=("Consolas", 8)).pack(side="left")
            return var

        tk.Label(win, text="This PC", bg=CARD, fg=ACCENT,
                 font=(FONT_UI_SEMI, 10)).pack(anchor="w", padx=18,
                                                      pady=(6, 2))
        for monitor in self.canvas.monitors:
            var = add_row(win, monitor["name"].replace("\\\\.\\", ""),
                          f"{monitor['w']}x{monitor['h']}",
                          monitor.get("diagonal_in"))
            rows.append(("monitor", monitor, var))
        for device in self.canvas.devices():
            tk.Label(win, text=device.get("name", device["id"]), bg=CARD,
                     fg=ACCENT, font=(FONT_UI_SEMI, 10)).pack(
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

    # ===================================================================
    # The arrangement's right-click menus
    #
    # "why can't i right click a screen to change its res, size, hz?" -- every
    # ingredient already existed (_hit_key, _lookup, _detail_lines, physical_
    # size, MacDisplayEditor); nothing was wired to a Button-3. It is wired
    # here, in App, because the handler reaches things the canvas deliberately
    # knows nothing about.
    #
    # The two menus are deliberately NOT the same menu:
    #
    #   * a managed device's screen is described by THIS app -- its resolution,
    #     rotation, refresh and diagonal are our numbers, and editing them is
    #     the point.
    #   * a Windows monitor is described by WINDOWS. It owns the position, the
    #     resolution, the refresh rate and the primary flag, and it can be asked
    #     for all four. So those are shown and re-readable, never editable: an
    #     entry that looked like it could set a Windows display mode would be a
    #     lie. The diagonal is the single field Windows does not know, and it is
    #     the single field this menu lets you type.
    # ===================================================================

    def _deferred(self, fn, *args):
        """A menu command that runs AFTER the menu has finished unposting.

        EVERY command in these menus goes through this. FrameModal.grab_set
        records grab_current() as _prev_grab and hands the grab back when the
        modal closes; opened inline from a posted menu it captures the MENU and
        then returns the grab to an unposted widget, leaving the whole window
        mouse-dead. The tray menu already defers for exactly this reason.

        Uniform rather than case-by-case on purpose: "Resolution" looks like it
        opens nothing at all, right up until the screen it is aimed at has no
        diagonal yet and has to ask for one.
        """
        return lambda: self.root.after(0, lambda: fn(*args))

    def _canvas_menu(self, event):
        """Right-click anything -- or nothing -- on the arrangement."""
        if self.canvas.action:      # mid-drag: the guard _on_hover uses
            return
        key, item = self.canvas._hit_key(event)
        # Select FIRST, and redraw, so the white outline and the menu's subject
        # are the same rectangle. A menu about a screen the user is not looking
        # at is how you edit the wrong one.
        self.canvas.selected = key
        self.canvas.redraw()
        if key is None:
            menu = self._desk_menu
            self._fill_desk_menu(menu)
        else:
            menu = self._surface_menu
            self._fill_surface_menu(menu, key, item)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _resolution_presets(self, key, item):
        """The resolutions it is HONEST to offer this particular screen."""
        device = self.canvas.target(key[1]) or {}
        if display_kind(device, item) == "ipad":
            options = [(label, tuple(size))
                       for label, size in IPAD_PRESETS.items()]
        else:
            options = [(label, tuple(size)) for label, size in DESKTOP_PRESETS]
        current = (int(item.get("res_w", 0)), int(item.get("res_h", 0)))
        if current not in [size for _label, size in options]:
            options.append(
                (f"{current[0]} × {current[1]}  (current)", current))
        return options

    @staticmethod
    def _refresh_presets(item):
        values = [float(v) for v in REFRESH_PRESETS]
        current = item.get("refresh_hz")
        if current:
            values.append(float(current))
        return sorted(set(values))

    def _fill_surface_menu(self, menu, key, item):
        menu.delete(0, "end")
        title, lines = self.canvas._detail_lines(key, item)
        menu.add_command(label=title, state="disabled")
        for line in lines[:2]:
            menu.add_command(label="   " + line, state="disabled")
        menu.add_separator()
        if key[0] == "local":
            self._fill_local_entries(menu, key, item)
            return
        current = (int(item.get("res_w", 0)), int(item.get("res_h", 0)))
        # Repopulated, never rebuilt -- see App.__init__. delete(0, "end") is
        # what releases the previous popup's Tcl command objects.
        res = self._res_menu
        res.delete(0, "end")
        for label, (width, height) in self._resolution_presets(key, item):
            res.add_command(
                label=("✓ " if (width, height) == current else "    ") + label,
                command=self._deferred(
                    self._menu_set_resolution, key, width, height))
        # Every label that costs a portal restart says so. portal_signature
        # includes each display's x/y/w/h/res_w/res_h/rotation, so resolution,
        # rotation and diagonal each taskkill and respawn the portal on the Tk
        # thread across all three lanes -- about eight seconds of dead input.
        # Refresh rate and renames are genuinely free; the signature's own
        # docstring says so.
        menu.add_cascade(label="Resolution   (restarts input ~8s)", menu=res)
        hz_menu = self._hz_menu
        hz_menu.delete(0, "end")
        now_hz = item.get("refresh_hz")
        for value in self._refresh_presets(item):
            same = now_hz and abs(float(now_hz) - value) < 0.01
            hz_menu.add_command(
                label=("✓ " if same else "    ") + hz_label(value),
                command=self._deferred(self._menu_set_refresh, key, value))
        menu.add_cascade(label="Refresh rate   (free)", menu=hz_menu)
        menu.add_separator()
        menu.add_command(label="Rotate 90°   (restarts input ~8s)",
                         command=self._deferred(self._menu_rotate, key))
        menu.add_command(label="Diagonal…   (restarts input ~8s)",
                         command=self._deferred(self._menu_diagonal, key))
        menu.add_separator()
        device = self.canvas.target(key[1]) or {}
        menu.add_command(
            label=f"Edit all screens on {device.get('name', key[1])}…",
            command=self._deferred(self._menu_device_editor, key[1]))
        self._fill_device_verb_entries(menu, key[1])

    # ---- the four connection verbs, as menu entries ------------------------
    # Doug: "it'd be nice if i could have my Pair connect disconnect unpair
    # options here, only surfacing two at a time based on what is relevant to
    # toggle".
    #
    # TWO IS NOT THE COUNT, and guessing it would have shipped a menu that hid
    # a live action. Run DEVICE_VERB_GATES through the real states:
    #
    #   unpaired                Pair                                    (1)
    #   paired, not connected   Pair, Connect, Unpair                   (3)
    #   connected               Disconnect, Unpair                      (2)
    #   mid-pair (busy)         Disconnect, which here means CANCEL     (1)
    #   no radio / radio gone   none                                    (0)
    #
    # Pair is live on a bonded-but-idle lane because it is NOT gated on "not
    # paired": re-pairing a bonded device is legal and is how a bad bond gets
    # recovered. So the rule is SHOW THE ONES THAT ARE LIVE, whatever the
    # count, and the zero case gets a reason rather than an empty section.
    #
    # Nothing in this section restarts the input portal. That was measured, not
    # assumed: portal_signature is taken over CONFIG fields (monitor and
    # display geometry, per-device input settings), and none of the four verbs
    # writes config -- they move bond and link state, which the signature does
    # not contain. The display entries above them cost eight seconds and say
    # so; these do not, and must not claim to.

    def _device_verb_entries(self, device_id):
        """(verb, label, enabled) for every verb entry this device gets, in
        DEVICE_VERB_SPEC order. Decides; renders nothing.

        Split from the filler so the decision can be driven through every
        device state without a window, and so the menu is WRITTEN FROM this
        list rather than alongside it -- a returned description that the
        renderer then ignores is the same drift this whole wave is closing.

        The gate is device_verb_offer on facts from _device_verb_facts: the
        same call, on the same producer, that paints the card's four buttons.
        There is deliberately no predicate of any kind in this method.
        """
        record = self.device_record(device_id) or {}
        facts = self._device_verb_facts(dict(record, id=device_id))
        offered = device_verb_offer(facts)
        rows = []
        for key, resting, in_flight in DEVICE_VERB_SPEC:
            # The in-flight verb OVERRIDES its own gate, exactly as it does on
            # the card: _apply_device_rows paints that one button with the
            # present participle and disables it whatever the gate said. If
            # the menu instead dropped it, the surface the user right-clicked
            # to check on a pair attempt would be the one surface that never
            # mentions it.
            if key == facts["verb"]:
                rows.append((key, in_flight, False))
                continue
            if not offered[key]:
                continue
            rows.append((key, self._verb_menu_label(key, resting, facts), True))
        return rows

    @staticmethod
    def _verb_menu_label(key, resting, facts):
        """The resting label plus what it COSTS. Timings are the real ones.

        Disconnect is the one verb whose name is wrong half the time: its gate
        is `live or busy`, so it is also the CANCEL for a pair attempt, and
        mid-pair the card's "Disconnect" offers to end a connection that does
        not exist yet. The card cannot say otherwise -- its label comes
        straight out of DEVICE_VERB_SPEC and is four characters wide. A menu
        entry can, so here it does.
        """
        if key == "disconnect" and facts["busy"] and not facts["live"]:
            return DEVICE_VERB_CANCEL_LABEL
        return DEVICE_VERB_MENU_SUFFIX[key].format(verb=resting)

    def _fill_device_verb_entries(self, menu, device_id):
        """Append the verb section to an already-filled menu. Returns the rows.

        NAMED, always. This menu is opened on one DISPLAY, but every verb here
        acts on the whole DEVICE: right-clicking any one of the Managed Mac's
        three panels and choosing Unpair unpairs the Mac. A header carrying the
        device's name is the difference between that being obvious and it being
        a trap.

        Every enabled entry is deferred, like every other command in these
        menus. Pair and Unpair open dark_confirm and Connect can open
        dark_alert -- all FrameModals, and FrameModal.grab_set records
        grab_current(): opened inline from a posted menu it captures the MENU,
        hands the grab back to a widget that is no longer posted, and the whole
        window goes mouse-dead. Disconnect opens nothing today, and is deferred
        anyway -- see _deferred's own docstring on why this is uniform.
        """
        record = self.device_record(device_id) or {}
        rows = self._device_verb_entries(device_id)
        menu.add_separator()
        menu.add_command(
            label=f"{record.get('name', device_id)} — connection",
            state="disabled")
        for key, label, enabled in rows:
            if not enabled:
                menu.add_command(label="   " + label, state="disabled")
                continue
            menu.add_command(
                label="   " + label,
                command=self._deferred(
                    getattr(self, DEVICE_VERB_HANDLERS[key]), device_id))
        if not rows:
            facts = self._device_verb_facts(dict(record, id=device_id))
            if facts["radio_missing"]:
                why = "its radio is not present"
            elif not facts["radio"]:
                why = "no radio assigned"
            elif not facts["vm"]:
                why = "the VM is not answering"
            else:
                why = "nothing to do right now"
            menu.add_command(label=f"   — {why} —", state="disabled")
        return rows

    def _fill_local_entries(self, menu, key, _item):
        """A Windows monitor's menu. Honest about who owns what.

        The user's own instruction: "Shouldn't we just accept the state of
        windows, maybe just like a refresh now button tho. I think the only
        thing that we'd want to change on the windows manually is the monitor
        size." That is exactly the ownership boundary, so it is exactly what
        this menu offers."""
        item = self.canvas._lookup(key) or {}
        menu.add_command(
            label=f"Resolution   {item.get('w')} × {item.get('h')}"
                  "   — Windows owns this",
            state="disabled")
        hz = hz_label(item.get("refresh_hz"))
        menu.add_command(
            label=(f"Refresh   {hz}   — Windows owns this" if hz
                   else "Refresh   not reported by Windows"),
            state="disabled")
        menu.add_separator()
        # BOTH facts, because both are true of this one entry. It is the only
        # field Windows cannot supply -- AND it costs the same eight seconds as
        # its managed-display twin: _menu_diagonal writes layout_w/layout_h for
        # a local key, and portal_signature lists layout_w/layout_h per
        # monitor, so the portal is taskkilled and respawned across all three
        # lanes. The twin in _fill_surface_menu says so; this one used to not.
        menu.add_command(
            label="Diagonal…   (the one Windows cannot tell us — "
                  "restarts input ~8s)",
            command=self._deferred(self._menu_diagonal, key))
        menu.add_command(label="Refresh now   (re-read Windows)",
                         command=self._deferred(self._menu_refresh_monitors))
        menu.add_command(label="Open Windows display settings…",
                         command=self._deferred(self._menu_display_settings))

    def _fill_desk_menu(self, menu):
        """Right-click on empty canvas: the arrangement itself.

        "Screen sizes..." lives here and nowhere else now. _hit_key returns only
        the TOPMOST rectangle, so a device display parked over a monitor makes
        that monitor unreachable by right-click; the all-surfaces table is the
        escape hatch and must not die with the button that used to open it."""
        menu.delete(0, "end")
        menu.add_command(label="Arrangement", state="disabled")
        menu.add_separator()
        menu.add_command(label="Screen sizes…   (every surface at once)",
                         command=self._deferred(self._screen_sizes_dialog))
        devices = self.canvas.devices()
        # Cleared unconditionally, cascaded only when there is something to
        # cascade: the submenu is persistent now, so leaving last popup's
        # devices in it would outlive the device that was removed.
        sub = self._device_menu
        sub.delete(0, "end")
        if devices:
            for device in devices:
                sub.add_command(
                    label=f"{device.get('name', device['id'])}…",
                    command=self._deferred(
                        self._menu_device_editor, device["id"]))
            menu.add_cascade(label="Edit a device's screens", menu=sub)
        menu.add_separator()
        menu.add_command(label="Refresh Windows screens now",
                         command=self._deferred(self._menu_refresh_monitors))
        menu.add_command(label="Open Windows display settings…",
                         command=self._deferred(self._menu_display_settings))

    # ---- the write path -------------------------------------------------
    # ONE sequence, no exceptions: re-resolve, set the field, recompute the
    # rectangle from the diagonal, redraw, save ONCE.

    def _ask_diagonal(self, key, item):
        """Ask for the one number nobody but the user can supply.

        NEVER defaulted and never silently assumed. physical_size clamps a
        missing diagonal to max(1.0, ...), which collapses the rectangle to
        MIN_LAYOUT_SIZE and moves every crossing band on that screen --
        _normalize_display only sets diagonal_in when it is truthy and
        new_device never writes it at all, so absent is a real case."""
        title, _lines = self.canvas._detail_lines(key, item)
        current = item.get("diagonal_in")
        text = dark_prompt(
            self.root, "Screen diagonal",
            f"{title}\n\nDiagonal in inches, corner to corner. Every surface "
            "is drawn to the same physical scale, so a 32\" really is about "
            "twice the width of a 17\".",
            default=(f"{float(current):g}" if current else ""))
        if text is None or not str(text).strip():
            return None
        try:
            return max(1.0, min(120.0, float(str(text).strip())))
        except ValueError:
            dark_alert(self.root, "Not a number",
                       f"“{text}” is not a screen size in inches.")
            return None

    def _edit_display(self, key, mutate):
        """Apply one complete change to one managed display, and save once.

        The display dict is re-resolved HERE, at invoke time, and not captured
        when the menu was built: adopt()'s own docstring warns that a stale
        display dict "survives every redraw looking perfectly valid", and every
        command is deferred, so an arrangement can have been switched between
        the click and this call."""
        item = self.canvas._lookup(key)
        if item is None or key[0] != "target":
            return False
        if not item.get("diagonal_in"):
            inches = self._ask_diagonal(key, item)
            if inches is None:
                return False
            # The prompt ran a nested event loop. Re-resolve again.
            item = self.canvas._lookup(key)
            if item is None:
                return False
            item["diagonal_in"] = inches
        mutate(item)
        # ALWAYS recomputed, from the RAW resolution -- physical_size does the
        # rotation swap itself, so pre-swapping it here would square the turn.
        item["w"], item["h"] = physical_size(
            float(item["diagonal_in"]), int(item["res_w"]), int(item["res_h"]),
            int(item.get("rotation", 0)))
        self.canvas.redraw()
        self.canvas.save()
        return True

    def _menu_set_resolution(self, key, res_w, res_h):
        def mutate(item):
            item["res_w"], item["res_h"] = int(res_w), int(res_h)
        # Re-selecting the resolution a screen is ALREADY set to writes the
        # same numbers back: save() sees an identical portal_signature and
        # restarts nothing. Announcing a reload that did not happen teaches the
        # console cannot be trusted about the eight seconds it DOES cost.
        before = portal_signature(self.canvas.config)
        if self._edit_display(key, mutate):
            if portal_signature(self.canvas.config) != before:
                _emit("event", f"screen resolution set to {res_w}×{res_h} — "
                               "portal reloading.")
            else:
                _emit("event", f"screen resolution already {res_w}×{res_h} — "
                               "nothing changed.")

    def _menu_rotate(self, key):
        # rotate_display sets the rotation and swaps the rectangle; _edit_
        # display then re-derives that same rectangle from the diagonal, so the
        # two can never disagree about a screen's real size.
        if self._edit_display(key, rotate_display):
            _emit("event", "screen rotated — portal reloading.")

    def _menu_set_refresh(self, key, hz):
        """Refresh rate is the one edit that is genuinely free: portal_signature
        does not include it, so save() writes the file and restarts nothing.

        Guarded on key[0] exactly like _edit_display. Nothing reaches here with
        a local key today -- _fill_local_entries offers no refresh entry, on
        purpose, because Windows owns that number -- but a future "refresh now"
        variant that did would stamp an app-invented refresh_hz onto a Windows
        monitor row, which is precisely the invented number this menu exists to
        have killed."""
        item = self.canvas._lookup(key)
        if item is None or key[0] != "target":
            return
        item["refresh_hz"] = float(hz)
        self.canvas.redraw()
        self.canvas.save()

    def _menu_diagonal(self, key):
        item = self.canvas._lookup(key)
        if item is None:
            return
        inches = self._ask_diagonal(key, item)
        if inches is None:
            return
        item = self.canvas._lookup(key)      # the prompt ran an event loop
        if item is None:
            return
        # Re-typing the diagonal a screen already carries re-derives the same
        # rectangle, leaves portal_signature identical and restarts nothing.
        before = portal_signature(self.canvas.config)
        item["diagonal_in"] = inches
        if key[0] == "local":
            # A local monitor's w/h ARE its pixels; its desk rectangle is
            # layout_w/layout_h. Same derivation as "Screen sizes...".
            item["layout_w"], item["layout_h"] = physical_size(
                inches, item["w"], item["h"], 0)
        else:
            item["w"], item["h"] = physical_size(
                inches, int(item["res_w"]), int(item["res_h"]),
                int(item.get("rotation", 0)))
        self.canvas.redraw()
        self.canvas.save()
        if portal_signature(self.canvas.config) != before:
            _emit("event", f"screen diagonal set to {inches:g}\" — "
                           "portal reloading.")
        else:
            _emit("event", f"screen diagonal already {inches:g}\" — "
                           "nothing changed.")

    def _menu_device_editor(self, device_id):
        # Delegated, not duplicated. The Devices panel's own button already
        # opens this editor with a device_id and rebuilds the rows afterwards;
        # a second call site that did nine tenths of that is precisely how the
        # deleted global button came to resolve to the first device.
        self._edit_device_displays(device_id)

    def _menu_refresh_monitors(self, automatic=False):
        """Re-read Windows, MERGING rather than replacing.

        Matched by monitor name. diagonal_in and the hand-placed layout
        position survive for every monitor that is still attached -- a refresh
        that reset diagonal_in would destroy the only field the user can supply,
        which is the whole point of the merge.

        `automatic` means the display watcher called this, not a person. The
        only difference is that a background poll must never raise a modal:
        a dialog nobody asked for, on top of whatever they were doing, over a
        screen change they can already see, is worse than the problem.
        """
        live = enum_monitors()
        if not live:
            if automatic:
                _emit("err", "Windows reported no monitors — leaving the "
                             "arrangement alone.")
                return
            dark_alert(self.root, "No monitors reported",
                       "Windows returned no monitors. Nothing was changed.")
            return
        merged, report = merge_live_monitors(self.canvas.monitors, live)
        # portal_signature serialises monitors as an ORDERED list, and this is
        # the one place that adopts EnumDisplayMonitors' order wholesale. Sorted
        # by name, the order is a function of WHICH monitors are attached and of
        # nothing else, so the same panels enumerated in a different sequence
        # cannot restart the portal behind a "nothing changed" report.
        merged.sort(key=lambda row: str(row.get("name", "")))
        summary = describe_monitor_refresh(report)
        before = portal_signature(self.canvas.config)
        # One list object, referenced from both places the canvas reads it.
        self.canvas.config["monitors"] = merged
        self.canvas.monitors = merged
        # The button carries no cost label because most re-reads cost nothing.
        # A real Windows change -- a resolution, a panel added or removed --
        # moves the merged rectangles, and save() below taskkills and respawns
        # the portal across all three lanes. So the OUTCOME says so, which is
        # the honest place for a cost that is conditional.
        if portal_signature(self.canvas.config) != before:
            summary += " — portal reloading, input back in ~8s"
        self.canvas.redraw()
        self.canvas.save()
        lead = ("Windows screens changed" if automatic
                else "Windows screens re-read")
        _emit("event", f"{lead} — {summary}.")
        try:
            self.status.set(f"{lead} — {summary}")
        except tk.TclError:
            pass

    # ---- the desk is READ, not remembered ---------------------------------

    DISPLAY_POLL_MS = 4000

    @staticmethod
    def _display_signature(monitors):
        """Which screens are attached, and where. Order-independent."""
        return tuple(sorted(
            (str(m.get("name", "")), int(m.get("x", 0)), int(m.get("y", 0)),
             int(m.get("w", 0)), int(m.get("h", 0)), bool(m.get("primary")))
            for m in monitors))

    def _watch_displays(self):
        """Notice a screen arriving or leaving, without being asked.

        Doug, 2026-08-15: his Mac 2k arrangement "lost its third display".
        It was never lost. The arrangement is a SNAPSHOT taken when it was
        saved, and DISPLAY4 was detached at that moment; when it came back,
        nothing re-read the desk, so the saved two-monitor picture went on
        being treated as the truth. Windows had three.

        The same shape as every other fault of this day: a fact captured once
        instead of derived. The merge that fixes it already existed and was
        careful -- it keeps diagonal_in and every hand-placed position -- and
        was simply never reached except by pressing a button.

        A poll rather than a WM_DISPLAYCHANGE hook because Tk offers no clean
        WndProc and EnumDisplayMonitors costs microseconds. If a message hook
        ever exists here for another reason, this should move onto it: an
        event is exact where a poll is merely frequent.
        """
        if getattr(self, "_closing", False):
            return
        try:
            live = enum_monitors()
            if live and getattr(self, "canvas", None) is not None:
                signature = self._display_signature(live)
                if self._display_sig is None:
                    self._display_sig = signature      # first look, not a change
                elif signature != self._display_sig:
                    self._display_sig = signature
                    self._menu_refresh_monitors(automatic=True)
        except Exception as exc:  # noqa: BLE001
            # A watcher that dies takes the desk's only link to reality with
            # it, so it reports and keeps going rather than raising out of a
            # timer callback.
            _emit("err", f"display watch: {type(exc).__name__}: {exc}")
        finally:
            try:
                self.root.after(self.DISPLAY_POLL_MS, self._watch_displays)
            except tk.TclError:
                pass

    def _menu_display_settings(self):
        try:
            os.startfile("ms-settings:display")
        except OSError:
            dark_alert(self.root, "Could not open display settings",
                       "Windows would not open the display settings page.")

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
                 font=(FONT_UI_SEMI, 12)).pack(
            anchor="w", padx=18, pady=(16, 2))
        tk.Label(win,
                 text="Applied by OpenSpan, not by the device — so the "
                      "device keeps its own settings and stays usable on its "
                      "own.",
                 bg=CARD, fg=MUTED, font=(FONT_UI, 9), wraplength=430,
                 justify="left").pack(anchor="w", padx=18, pady=(0, 10))

        state = {}

        def slider(key, title, lo, hi, default, hint, notches=None):
            box = tk.Frame(win, bg=CARD)
            box.pack(fill="x", padx=18, pady=(6, 0))
            head = tk.Frame(box, bg=CARD)
            head.pack(fill="x")
            tk.Label(head, text=title, bg=CARD, fg=FG,
                     font=(FONT_UI, 10)).pack(side="left")
            val = tk.Label(head, text="", bg=CARD, fg=ACCENT,
                           font=("Consolas", 10))
            val.pack(side="right")
            var = tk.DoubleVar(value=float(record.get(key, default)))

            def on_move(_v=None):
                val.config(text=(format_sensitivity(var.get()) if notches
                                 else f"{var.get():.2f}"))
            var.trace_add("write", lambda *_a: on_move())

            if notches:
                scale = notched_scale(box, var, notches)
            else:
                scale = ttk.Scale(box, from_=lo, to=hi, variable=var,
                                  orient="horizontal")
            scale.pack(fill="x", pady=(2, 0))
            tk.Label(box, text=hint, bg=CARD, fg=MUTED,
                     font=(FONT_UI, 8), wraplength=430,
                     justify="left").pack(anchor="w")
            on_move()
            state[key] = var

        slider("sensitivity", "Mouse sensitivity", 0.25, 3.0, 1.0,
               "How far the pointer travels on this device for the same hand "
               "movement. Lower it if this device feels too fast. The slider "
               "steps between set values, finely where the useful range is.",
               notches=SENSITIVITY_NOTCHES)
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
                 bg=CARD, fg=MUTED, font=(FONT_UI, 8), wraplength=430,
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
                 bg=CARD, fg=MUTED, font=(FONT_UI, 8), wraplength=430,
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
                 font=(FONT_UI, 10)).pack(side="left")
        ttk.Combobox(altbox, textvariable=alt_var, width=12, state="readonly",
                     values=("option", "command", "inherit")).pack(
            side="left", padx=(10, 0))
        tk.Label(win, text="option = macOS Option  ·  command = iPad Command",
                 bg=CARD, fg=MUTED, font=(FONT_UI, 8)).pack(
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
                           f"{format_sensitivity(record['sensitivity'])}, "
                           f"acceleration "
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
        if self._portal_live():
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
        on = self._portal_live()
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
        self.ui(lambda: self._apply_poll(running, st, on, aud))

    def _apply_poll(self, running, st, on, aud):
        """Repaint every global status surface from one tick's facts.

        There is no `mac_st` parameter any more and no read of one. It was
        assigned None in _poll, never reassigned, and four surfaces here tested
        it -- so `mac_st is not None` was False on every tick this app has ever
        run, and the System control line reported the Managed Mac as DOWN while
        it was connected. Everything that used it now comes out of
        device_status_rollup, which is N devices wide.
        """
        # per-indicator status row: each token coloured by ITS OWN live state.
        roll = device_status_rollup(self.canvas.devices(), self._dev_status)

        def setind(key, text, good):
            self._ind[key].config(text=text, fg=(ACCENT if good else MUTED))
        setind("vm", f"VM {'●' if running else '○'}", running)
        if st:
            # This token speaks for the FIRST configured device, under that
            # device's OWN name and its OWN facts.
            #
            # It used to render the hardcoded word "iPad" over `_bonded =
            # any(... for d in devices())` -- one device's name printed on top
            # of an all-devices aggregate. On a three-device desk that made the
            # token contradict the card three inches below it, on the same tick,
            # and it was the surviving half of the same two-device model whose
            # Mac half made System control permanently read "Mac ○ down".
            #
            # A token that names one machine must speak only for that machine.
            # The aggregate already has its own home: the `devices N/M` token
            # two places along, which says so in its text.
            _devs = self.canvas.devices()
            _first = _devs[0] if _devs else None
            _fname = ((_first.get("name") or _first["id"]) if _first
                      else "device")
            _sub = bool(st.get("kbd_subscribed"))
            _bonded = bool(
                self._dev_state(_first["id"])["paired"]) if _first else False
            # The SAME truth table the device cards use, so the top of the
            # window and the device area cannot disagree about what is wrong.
            # The "— portal off" half of the text is dropped here because this
            # row already carries its own `portal ● ON / ○ off` token two
            # places along; in the row the colour alone carries the register.
            _col, _txt = device_state_colour(on, _sub, _bonded)
            if _txt.endswith(PORTAL_OFF_SUFFIX):
                _txt = _txt[:-len(PORTAL_OFF_SUFFIX)]
            _mark = "●" if (_sub and on) else ("◐" if (_sub or _bonded) else "○")
            self._ind["ipad"].config(text=f"{_fname} {_mark} {_txt}", fg=_col)
        elif running:
            setind("ipad", "iPad ○ daemon starting", False)
        else:
            setind("ipad", "iPad ○ off", False)
        # one summary token for ALL devices -- the per-device detail lives in
        # the Devices panel, so the row does not grow a column per machine.
        # The dict key is still "mac" for the same reason the file kept it: it
        # is a slot in self._ind, not a claim about a machine. The TEXT is an
        # honest aggregate and says the word "devices".
        if roll["total"]:
            self._ind["mac"].config(
                text=f"devices {roll['live']}/{roll['total']}",
                fg=(ACCENT if roll["live"] else MUTED))
        else:
            self._ind["mac"].config(text="no devices", fg=MUTED)
        setind("portal", f"portal {'● ON' if on else '○ off'}", on)
        setind("audio", f"audio {'●' if aud else '○'}", aud)
        # Honest broadcast state, read straight from each device's own daemon
        # -- never a UI guess: if a daemon says BROADCASTING that machine
        # really is advertising. This used to be exactly two names, "iPad" and
        # "Mac", the second of which could never light because its status was
        # the dead `mac_st`. Now it names whichever devices are actually
        # beaconing, however many there are.
        _adv = bool(roll["advertising"])
        # broadcast_names, not " + ".join: this is the widest token in the row
        # and the last one packed, so on a three-device desk the joined form
        # spent 304px of a 908px cavity and pushed the tail of the row over the
        # edge. See INDICATOR_ORDER.
        _adv_names = broadcast_names(roll["advertising"])
        setind(
            "bcast",
            (f"📡 {_adv_names} BROADCASTING"
             if _adv else "📡 not broadcasting") if roll["reachable"] else "",
            _adv)
        # The boolean above is confirmed BlueZ state. Transitional and failure
        # states get their own honest, non-green rendering.
        _adv_state = roll["adv_state"]
        _adv_error = roll["adv_error"]
        if roll["reachable"] and not _adv:
            # broadcast_token, not a raw fg=WARN. This token was the last
            # full-strength amber in the file outside the Warn.TButton style,
            # and while the portal is down it is a consequence like every other
            # idle thing here -- so it takes the suppressed register too.
            _token = broadcast_token(_adv_state, _adv_error, on)
            if _token:
                self._ind["bcast"].config(text=_token[0], fg=_token[1])
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
            # NOT wrapped in a silent except. This is the readiness banner --
            # the surface whose disappearance was a W4 fatal and whose placement
            # W7 spent a whole section proving. A swallow here means it silently
            # stops updating while every other surface keeps refreshing, which
            # is indistinguishable from "the state genuinely has not changed".
            # If this raises, _drain_ui reports it now.
            self.ready_lbl.config(text=r_txt, fg=r_col)
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
        # snapshot for the tray menu (built on the Tk thread; must never block)
        self._cache = {"running": running, "connected": connected, "on": on,
                       "aud": aud,
                       "busy": self._any_device_busy()}
        # `connected`, not `connected and on` -- the portal is its own argument
        # now, for the same reason it is in _apply_device_rows below: folding it
        # into liveness is what made the canvas unable to tell a stopped portal
        # from an unconnected device.
        self.canvas.set_ipad_state(connected, False, on)
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
                        if not self._portal_live():
                            self.toggle_portal()
                            _emit("event", f"{_label} paired — portal "
                                           "auto-started.")
                        on = self._portal_live()
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
        # `on` reflects the portal we may have just started this tick.
        #
        # ONE fact, and it is this line's alone: how many device daemons
        # ANSWERED this tick. Everything else it used to carry had a better
        # home:
        #
        #   * "Mac ● up / ○ down" was the dead two-device model. It is gone,
        #     not relocated.
        #   * "VM", "audio" and "portal" are three tokens in the indicator row
        #     at the top of this window, in a larger font, always visible.
        #     Restating them here in 8pt was the same duplication the five
        #     compact-mode dots were deleted for, and the same argument
        #     retires it.
        #   * "keyboard ● up" was daemon_status(), which is by its own
        #     docstring the FIRST configured device's daemon. Since this wave
        #     every card reports its own daemon's reachability, so that claim
        #     now belongs to a card and not to a global line.
        #
        # What no card and no token has is the roll-up, which is exactly the
        # shape the indicator row's `devices N/M` already uses for the other
        # half of the same question: that token counts SUBSCRIBED, this line
        # counts ANSWERING, and the difference between the two numbers is the
        # difference between "the lane is idle" and "the lane is not there".
        #
        # No blanket try/except around it any more. This block was wrapped so
        # that a fault here could not abort the tick, but the wrap made the
        # fault INVISIBLE while everything below it kept running: the classic
        # half-frozen window with nothing in any log. _drain_ui now reports
        # what it swallows, so a real fault surfaces in the console instead.
        self.sys_status.set(
            f"device daemons {'●' if roll['reachable'] else '○'} "
            f"{roll['reachable']}/{roll['total']} answering"
            if roll["total"] else "no devices configured")
        # while hidden in the tray, make sure the icon still exists (an
        # explorer.exe restart wipes tray icons); if it can't be restored,
        # bring the window back — the app must never be strandable
        if self._tray and not self._closing \
                and self.root.state() == "withdrawn":
            if not self._tray.ensure():
                _emit("event", "tray icon lost — bringing the window back.")
                self._from_tray()
        # ---- compact-mode widgets (cheap; update even when hidden) ----
        # The five c_stat dots and the second c_ready banner that stood here
        # are deleted, not moved. They were a verbatim second copy of the
        # indicator row and of ready_lbl, painted from the same variables in
        # the same tick -- and one of the five, "Mac", was wired to the dead
        # two-device model and could never light at all.
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
                # Not a timer. A spinner that never resolves is the app knowing
                # something is wrong and saying nothing -- it span for as long as
                # it was left running while every fact needed to explain it was
                # one command away. The sentence is computed on a worker (it
                # costs an ssh) and cached; the timer is only the fallback for
                # the first few seconds.
                self._boot_why_probe()
                # getattr, not the attribute: the watcher's first tick arrives
                # before any probe has finished, and a bare read would raise
                # AttributeError inside the status poll -- which is exactly the
                # kind of failure that turns into "the app just sits there"
                self.status.set(getattr(self, "_boot_why", "")
                                or "Booting the bridge… (~90s)")
            # Named when one device is live, counted when several are. The
            # branch this replaces was "iPad connected" / "Device connected",
            # and the second of those was unreachable: it was gated on
            # mac_connected, which was derived from the dead `mac_st`.
            elif roll["live"] == 1:
                self.status.set(f"{roll['live_names'][0]} connected — "
                                "keyboard & mouse bridging.")
            elif roll["live"]:
                self.status.set(f"{roll['live']} devices connected — "
                                "keyboard & mouse bridging.")
            else:
                self.status.set("Ready — pair or connect a device.")
        # the Pair button stays a static "Pair"; connection state is shown by the
        # indicator colours + which of Connect/Disconnect/Unpair are enabled.
        # button_is_busy is the guard that makes the pending state survive: this
        # tick rewrites the VM button's label every 3 seconds and would
        # otherwise paint straight over "Starting VM…".
        if not button_is_busy(self.vm_btn):
            self.vm_btn.config(text="Bridge VM ✓" if running
                               else "Start Bridge VM")
        self._render_portal_button(on)


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
    _resolve_brand_fonts(root)
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
