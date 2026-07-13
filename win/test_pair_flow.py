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
# non-None so _pair_worker's "wait for daemon" loop breaks immediately
openspan.daemon_status = lambda *a, **k: {"kbd_subscribed": False}
openspan.ssh_guest = lambda *a, **k: R(0, "", "")
openspan.vbox = lambda *a, **k: R(0, "", "")
openspan.current_mode = lambda *a, **k: "windows"
openspan.boot_task_exists = lambda *a, **k: True
openspan.ensure_boot_task = lambda *a, **k: None
openspan.ClipboardServer.start = lambda self: False
openspan.App._ensure_audio = lambda self: None
openspan.App._sync_guest_scripts = lambda self: None
openspan.App._tick = lambda self: None
openspan.App._volume_thread = lambda self: None


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


fails = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


# === 0. the REAL dark confirm dialog: themed, modal, returns the right bool ==
_dlg_bg = {"v": ""}


def _click_dialog(style_exact):
    """Find the open dialog and invoke the button whose ttk style == given."""
    for tl in root.winfo_children():
        if not isinstance(tl, tk.Toplevel):
            continue
        found = []

        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, ttk.Button):
                    found.append(c)
                walk(c)
        walk(tl)
        _dlg_bg["v"] = str(tl.cget("bg"))
        for b in found:
            if str(b.cget("style")) == style_exact:
                b.invoke()
                return
        if found:
            found[0].invoke()
        return


root.deiconify()  # a modal dialog needs a viewable parent for grab_set
root.after(200, lambda: _click_dialog("Accent.TButton"))   # click Yes
r_yes = openspan.dark_confirm(root, "T", "message body")
check("dialog: Yes returns True", r_yes is True)
check("dialog: background is themed (BG)", _dlg_bg["v"] == openspan.BG)
root.after(200, lambda: _click_dialog("TButton"))          # click No
r_no = openspan.dark_confirm(root, "T", "message body")
check("dialog: No returns False", r_no is False)
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
check("pair: button not disabled after ok()",
      "disabled" not in app.pair_btn.state())
check("pair: button shows Broadcasting", "Broadcast" in btn())

# === 3. connect edge -> portal auto-start + settle + forced reconnect =======
app.broadcasting = True
app._pair_inflight = True
app.portal_proc = None            # portal currently OFF
app._auto_conn_last = 999999.0    # pretend a recent reconnect (cooldown active)
app._auto_conn_fails = 3          # pretend backed off
_reconnects.clear()
app._apply_poll(True, {"kbd_subscribed": True}, False, True)
drain()
check("connect: broadcasting cleared", app.broadcasting is False)
check("connect: _pair_inflight cleared", app._pair_inflight is False)
check("connect: portal auto-started (proc live)",
      app.portal_proc is not None and app.portal_proc.poll() is None)
check("connect: button settled to paired-check", "paired" in btn())
check("connect: cooldown reset to 0", app._auto_conn_last == 0.0)
check("connect: fails reset to 0", app._auto_conn_fails == 0)
check("connect: reconnect was invoked", len(_reconnects) == 1)
# the portal indicators must reflect the just-started portal THIS tick
check("connect: portal_btn says 'Stop portal'",
      app.portal_btn.cget("text") == "Stop portal")
check("connect: portal dot is green (ACCENT)",
      str(app.c_stat["portal"].cget("fg")) == str(openspan.ACCENT))
check("connect: status line shows 'portal ● ON'",
      "portal ● ON" in app.status.get())

# === 3b. connect edge when portal ALREADY on -> don't double-start ==========
app.broadcasting = True
existing = app.portal_proc
_reconnects.clear()
app._apply_poll(True, {"kbd_subscribed": True}, True, True)
drain()
check("connect(portal on): same portal proc (no restart)",
      app.portal_proc is existing)

# === 4. reconciler: button truthful vs. connected when idle =================
app.broadcasting = False
app._pair_inflight = False
app.pair_btn.state(["!disabled"])
app._apply_poll(True, {"kbd_subscribed": True}, True, True)
drain()
check("reconcile: connected -> paired check", "paired" in btn())
app._apply_poll(True, {"kbd_subscribed": False}, True, True)
drain()
check("reconcile: disconnected -> Pair / Broadcast",
      "Pair / Broadcast" in btn())
# reconciler must NOT stomp a transient state (broadcasting)
app.broadcasting = True
app.pair_btn.config(text="📡  Broadcasting…")
app._apply_poll(True, {"kbd_subscribed": False}, True, True)
drain()
check("reconcile: skipped while broadcasting", "Broadcast" in btn())
app.broadcasting = False

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
check("fail: button re-enabled", "disabled" not in app.pair_btn.state())
openspan.ssh_guest = lambda *a, **k: R(0, "", "")  # restore for later

# === 6. expiry path -> reconnect audio, fails reset =========================
app.broadcasting = True
app._pair_inflight = True
app._broadcast_started = time.time() - 301
app._auto_conn_fails = 3          # prior session hit the 3-fail pause
app._auto_conn_last = 999999.0
_reconnects.clear()
app._apply_poll(True, {"kbd_subscribed": False}, False, True)
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
