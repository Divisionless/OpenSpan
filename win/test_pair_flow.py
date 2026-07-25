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


def btn():
    return app.pair_btn.cget("text")


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
disconnect_source = inspect.getsource(openspan.App._disconnect_ipad)
unpair_source = inspect.getsource(openspan.App._unpair_ipad)
mac_unpair_source = inspect.getsource(openspan.App._unpair_mac)
check("iPad disconnect leaves the shared Mac router running",
      "_stop_portal_if_running" not in disconnect_source)
check("iPad unpair leaves the shared Mac router running",
      "_stop_portal_if_running" not in unpair_source)
check("iPad and Mac unpair commands are target-scoped",
      "--target ipad" in unpair_source
      and "--target mac" in mac_unpair_source)


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
_nat_info = (
    'Forwarding(0)="hid,tcp,127.0.0.1,9955,,9955"\n'
    'Forwarding(1)="mac-hid,tcp,127.0.0.1,9956,,9956"\n')
check("managed Mac NAT forwarding is recognized",
      openspan._has_nat_forward(_nat_info, 9956, 9956))
check("iPad forwarding is not mistaken for the Mac lane",
      not openspan._has_nat_forward(_nat_info, 9956, 9955))
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(_project_root, "create-vm.ps1"),
          encoding="utf-8") as _vm_file:
    _vm_source = _vm_file.read()
check("new VMs expose the managed Mac daemon",
      'mac-hid,tcp,,9956,,9956' in _vm_source)

# Multi-radio UI is opt-in and keeps stable controller identities.
panel = app.bt_panel
check("radio UI: single-radio compatibility is the default",
      panel.radio_mode.get() == "Single radio (recommended)"
      and str(panel.hid_combo.cget("state")) == "disabled")
_original_save_prefs = openspan.save_bt_prefs
openspan.save_bt_prefs = lambda _prefs: None
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


# === 1. confirm = No -> nothing happens ====================================
_confirm["answer"] = False
app._pair_inflight = False
app.broadcasting = False
before = btn()
app.pair()
drain()
check("cancel: _pair_inflight stays False", app._pair_inflight is False)
check("cancel: not broadcasting", app.broadcasting is False)
check("cancel: button unchanged", btn() == before)

# === 2. confirm = Yes -> worker runs, broadcasting=True =====================
_confirm["answer"] = True
app.pair()
t0 = time.time()
while time.time() - t0 < 10:
    drain()
    if app.broadcasting:
        break
    time.sleep(0.1)
check("pair: broadcasting=True after worker", app.broadcasting is True)
app._apply_poll(True, fake_daemon_status(), False, True)
drain()
check("pair: Pair disabled while broadcasting", not enabled(app.pair_btn))
check("pair: broadcast indicator is green",
      str(app._ind["bcast"].cget("fg")) == str(openspan.ACCENT))

# === 3. connect edge -> portal auto-start + settle + forced reconnect =======
app.broadcasting = True
app._pair_inflight = True
app.portal_proc = None            # portal currently OFF
app._auto_conn_last = 999999.0    # pretend a recent reconnect (cooldown active)
app._auto_conn_fails = 3          # pretend backed off
_reconnects.clear()
app._apply_poll(
    True,
    {"kbd_subscribed": True, "mouse_subscribed": True,
     "advertising": True, "advertising_state": "on",
     "advertising_error": ""},
    False, True)
drain()
check("connect: broadcasting cleared", app.broadcasting is False)
check("connect: _pair_inflight cleared", app._pair_inflight is False)
check("connect: portal auto-started (proc live)",
      app.portal_proc is not None and app.portal_proc.poll() is None)
check("connect: Pair and Connect disabled",
      not enabled(app.pair_btn) and not enabled(app.conn_btn))
check("connect: Disconnect enabled", enabled(app._disc_btn))
check("connect: cooldown reset to 0", app._auto_conn_last == 0.0)
check("connect: fails reset to 0", app._auto_conn_fails == 0)
check("connect: reconnect was invoked", len(_reconnects) == 1)
# the portal indicators must reflect the just-started portal THIS tick
check("connect: portal_btn says 'Stop portal'",
      app.portal_btn.cget("text") == "Stop portal")
check("connect: portal dot is green (ACCENT)",
      str(app.c_stat["portal"].cget("fg")) == str(openspan.ACCENT))
check("connect: portal indicator says ON",
      "ON" in app._ind["portal"].cget("text"))

# === 3b. connect edge when portal ALREADY on -> don't double-start ==========
app.broadcasting = True
existing = app.portal_proc
_reconnects.clear()
app._apply_poll(
    True,
    {"kbd_subscribed": True, "mouse_subscribed": True,
     "advertising": False, "advertising_state": "off",
     "advertising_error": ""},
    True, True)
drain()
check("connect(portal on): same portal proc (no restart)",
      app.portal_proc is existing)

# === 4. reconciler: button truthful vs. connected when idle =================
app.broadcasting = False
app._pair_inflight = False
app.pair_btn.state(["!disabled"])
app._apply_poll(
    True,
    {"kbd_subscribed": True, "mouse_subscribed": True,
     "advertising": False, "advertising_state": "off",
     "advertising_error": ""},
    True, True)
drain()
check("reconcile: connected -> Pair disabled", not enabled(app.pair_btn))
app._apply_poll(
    True,
    {"kbd_subscribed": False, "mouse_subscribed": False,
     "advertising": False, "advertising_state": "off",
     "advertising_error": ""},
    True, True)
drain()
check("reconcile: disconnected -> Pair enabled", enabled(app.pair_btn))
# reconciler must NOT stomp a transient state (broadcasting)
app.broadcasting = True
app._apply_poll(
    True,
    {"kbd_subscribed": False, "mouse_subscribed": False,
     "advertising": True, "advertising_state": "on",
     "advertising_error": ""},
    True, True)
drain()
check("reconcile: Pair stays disabled while broadcasting",
      not enabled(app.pair_btn))
app.broadcasting = False

# === 4b. multi-radio pair stays controller-scoped ===========================
_multi_commands = []
_original_load_prefs = openspan.load_bt_prefs
openspan.load_bt_prefs = lambda: {
    "renames": {},
    "blacklist": set(),
    "radio_mode": "multi",
    "radio_assignments": {},
    "hid_radio": "AA:BB:CC:00:00:01",
    "scan_radio": "AA:BB:CC:00:00:01",
    "radio_labels": {},
}
openspan.ssh_guest = lambda command, *a, **k: (
    _multi_commands.append(command) or R(0, "", ""))
app._pair_inflight = True
app.broadcasting = False
_adv["on"] = False
app._pair_worker(False)
check("multi-radio pair selects the saved HID controller",
      any("set-hid-radio.sh AA:BB:CC:00:00:01" in command
          for command in _multi_commands))
check("multi-radio pair uses controller-scoped preparation",
      any("openspan_bt.py prepare-hid "
          "--controller AA:BB:CC:00:00:01 --target ipad" in command
          for command in _multi_commands))
check("multi-radio pair does not use the legacy global disconnect",
      not any("bluetoothctl disconnect" in command
              for command in _multi_commands))

# The managed Mac uses a second daemon/port and a distinct controller. It must
# never reuse the iPad lane or fall through to the single-radio shell path.
_mac_adv = {"on": False}
openspan.load_bt_prefs = lambda: {
    "renames": {},
    "blacklist": set(),
    "radio_mode": "multi",
    "radio_assignments": {},
    "hid_radio": "AA:BB:CC:00:00:01",
    "mac_radio": "AA:BB:CC:00:00:02",
    "scan_radio": "AA:BB:CC:00:00:03",
    "radio_labels": {},
}
openspan.set_target_advertising = \
    lambda target, on: (
        _mac_adv.update(on=bool(on)) or target == "mac")
openspan.target_daemon_status = lambda target: {
    "kbd_subscribed": False,
    "mouse_subscribed": False,
    "advertising": _mac_adv["on"],
    "advertising_state": "on" if _mac_adv["on"] else "off",
    "advertising_error": "",
} if target == "mac" else fake_daemon_status()
_multi_commands.clear()
_original_vm_running_for_mac = openspan.vm_running
openspan.vm_running = lambda: True
app._mac_pair_inflight = True
app.mac_broadcasting = False
app._pair_mac_worker(False)
check("managed Mac pair selects its independent controller",
      any("set-hid-target.sh mac AA:BB:CC:00:00:02" in command
          for command in _multi_commands))
check("managed Mac pair uses controller-scoped HID preparation",
      any("prepare-hid --controller AA:BB:CC:00:00:02 --target mac" in command
          for command in _multi_commands))
check("managed Mac advertises through the second daemon",
      app.mac_broadcasting is True and _mac_adv["on"] is True)
app._mac_pair_inflight = False
app.mac_broadcasting = False
_mac_adv["on"] = False
openspan.vm_running = _original_vm_running_for_mac
openspan.load_bt_prefs = _original_load_prefs
openspan.ssh_guest = lambda *a, **k: R(0, "", "")
app.broadcasting = False
app._pair_inflight = False
_adv["on"] = False

# === 5. failure path -> reconnect audio, button reset =======================
_confirm["answer"] = True
openspan.ssh_guest = lambda *a, **k: R(1, "", "boom")  # guest work fails
app.broadcasting = False
app._pair_inflight = False
_reconnects.clear()
app.pair()
t0 = time.time()
while time.time() - t0 < 10:
    drain()
    if _reconnects:
        break
    time.sleep(0.1)
check("fail: not broadcasting", app.broadcasting is False)
check("fail: _pair_inflight cleared", app._pair_inflight is False)
check("fail: audio reconnect invoked",
      any("restoring" in r for r in _reconnects))
app._apply_poll(True, fake_daemon_status(), False, True)
drain()
check("fail: Pair re-enabled", enabled(app.pair_btn))
openspan.ssh_guest = lambda *a, **k: R(0, "", "")  # restore for later

# === 6. expiry path -> reconnect audio, fails reset =========================
app.broadcasting = True
app._pair_inflight = True
app._broadcast_started = time.time() - 301
app._auto_conn_fails = 3          # prior session hit the 3-fail pause
app._auto_conn_last = 999999.0
_reconnects.clear()
app._apply_poll(True, fake_daemon_status(), False, True)
drain()
check("expiry: broadcasting cleared", app.broadcasting is False)
check("expiry: audio reconnect invoked",
      any("expired" in r for r in _reconnects))
check("expiry: fails reset to 0", app._auto_conn_fails == 0)
check("expiry: cooldown reset to 0", app._auto_conn_last == 0.0)

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
root.destroy()
sys.exit(1 if fails else 0)
