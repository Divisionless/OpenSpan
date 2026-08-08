"""Focused UI checks for connected-headset device-level controls.

No VM, Bluetooth radio, production preference file, or audio process is used.
Requires Tk, like the existing pane/layout UI tests.
"""

import inspect
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openspan as A  # noqa: E402

import tkinter as tk  # noqa: E402


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


scratch = tempfile.TemporaryDirectory(prefix="openspan-bt-volume-")
A.BT_PREFS = os.path.join(scratch.name, "bt_prefs.json")
A.BtPanel.refresh = lambda self, quiet=False: None
A.BtPanel._radio_usb_check = lambda self: None
A._emit = lambda *_args, **_kwargs: None

root = tk.Tk()
root.withdraw()
panel = A.BtPanel(root, app=None)

headset = "AA:BB:CC:00:00:20"
panel._apply_rows([
    (headset, "Onn Headset", True, True, "audio-card", ""),
    ("AA:BB:CC:00:00:21", "Sleeping Buds", True, False,
     "audio-card", ""),
    ("AA:BB:CC:00:00:22", "Keyboard", True, True,
     "input-keyboard", ""),
], audio_levels={headset: 63})
root.update_idletasks()

check("only a connected audio device gets an inline level control",
      list(panel._audio_controls) == [headset])
control = panel._audio_controls[headset]
check("the connected headset's current sink level is shown",
      int(round(control["variable"].get())) == 63
      and control["value_text"].get() == "63%"
      and "disabled" not in control["scale"].state())
check("one live headset control remains inside the Bluetooth pane budget",
      panel.winfo_reqheight() <= 700)
second_headset = "AA:BB:CC:00:00:21"
panel._apply_rows([
    (headset, "Onn Headset", True, True, "audio-card", ""),
    (second_headset, "Other Buds", True, True, "audio-card", ""),
], audio_levels={headset: 63, second_headset: 71})
root.update_idletasks()
check("two simultaneously connected headsets keep independent controls",
      set(panel._audio_controls) == {headset, second_headset}
      and int(round(panel._audio_controls[second_headset]["variable"].get()))
      == 71)
check("two live headset controls remain inside the Bluetooth pane budget",
      panel.winfo_reqheight() <= 700)
panel._apply_rows([
    (headset, "Onn Headset", True, True, "audio-card", ""),
], audio_levels={headset: 63})
control = panel._audio_controls[headset]
check("mouse and keyboard release are the network commit boundaries",
      bool(control["scale"].bind("<ButtonPress-1>"))
      and bool(control["scale"].bind("<ButtonRelease-1>"))
      and bool(control["scale"].bind("<KeyRelease>")))
panel._sync_audio_controls([(headset, "Onn Headset")], {})
check("a failed or ambiguous level read disables a stale control",
      control["value_text"].get() == "--"
      and "disabled" in control["scale"].state())
panel._sync_audio_controls([(headset, "Onn Headset")], {headset: 63})

calls = []


def fake_ssh(command, **kwargs):
    calls.append((command, kwargs))
    return types.SimpleNamespace(returncode=0, stdout="", stderr="")


class ImmediateThread:
    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


A.ssh_guest = fake_ssh
A.threading.Thread = ImmediateThread
panel._begin_audio_level_drag(headset)
control["variable"].set(37.4)
panel._preview_audio_level(headset, 37.4)
panel._sync_audio_controls([(headset, "Onn Headset")], {headset: 63})
check("drag preview changes the label without issuing SSH",
      control["value_text"].get() == "37%" and calls == [])

panel._release_audio_level_drag(headset)
check("release sends one exact per-device integer level",
      len(calls) == 1
      and calls[0][0] == (
          "python3 /opt/openspan/openspan_bt.py set-audio-level "
          f"--device {headset} --level 37"))
check("device-level commit does not write or replace global gain",
      "GAIN_FILE" not in inspect.getsource(A.BtPanel._commit_audio_level)
      and "c_vol_var" not in inspect.getsource(A.BtPanel._commit_audio_level))

calls.clear()
control["variable"].set(42)
panel._release_audio_level_key(
    types.SimpleNamespace(keysym="Tab"), headset)
check("non-volume key release performs no write", calls == [])
panel._release_audio_level_key(
    types.SimpleNamespace(keysym="Left"), headset)
check("volume key release commits the current device level",
      len(calls) == 1 and calls[0][0].endswith("--level 42"))


class DeferredThread:
    started = []

    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.started.append(self.target)


A.threading.Thread = DeferredThread
calls.clear()
control["variable"].set(51)
panel._commit_audio_level(headset)
control["variable"].set(54)
panel._commit_audio_level(headset)
check("two releases queue one worker and retain only the latest value",
      len(DeferredThread.started) == 1
      and panel._audio_level_pending[headset] == 54
      and calls == [])
DeferredThread.started.pop(0)()
check("the queued worker writes the latest release and clears busy state",
      len(calls) == 1 and calls[0][0].endswith("--level 54")
      and headset not in panel._audio_level_workers
      and headset not in panel._audio_level_pending)

A.threading.Thread = ImmediateThread
refreshes = []
panel.app = types.SimpleNamespace(ui=lambda fn: fn())
panel.refresh = lambda quiet=False: refreshes.append(quiet)
A.ssh_guest = lambda *_args, **_kwargs: types.SimpleNamespace(
    returncode=1, stdout="", stderr="sink disappeared")
control["variable"].set(58)
panel._commit_audio_level(headset)
check("a rejected write clears busy state and requests reconciliation",
      refreshes == [True]
      and headset not in panel._audio_level_workers
      and headset not in panel._audio_level_pending)
panel.app = None

panel._apply_rows([
    (headset, "Onn Headset", True, False, "audio-card", ""),
], audio_levels={})
check("disconnecting a headset removes its inline control",
      panel._audio_controls == {})

root.destroy()
scratch.cleanup()
print("RESULT: ALL PASS")
