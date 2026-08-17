"""Headless smoke test for the deliberate-pair flow in openspan.py.

Runs the App through the whole Pair/Broadcast state machine with every real
side effect (ssh, VirtualBox, subprocess, audio, tray, schtasks) neutralized,
so the logic can be checked without a VM or a Bluetooth radio:

  1. Confirm = No           -> no state change
  2. Confirm = Yes          -> worker runs, broadcasting = True
  3. iPad connects          -> portal auto-starts, button settles to a check,
                               the portal indicators (button/dot/status line)
                               all read ON the SAME tick, audio reconnect forced
                               past its cooldown/backoff
  4. Reconciler             -> Pair button truthful vs. `connected` when idle
  5. Broadcast fails        -> audio restored, button reset
  6. Broadcast expires 300s -> audio restored, fail counter reset

Requires a display (Tk). On Windows: `python win/test_pair_flow.py`.
Exit code 0 = all pass, 1 = a check failed.
"""
import os
import inspect
import sys
import types

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openspan  # noqa: E402

# ---- NOTHING BELOW MAY TOUCH THE RUNNING APP'S FILES -----------------------
# This file builds a REAL App and calls canvas.save() six times. Until 3 August
# it did that against the LIVE config -- and save() write-throughs to whichever
# arrangement is active, so a suite run silently rewrote Doug's own profile.
# It bit for real: a run under a new save rule stripped every device field out
# of "Mac 2k.json" while the app he was using still expected them there.
#
# The redirect must happen HERE, before the module-level neutralisation below
# and long before App(root), because App reads these at construction. The live
# config is COPIED in so the tests still see a realistic three-device desk;
# every write lands in the copy.
import json  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402

_SCRATCH = tempfile.mkdtemp(prefix="openspan-pairflow-")
_LIVE_CONFIG = openspan.CONFIG
openspan.CONFIG = os.path.join(_SCRATCH, "openspan_config.json")
openspan.PROFILE_DIR = os.path.join(_SCRATCH, "profiles")
openspan.BT_PREFS = os.path.join(_SCRATCH, "bt_prefs.json")
os.makedirs(openspan.PROFILE_DIR, exist_ok=True)
try:
    shutil.copy2(_LIVE_CONFIG, openspan.CONFIG)
except OSError:
    with open(openspan.CONFIG, "w", encoding="utf-8") as _fh:
        json.dump({"version": 3, "monitors": [], "devices": []}, _fh)


def R(rc=0, out="", err=""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


# --- neutralize real side effects (module + class level, before construct) --
openspan.start_vm_clean = lambda *a, **k: None
openspan.vm_running = lambda *a, **k: False
_adv = {"on": False}


def fake_daemon_status():
    # non-None so _pair_worker's "wait for daemon" loop breaks immediately
    return {
        "kbd_subscribed": False,
        "mouse_subscribed": False,
        "advertising": _adv["on"],
        "advertising_state": "on" if _adv["on"] else "off",
        "advertising_error": "",
    }


def fake_set_advertising(on):
    _adv["on"] = bool(on)
    return True


openspan.daemon_status = fake_daemon_status
openspan.set_advertising = fake_set_advertising
openspan.ssh_guest = lambda *a, **k: R(0, "", "")
openspan.vbox = lambda *a, **k: R(0, "", "")
openspan.current_mode = lambda *a, **k: "windows"
openspan.boot_task_exists = lambda *a, **k: True
openspan.ensure_boot_task = lambda *a, **k: None
openspan.ClipboardServer.start = lambda self: False
openspan.App._ensure_audio = lambda self: None
openspan.App._sync_guest_scripts = lambda self: None
openspan.App._tick = lambda self: None
_unsafe_volume_calls = []
openspan.App._volume_thread = \
    lambda self: _unsafe_volume_calls.append(True)
_unsafe_frame_calls = []
openspan.App._dark_titlebar = \
    lambda self: _unsafe_frame_calls.append(True)
_unsafe_tray_calls = []
openspan.App._ensure_tray = \
    lambda self: _unsafe_tray_calls.append(True)


class FakeProc:
    """Stands in for a live subprocess (portal). poll()->None means running."""
    def __init__(self, *a, **k):
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self._alive = False


openspan.subprocess.Popen = FakeProc

# record auto-reconnect invocations instead of doing real ssh
_reconnects = []
openspan.App._auto_reconnect_audio = \
    lambda self, reason: _reconnects.append(reason)

# answer for the (mocked) confirm dialog in the flow sections below; the REAL
# dark_confirm dialog is exercised first, in section 0, before we mock it
_confirm = {"answer": True}

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402
import threading  # noqa: E402,F401
import time  # noqa: E402

root = tk.Tk()
root.withdraw()
app = openspan.App(root)


def drain():
    for _ in range(5):
        try:
            app._drain_ui()   # run queued ui() closures synchronously
            root.update()
        except tk.TclError:
            break


def enabled(widget):
    return "disabled" not in widget.state()


fails = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


# The old frameless window installed a Python ctypes WNDPROC callback. Repeated
# display arrangement messages could outlive that callback and crash in
# _ctypes.pyd. App construction must now leave the native Tk WNDPROC installed.
time.sleep(0.2)
drain()
check("native Tk WNDPROC remains installed", not _unsafe_frame_calls)
time.sleep(0.6)
drain()
check("runtime does not create a ctypes tray WNDPROC", not _unsafe_tray_calls)
check("main UI does not start a Core Audio COM thread",
      not _unsafe_volume_calls)
disconnect_source = inspect.getsource(openspan.App._disconnect_device)
unpair_source = inspect.getsource(openspan.App._unpair_device)
device_disconnect_source = inspect.getsource(openspan.App._disconnect_device)
device_unpair_source = inspect.getsource(openspan.App._unpair_device)
check("disconnecting one device leaves the shared router running",
      "_stop_portal_if_running" not in disconnect_source
      and "_stop_portal_if_running" not in device_disconnect_source)
check("unpairing one device leaves the shared router running",
      "_stop_portal_if_running" not in unpair_source
      and "_stop_portal_if_running" not in device_unpair_source)
# REGRESSION: the guest prints exactly PAIRED or NOT_PAIRED. A bare substring
# test ("PAIRED" in out) is TRUE for "NOT_PAIRED", which pinned every device to
# "paired" forever -- a device stayed yellow after a successful Unpair.
_paired_src = inspect.getsource(openspan.App._refresh_device_paired)
check("paired parse rejects the NOT_PAIRED substring",
      "NOT_PAIRED" in _paired_src)


def _parse_paired(out):
    """Mirror of the app's token test, exercised on the guest's real strings."""
    out = out.upper()
    return "PAIRED" in out and "NOT_PAIRED" not in out


check("paired parse: guest 'PAIRED' -> paired", _parse_paired("PAIRED") is True)
check("paired parse: guest 'NOT_PAIRED' -> NOT paired",
      _parse_paired("NOT_PAIRED") is False)
check("paired parse: trailing newline tolerated",
      _parse_paired("NOT_PAIRED\n") is False
      and _parse_paired("PAIRED\n") is True)

check("unpair forgets only that device's own bond, never a global wipe",
      "--target {device_id}" in device_unpair_source
      and "bluetoothctl remove" not in device_unpair_source)


# Multi-radio USB recovery is deliberately narrow: it may re-arm a shared
# external hub containing two filtered radios, but never a root hub, unrelated
# USB hardware, or the normal single-radio path.
_usb_info = "\n".join([
    'USBFilterActive1="on"',
    'USBFilterVendorId1="8087"',
    'USBFilterProductId1="0aaa"',
    'USBFilterActive2="on"',
    'USBFilterVendorId2="2357"',
    'USBFilterProductId2="0604"',
])
_radio_hub = r"USB\VID_05E3&PID_0610\HUB"
_pnp_lines = "\n".join([
    r"USB\VID_8087&PID_0AAA\INTERNAL|USB\ROOT_HUB30\ROOT",
    rf"USB\VID_2357&PID_0604\RADIO1|{_radio_hub}",
    rf"USB\VID_2357&PID_0604\RADIO2|{_radio_hub}",
    rf"USB\VID_9999&PID_9999\OTHER|{_radio_hub}",
])
check("multi-radio USB recovery: shared filtered hub is selected",
      openspan._shared_filtered_radio_hubs(
          _usb_info, _pnp_lines) == [_radio_hub])
check("multi-radio USB recovery: one radio never cycles a hub",
      openspan._shared_filtered_radio_hubs(
          _usb_info, _pnp_lines.splitlines()[1]) == [])
check("multi-radio USB recovery: root hub is never selected",
      openspan._shared_filtered_radio_hubs(
          _usb_info,
          "\n".join([
              r"USB\VID_2357&PID_0604\RADIO1|USB\ROOT_HUB30\ROOT",
              r"USB\VID_2357&PID_0604\RADIO2|USB\ROOT_HUB30\ROOT",
          ])) == [])


# Startup choice is intentionally tested without constructing another App.
# Both Close and Restart must return before key setup or VM/audio workers.
_startup_originals = {
    "is_elevated": openspan.is_elevated,
    "single_lock": openspan._single_instance_lock,
    "gate": openspan._elevation_gate,
    "release": openspan._release_single_instance_lock,
    "launch": openspan._launch_elevated,
    "key": openspan.ensure_ssh_key,
}
_startup = {"key_calls": 0, "released": [], "launched": 0}
openspan.is_elevated = lambda: False
openspan._single_instance_lock = lambda: 9876
openspan.ensure_ssh_key = \
    lambda: _startup.update(key_calls=_startup["key_calls"] + 1)
openspan._elevation_gate = lambda: "close"
openspan.run_app()
check("startup gate: Close exits before key/VM/audio setup",
      _startup["key_calls"] == 0)

openspan._elevation_gate = lambda: "restart"
openspan._release_single_instance_lock = \
    lambda lock: _startup["released"].append(lock)
openspan._launch_elevated = \
    lambda: _startup.update(launched=_startup["launched"] + 1) or True
openspan.run_app()
check("startup gate: Restart releases mutex and launches replacement",
      _startup["released"] == [9876] and _startup["launched"] == 1
      and _startup["key_calls"] == 0)
openspan.is_elevated = _startup_originals["is_elevated"]
openspan._single_instance_lock = _startup_originals["single_lock"]
openspan._elevation_gate = _startup_originals["gate"]
openspan._release_single_instance_lock = _startup_originals["release"]
openspan._launch_elevated = _startup_originals["launch"]
openspan.ensure_ssh_key = _startup_originals["key"]


ssh_args = openspan._ssh_argv("echo ok")
check("transport: SSH is strictly non-interactive",
      "BatchMode=yes" in ssh_args
      and "IdentitiesOnly=yes" in ssh_args
      and "NumberOfPasswordPrompts=0" in ssh_args
      and ssh_args[-1] == "echo ok")

_had_frozen = hasattr(openspan.sys, "frozen")
_old_frozen = getattr(openspan.sys, "frozen", None)
openspan.sys.frozen = True
_role_env = openspan._independent_frozen_env()
check("frozen roles: independent one-file environment is requested",
      _role_env.get("PYINSTALLER_RESET_ENVIRONMENT") == "1"
      and _role_env is not os.environ)
if _had_frozen:
    openspan.sys.frozen = _old_frozen
else:
    delattr(openspan.sys, "frozen")
check("plain Python roles: process environment is inherited normally",
      openspan._independent_frozen_env() is None)
# Every device is its own entrance point, so every enabled device needs its own
# NAT lane. Nothing here is keyed to a kind of device: the ports come from the
# config this test builds.
_PORT = openspan.BASE_PORT
_nat_devices = {
    "monitors": [],
    "devices": [
        {"id": "device-1", "name": "Tablet", "port": _PORT,
         "enabled": True, "displays": []},
        {"id": "device-2", "name": "Studio", "port": _PORT + 1,
         "enabled": True, "displays": []},
        {"id": "device-3", "name": "Shelved", "port": _PORT + 2,
         "enabled": False, "displays": []},
    ],
}
_nat_info = (
    f'Forwarding(0)="osp-device-1,tcp,127.0.0.1,{_PORT},,{_PORT}"\n'
    f'Forwarding(1)="osp-device-2,tcp,127.0.0.1,{_PORT + 1},,{_PORT + 1}"\n')
check("each enabled device gets its own daemon lane from the config",
      openspan.target_daemons(_nat_devices) == {
          "device-1": (openspan.DAEMON[0], _PORT),
          "device-2": (openspan.DAEMON[0], _PORT + 1)})
check("one device's forwarding is never mistaken for another's",
      openspan._has_nat_forward(_nat_info, _PORT + 1, _PORT + 1)
      and not openspan._has_nat_forward(_nat_info, _PORT + 1, _PORT)
      and not openspan._has_nat_forward(_nat_info, _PORT + 2, _PORT + 2))
_nat_rules = []          # only natpf rule arguments, so an unrelated
_original_vbox = openspan.vbox   # background VBoxManage call can't skew this
openspan.vbox = lambda *a, **k: (
    _nat_rules.extend(str(x) for x in a if str(x).startswith("osp-"))
    or R(0, "", ""))
check("existing per-device forwards are recognized and left untouched",
      openspan.ensure_device_forwards(_nat_devices, _nat_info) is True
      and not _nat_rules)
_partial_info = (
    f'Forwarding(0)="osp-device-1,tcp,127.0.0.1,{_PORT},,{_PORT}"\n')
_added_ok = openspan.ensure_device_forwards(_nat_devices, _partial_info)
openspan.vbox = _original_vbox
check("a missing device lane is added under its own unique rule name",
      _added_ok is True
      and _nat_rules
      == [f"osp-device-2,tcp,127.0.0.1,{_PORT + 1},,{_PORT + 1}"])
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(_project_root, "create-vm.ps1"),
          encoding="utf-8") as _vm_file:
    _vm_source = _vm_file.read()
check("new VMs pre-expose a second daemon lane",
      f'tcp,,{_PORT + 1},,{_PORT + 1}' in _vm_source)

# Multi-radio UI is opt-in and keeps stable controller identities.
panel = app.bt_panel
_original_save_prefs = openspan.save_bt_prefs
openspan.save_bt_prefs = lambda _prefs: None
# Driven from prefs this test supplies -- whatever this machine happens to have
# saved must not decide the result.
panel.prefs = {
    "renames": {},
    "blacklist": set(),
    "radio_mode": "single",
    "radio_assignments": {},
    "hid_radio": "",
    "scan_radio": "",
    "radio_labels": {},
}
panel.radio_mode.set("Single radio (recommended)")
panel._radios = []
panel._refresh_radio_choices()
check("radio UI: single-radio compatibility is the default",
      str(panel.hid_combo.cget("state")) == "disabled"
      and str(panel.mac_combo.cget("state")) == "disabled"
      and "Single-radio compatibility" in panel.radio_note.get())
panel.prefs = {
    "renames": {},
    "blacklist": set(),
    "radio_mode": "multi",
    "radio_assignments": {
        "AA:BB:CC:00:00:20": "AA:BB:CC:00:00:02"},
    "hid_radio": "AA:BB:CC:00:00:01",
    "scan_radio": "AA:BB:CC:00:00:02",
    "radio_labels": {},
}
panel.radio_mode.set("Multiple radios")
panel._apply_rows(
    [("AA:BB:CC:00:00:20", "Onn", True, True, "audio-card",
      "AA:BB:CC:00:00:02")],
    [{"address": "AA:BB:CC:00:00:01", "hci": "hci0",
      "hardware": "Intel Bluetooth", "alias": "Intel"},
     {"address": "AA:BB:CC:00:00:02", "hci": "hci1",
      "hardware": "TP-Link Bluetooth", "alias": "TP-Link"}])
check("radio UI: all guest controllers are exposed",
      len(panel._radios) == 2
      and len(panel.hid_combo.cget("values")) == 2)
check("radio UI: saved iPad and scan assignments are restored",
      panel._selected_controller(panel.hid_radio) == "AA:BB:CC:00:00:01"
      and panel._selected_controller(panel.scan_radio)
      == "AA:BB:CC:00:00:02")
check("radio UI: device row identifies its assigned controller",
      "TP-Link Bluetooth" in panel.tree.item(
          "AA:BB:CC:00:00:20", "values")[3])
panel.prefs = openspan.load_bt_prefs()
panel.radio_mode.set("Single radio (recommended)")
panel._radios = []
panel._refresh_radio_choices()
openspan.save_bt_prefs = _original_save_prefs


# === 0. the REAL in-frame dialog: overlay (no Toplevel), returns right value =
_dlg_seen = {"scrim": False}


def _find_scrim(w):
    for c in w.winfo_children():
        try:
            if isinstance(c, tk.Frame) \
                    and str(c.cget("bg")) == str(openspan.SCRIM):
                return c
        except tk.TclError:
            pass
        r = _find_scrim(c)
        if r is not None:
            return r
    return None


def _click_dialog(style_exact):
    """Find the in-frame overlay and invoke the button whose style == given."""
    scrim = _find_scrim(root)
    if scrim is None:
        return
    _dlg_seen["scrim"] = True
    found = []

    def walk(w):
        for c in w.winfo_children():
            if isinstance(c, ttk.Button):
                found.append(c)
            walk(c)
    walk(scrim)
    for b in found:
        if str(b.cget("style")) == style_exact:
            b.invoke()
            return
    if found:
        found[0].invoke()


root.deiconify()  # the overlay grabs input; the parent must be viewable
root.after(200, lambda: _click_dialog("Accent.TButton"))   # click Yes
r_yes = openspan.dark_confirm(root, "T", "message body")
check("dialog: Yes returns True", r_yes is True)
check("dialog: rendered in-frame (overlay used, no Toplevel spawned)",
      _dlg_seen["scrim"] is True
      and not any(isinstance(c, tk.Toplevel) for c in root.winfo_children()))
root.after(200, lambda: _click_dialog("TButton"))          # click No
r_no = openspan.dark_confirm(root, "T", "message body")
check("dialog: No returns False", r_no is False)
# 3-button shape (the close dialog): returns the specific clicked value
root.after(200, lambda: _click_dialog("Danger.TButton"))
r_mid = openspan._dialog(root, "Close?", "body",
                         [("Tray", "tray", "TButton"),
                          ("Shut down", "shutdown", "Danger.TButton"),
                          ("Cancel", "cancel", "TButton")])
check("dialog: 3-button returns the clicked value", r_mid == "shutdown")
root.withdraw()

# the flow sections drive the state machine, so mock the dialog from here
openspan.dark_confirm = lambda *a, **k: _confirm["answer"]


# (section 4b removed: 4c below drives the same generic
#  verb across THREE devices, which strictly supersedes it)
_multi_commands = []
# capture the guest commands the generic pair verb issues
openspan.ssh_guest = lambda command, *a, **k: (
    _multi_commands.append(command) or R(0, "", ""))

# === 4c. an Nth device is not an Nth code path ==============================
# Every device pairs through the SAME generic verb, which reads its radio,
# port and advertised name from that device's own record. So this section
# installs three devices of its own making and drives the real verbs.
_PORT = openspan.BASE_PORT
_devices_backup = list(app.canvas.config["devices"])


def _fake_device(ident, name, port, radio):
    return {"id": ident, "name": name, "port": port, "radio": radio,
            "enabled": True, "clipboard": False, "displays": []}


# in place: the canvas holds the SAME list object as the config
app.canvas.config["devices"][:] = [
    _fake_device("device-1", "Tablet", _PORT, "AA:BB:CC:00:00:01"),
    _fake_device("device-2", "Studio", _PORT + 1, "AA:BB:CC:00:00:02"),
    _fake_device("device-3", "Spare", _PORT + 2, ""),
]
_dev_adv = {}
openspan.set_target_advertising = lambda target, on: (
    _dev_adv.update({target: bool(on)}) or True)
openspan.target_daemon_status = lambda target: {
    "kbd_subscribed": False,
    "mouse_subscribed": False,
    "advertising": _dev_adv.get(target, False),
    "advertising_state": "on" if _dev_adv.get(target) else "off",
    "advertising_error": "",
}
_multi_commands.clear()
_original_vm_running_for_device = openspan.vm_running
openspan.vm_running = lambda: True
_studio = app._dev_state("device-2")
_studio["inflight"] = True
_studio["broadcasting"] = False
app._pair_device_worker("device-2")
check("a device pairs on the radio, port and name from its OWN record",
      any(f"set-hid-device.sh device-2 AA:BB:CC:00:00:02 {_PORT + 1} "
          f"'OpenSpan Studio'" in command for command in _multi_commands))
check("device pairing uses controller-scoped HID preparation",
      any("prepare-hid --controller AA:BB:CC:00:00:02 --target device-2"
          in command for command in _multi_commands))
check("pairing one device never touches another device's lane",
      _multi_commands
      and not any("device-1" in command or "device-3" in command
                  for command in _multi_commands))
check("the device advertises through its own daemon",
      _studio["broadcasting"] is True and _dev_adv.get("device-2") is True)

# A lane must be unambiguous: no radio, or a radio another device already
# holds, is refused up front rather than half-paired.
_alerts = []
_original_alert = openspan.dark_alert
openspan.dark_alert = \
    lambda parent, title, message, ok="OK": _alerts.append(title)
app._pair_device("device-3")
check("a device with no radio assigned is refused, never silently paired",
      app._dev_state("device-3")["inflight"] is False
      and any("Assign a radio" in title for title in _alerts))
_alerts.clear()
app.canvas.config["devices"][2]["radio"] = "AA:BB:CC:00:00:02"
app._pair_device("device-3")
check("a radio another device already holds is refused",
      app._dev_state("device-3")["inflight"] is False
      and any("already in use" in title for title in _alerts))
openspan.dark_alert = _original_alert
app.canvas.config["devices"][2]["radio"] = ""

# The panel is generated from the device list: the Nth device needs no new UI.
app._dev_status = {
    device["id"]: {"kbd_subscribed": False, "mouse_subscribed": False}
    for device in app.canvas.config["devices"]
}
# Pair is gated on the VM answering, NOT on that device's daemon already
# listening -- pairing is what creates the lane, so requiring it deadlocked.
app._vm_reachable = True
app._apply_device_rows(False)
drain()
check("the device panel renders one identical row per configured device",
      set(app._dev_rows) == {"device-1", "device-2", "device-3"})
check("a device without a radio says so and cannot be paired",
      "no radio assigned" in app._dev_rows["device-3"]["name"].cget("text")
      and not enabled(app._dev_rows["device-3"]["buttons"]["pair"])
      and enabled(app._dev_rows["device-1"]["buttons"]["pair"]))
check("every row is labelled by name and shows its own lane",
      f":{_PORT + 1}" in app._dev_rows["device-2"]["radio"].cget("text")
      and "Studio" in app._dev_rows["device-2"]["name"].cget("text"))

_studio["inflight"] = False
_studio["broadcasting"] = False
app.broadcasting = False
app._pair_inflight = False
_adv["on"] = False
app._apply_poll(True, fake_daemon_status(), False, True)
drain()
check("the status row summarises N devices in one token",
      app._ind["mac"].cget("text") == f"devices 0/{len(app._dev_rows)}")

# A source-only checkout has no live config to copy, and an empty v3 config is
# intentionally valid. Keep this test independent of the operator's desk by
# supplying the one lane its expiry section needs when the backup is empty.
app.canvas.config["devices"][:] = _devices_backup or [
    _fake_device("expiry-device", "Expiry", _PORT, "AA:BB:CC:00:00:09")]
openspan.vm_running = _original_vm_running_for_device
openspan.ssh_guest = lambda *a, **k: R(0, "", "")

# === 5/6. per-device failure + expiry (the legacy global path is deleted) ===
# A device mid-pair must clear its own flags and hand the earbuds back; an
# abandoned attempt must not leave that device's radio beaconing forever.
_dev = app.canvas.devices()[0]["id"]
_st = app._dev_state(_dev)
_st["inflight"] = True
_st["broadcasting"] = True
_st["started"] = time.time() - 301
app._auto_conn_fails = 3          # prior session hit the 3-fail pause
app._auto_conn_last = 999999.0
_reconnects.clear()
app._apply_poll(True, fake_daemon_status(), False, True)
drain()
check("expiry: that device stops broadcasting", _st["broadcasting"] is False)
check("expiry: that device is no longer in flight", _st["inflight"] is False)
check("expiry: a busy device no longer blocks audio reconnect",
      app._any_device_busy() is False)

_st["inflight"] = True
check("busy: an in-flight device defers the audio auto-reconnect",
      app._any_device_busy() is True)
_st["inflight"] = False


# === the follow-up transport check after "connected ✓" ======================
# The check-mark was true when printed and stopped being true 2.1s later while
# nothing was looking (2026-08-17: btready.sh bounced wireplumber out from
# under a live A2DP stream). _verify_conn is the one plain re-read that catches
# it. Only the guest's answer is mocked; the decision logic is the real one.
_saved_ssh = openspan.ssh_guest


def _answer(token):
    openspan.ssh_guest = lambda *a, **k: R(0, token, "")


app._auto_conn_lost = 0
app._auto_conn_fails = 0
_reconnects.clear()

_answer("LIVE")
app._verify_conn("AA:BB:CC:00:00:09", "Buds")
check("verify: a transport that is still live triggers no retry",
      _reconnects == [])
check("verify: and it clears the teardown counter",
      app._auto_conn_lost == 0)

_answer("GONE")
app._verify_conn("AA:BB:CC:00:00:09", "Buds")
check("verify: a vanished transport re-runs the auto-reconnect path",
      len(_reconnects) == 1)
check("verify: and counts the teardown", app._auto_conn_lost == 1)
check("verify: the retry bypasses the 90s cooldown, which exists to stop us "
      "paging idle buds -- not to stop us repairing a link we watched die",
      app._auto_conn_last == 0.0)

# an unreadable answer is not evidence of a teardown -- claim nothing
_answer("")
app._verify_conn("AA:BB:CC:00:00:09", "Buds")
check("verify: an unreadable answer neither retries nor counts",
      len(_reconnects) == 1 and app._auto_conn_lost == 1)

# the chain is bounded on its own: a successful reconnect resets
# _auto_conn_fails to 0, so without _auto_conn_lost a link that connects and
# dies forever would ping-pong every 7s for the life of the session
_answer("GONE")
app._auto_conn_lost = app._CONN_VERIFY_MAX - 1
app._verify_conn("AA:BB:CC:00:00:09", "Buds")
check("verify: the retry chain stops after _CONN_VERIFY_MAX teardowns",
      len(_reconnects) == 1)

# The session-wide 3-fail pause is NOT asserted here on purpose:
# _auto_reconnect_audio is stubbed at the top of this file, so a call to it
# would only prove the stub appends. The retry goes through that same real
# guard in production; what this section owns is the decision to retry at all.

app._auto_conn_lost = 0
app._auto_conn_fails = 0
_reconnects.clear()
openspan.ssh_guest = _saved_ssh


# --- a new SCREEN belongs to the device you are editing ---------------------
# Adding a screen used to mint "mac-N" whatever device was open -- the last
# hardcoded remnant of the two-device model, in the one dialog used for every
# device. A third device's second screen therefore came out as "mac-2", the
# same id the Managed Mac's own second screen already had.
class _FakeEditor:
    _fresh_display_id = openspan.MacDisplayEditor._fresh_display_id

    def __init__(self, device_id, rows):
        self.device_id = device_id
        self.rows = [{"id": r} for r in rows]


check("a device's FIRST added screen is named after that device",
      _FakeEditor("device-1", []).\
      _fresh_display_id() == "device-1-1")
check("and so is its second -- never 'mac-2'",
      _FakeEditor("device-1", ["device-1-1"]).\
      _fresh_display_id() == "device-1-2")
check("an id already taken on this device is skipped, not reused",
      _FakeEditor("device-1", ["device-1-1", "device-1-2"]).\
      _fresh_display_id() == "device-1-3")
check("a device with no id still gets something, and not a Mac one",
      _FakeEditor(None, []).\
      _fresh_display_id().startswith("display-"))

# and a config that already carries a clash must heal itself on load, because
# one was written before the editor was fixed
_clashed = {"devices": [
    {"id": "mac", "displays": [{"id": "mac-1"}, {"id": "mac-2"}]},
    {"id": "device-1", "displays": [{"id": "device-1-1"}, {"id": "mac-2"}]},
    {"id": "device-2", "displays": [{"id": "mac-2"}, {"id": ""}]},
]}
_renamed = openspan.dedupe_display_ids(_clashed)
_after = [[_s["id"] for _s in _d["displays"]] for _d in _clashed["devices"]]
check("the device that had the id first keeps it",
      _after[0] == ["mac-1", "mac-2"])
check("a clashing id is renamed onto the device it actually belongs to",
      _after[1] == ["device-1-1", "device-1-2"])
check("and so is a third clash, and a blank id",
      _after[2] == ["device-2-1", "device-2-2"])
check("no id is left shared after the migration",
      len({i for row in _after for i in row}) == 6)


# --- the portal must be reloaded whenever what it READS changes -------------
# save() used to snapshot the signature at the top of itself and compare with
# the bottom. But every caller mutates the config and THEN calls save(), so the
# snapshot was already the after: adding a whole device compared equal to
# itself. The portal kept routing for two devices while a third sat there
# paired, healthy and unreachable. The comparison has to be against what the
# portal was last TOLD.
class _FakeCanvas:
    save = openspan.MultiArrangeCanvas.save

    def __init__(self, config):
        self.config = config
        self.reloads = 0
        self._told_portal = openspan.portal_signature(config)
        self.on_change = self._reload

    def _reload(self, _ok):
        self.reloads += 1

    def _persist(self):
        pass

    def _fit_height(self):
        """Same reason as _persist: this fake borrows the real save() and has to
        stub whatever save() reaches for. save() re-fits the canvas height
        because every one of its callers has just changed the shape of the desk
        -- adding a device, deleting one, rotating a screen -- and none of those
        fire a <Configure>. There is no widget here, so there is nothing to
        fit; what is under test on this object is the portal-reload signature."""


def _screen(ident, x, y):
    return {"id": ident, "name": ident, "x": x, "y": y, "w": 1600, "h": 900,
            "res_w": 1920, "res_h": 1080, "rotation": 0, "refresh_hz": 60.0,
            "diagonal_in": 24.0}


def _device(ident, x):
    return {"id": ident, "name": ident, "port": 9955, "radio": "",
            "enabled": True, "clipboard": False, "scroll_invert": False,
            "pointer_gain": 1.0, "pointer_accel": 0.0, "sensitivity": 1.0,
            "compensate_target_accel": False, "modifier_remap": None,
            "displays": [_screen(f"{ident}-1", x, 0)]}


_canvas = _FakeCanvas({
    "monitors": [{"name": r"\.\DISPLAY1", "x": 0, "y": 0, "w": 1920,
                  "h": 1080, "layout_x": 0, "layout_y": 0, "layout_w": 1600,
                  "layout_h": 900, "primary": True}],
    "devices": [_device("dev-a", -1600)],
})
_canvas.save()
check("saving an unchanged config does not bounce the portal",
      _canvas.reloads == 0)

_canvas.config["devices"].append(_device("dev-b", -3200))
_canvas.save()
check("ADDING A DEVICE reloads the portal", _canvas.reloads == 1)

_canvas.save()
check("and saving again with nothing changed does not reload it again",
      _canvas.reloads == 1)

_canvas.config["devices"][0]["displays"][0]["res_w"] = 3840
_canvas.save()
check("changing a resolution reloads the portal", _canvas.reloads == 2)

_canvas.config["devices"][0]["displays"][0]["x"] = -1700
_canvas.save()
check("moving a screen reloads the portal", _canvas.reloads == 3)

_canvas.config["devices"][0]["displays"][0]["name"] = "Renamed"
_canvas.save()
check("but a rename still does not", _canvas.reloads == 3)


# ---- the verdict -----------------------------------------------------------
# This tail is not decoration. Without it the file collected every failure into
# `fails` and then fell off the end, exiting 0 -- so every check in it was
# advisory and the runner could never see one fail. Same two lines as every
# other test file in this directory.
print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
