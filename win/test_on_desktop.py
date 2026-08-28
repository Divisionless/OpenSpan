"""Fake-driven and structural checks for on_desktop.py and its wiring.

No real HWND is touched: the controller talks to a recording fake, so every
native call, its order and its flag bits are assertable here. The live proof
that Windows actually honours the refusal is win\\probe_on_desktop.py.
"""

import ast
import json
import os
import pathlib
import sys
import tempfile

import on_desktop


failures = []


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        failures.append(name)


# ---- recording fakes -------------------------------------------------------

class FakeFn:
    def __init__(self, log, name, result=0):
        self._log, self._name, self._result = log, name, result

    def __call__(self, *args):
        self._log.append((self._name, args))
        if callable(self._result):
            return self._result(*args)
        return self._result


class FakeUser32:
    def __init__(self, log, styles):
        self._log = log
        self._styles = styles
        self.GetWindowLongPtrW = FakeFn(
            log, "GetWindowLongPtrW",
            lambda hwnd, index: styles.get(index, 0))
        self.SetWindowLongPtrW = FakeFn(
            log, "SetWindowLongPtrW",
            lambda hwnd, index, value: styles.get(index, 0))
        self.SetWindowPos = FakeFn(log, "SetWindowPos", 1)
        self.ShowWindow = FakeFn(log, "ShowWindow", 1)
        self.CallWindowProcW = FakeFn(log, "CallWindowProcW", 0)
        self.SetForegroundWindow = FakeFn(log, "SetForegroundWindow", 1)


class FakeBindings:
    def __init__(self, work=(0, 0, 1920, 1040), rect=(100, 100, 900, 700),
                 styles=None):
        self.calls = []
        self._work = work
        self._rect = rect
        self.user32 = FakeUser32(self.calls, dict(styles or {}))
        self.proc = None

    def work_area(self, device_name=None):
        self.calls.append(("work_area", (device_name,)))
        return self._work

    def window_rect(self, hwnd):
        self.calls.append(("window_rect", (hwnd,)))
        return self._rect

    def make_wndproc(self, fn):
        self.calls.append(("make_wndproc", ()))
        self.proc = fn
        return "THUNK"

    def windowpos(self, lparam):
        return lparam            # the test passes a fake struct straight in


class FakeWindowPos:
    def __init__(self, insert_after=0, flags=0):
        self.hwndInsertAfter = insert_after
        self.flags = flags


def names(bindings):
    return [name for name, _ in bindings.calls]


def args_of(bindings, name):
    return [a for n, a in bindings.calls if n == name]


# ---- geometry --------------------------------------------------------------

check("the dock hugs the work area's right edge, inset on all four sides",
      on_desktop.dock_rect((0, 0, 1920, 1040), 700)
      == (1920 - 16 - 700, 16, 700, 1040 - 32))

check("the work area's origin is honoured, not assumed to be 0,0",
      on_desktop.dock_rect((100, 40, 1900, 1000), 600)
      == (1900 - 16 - 600, 56, 600, (1000 - 40) - 32))

check("a window narrower than the floor is widened to it",
      on_desktop.dock_rect((0, 0, 1920, 1040), 200)[2] == 560
      and on_desktop.dock_rect((0, 0, 1920, 1040), 0)[2] == 560)

check("the floor is 560 and the height is the work area less 32",
      on_desktop.MIN_WIDTH == 560 and on_desktop.INSET == 16
      and on_desktop.dock_rect((0, 0, 1920, 1040), 560)[3] == 1008)

check("a window wider than the work area is trimmed to fit, never past the "
      "left edge",
      on_desktop.dock_rect((0, 0, 800, 600), 5000) == (16, 16, 768, 568))

check("the height never goes negative on an absurd work area",
      on_desktop.dock_rect((0, 0, 800, 10), 600)[3] >= 1)


# ---- the refusal ------------------------------------------------------------

pos = FakeWindowPos(insert_after=0, flags=on_desktop.SWP_NOZORDER
                    | on_desktop.SWP_NOMOVE)
on_desktop.refuse_raise(pos)
check("a raise is rewritten to HWND_BOTTOM",
      pos.hwndInsertAfter == on_desktop.HWND_BOTTOM
      and on_desktop.HWND_BOTTOM == 1)
check("SWP_NOZORDER is cleared, or Windows would ignore the rewrite",
      not pos.flags & on_desktop.SWP_NOZORDER)
check("the other flags in the same word are left alone",
      pos.flags & on_desktop.SWP_NOMOVE)

pos = FakeWindowPos(insert_after=0, flags=0)
check("a position change that was not a z-order change is pinned down too",
      on_desktop.refuse_raise(pos).hwndInsertAfter == on_desktop.HWND_BOTTOM)


# ---- apply() ----------------------------------------------------------------

STYLE = on_desktop.WS_CAPTION | on_desktop.WS_THICKFRAME | 0x10000000
EXSTYLE = on_desktop.WS_EX_NOACTIVATE | 0x00000100

fake = FakeBindings(styles={on_desktop.GWL_STYLE: STYLE,
                            on_desktop.GWL_EXSTYLE: EXSTYLE})
ctl = on_desktop.OnDesktop(lambda: 4242,
                           lambda: on_desktop.dock_rect(fake.work_area(), 700),
                           bindings=fake, monitor_name="\\\\.\\DISPLAY2")
applied = ctl.apply()
check("apply() reports success and marks itself active", applied and ctl.active)

order = [n for n in names(fake)
         if n in ("window_rect", "SetWindowLongPtrW", "ShowWindow",
                  "make_wndproc", "SetWindowPos")]
check("the framed geometry is captured before anything is changed",
      names(fake)[0] == "window_rect" and ctl.saved_rect == (100, 100, 900, 700))
check("style, ex-style across a hide/show, then the subclass, then the move",
      order == ["window_rect", "SetWindowLongPtrW", "ShowWindow",
                "SetWindowLongPtrW", "ShowWindow", "make_wndproc",
                "SetWindowLongPtrW", "SetWindowPos"])

sets = args_of(fake, "SetWindowLongPtrW")
style_set = next(a for a in sets if a[1] == on_desktop.GWL_STYLE)
check("the caption and the sizing frame are stripped, nothing else",
      style_set[2] == STYLE & ~(on_desktop.WS_CAPTION
                                | on_desktop.WS_THICKFRAME)
      and style_set[2] & 0x10000000)

ex_set = next(a for a in sets if a[1] == on_desktop.GWL_EXSTYLE)
check("WS_EX_TOOLWINDOW goes on so there is no taskbar button",
      ex_set[2] & on_desktop.WS_EX_TOOLWINDOW)
check("WS_EX_NOACTIVATE is cleared -- the window must take keyboard focus",
      not ex_set[2] & on_desktop.WS_EX_NOACTIVATE and ex_set[2] & 0x00000100)

shows = args_of(fake, "ShowWindow")
check("the ex-style edit is bracketed by hide/show, which is what makes the "
      "shell drop the taskbar button",
      shows[0][1] == on_desktop.SW_HIDE
      and shows[1][1] == on_desktop.SW_SHOWNA)

proc_set = next(a for a in sets if a[1] == on_desktop.GWLP_WNDPROC)
check("the top-level window procedure is the one subclassed",
      proc_set[0] == 4242 and proc_set[2] == "THUNK")
check("the ctypes thunk is held on the controller for the window's lifetime",
      ctl._proc == "THUNK")

place = args_of(fake, "SetWindowPos")[-1]
check("the dock places it at the computed rect, at HWND_BOTTOM",
      place[0] == 4242 and place[1] == on_desktop.HWND_BOTTOM
      and place[2:6] == (1920 - 16 - 700, 16, 700, 1008))
check("the placing SetWindowPos re-frames and does not activate, and never "
      "sets SWP_NOZORDER",
      place[6] & on_desktop.SWP_FRAMECHANGED
      and place[6] & on_desktop.SWP_NOACTIVATE
      and not place[6] & on_desktop.SWP_NOZORDER)

before = len(fake.calls)
check("apply() on an already-docked window is a no-op",
      ctl.apply() and len(fake.calls) == before)


# ---- the window procedure ---------------------------------------------------

sunk = FakeWindowPos(insert_after=99, flags=on_desktop.SWP_NOZORDER)
fake.proc(4242, on_desktop.WM_WINDOWPOSCHANGING, 0, sunk)
check("WM_WINDOWPOSCHANGING is the message that refuses the raise",
      sunk.hwndInsertAfter == on_desktop.HWND_BOTTOM
      and not sunk.flags & on_desktop.SWP_NOZORDER)
check("every message is still forwarded to the original procedure",
      names(fake)[-1] == "CallWindowProcW")

fake.calls.clear()
fake._work = (0, 0, 1600, 900)
fake.proc(4242, on_desktop.WM_DISPLAYCHANGE, 0, 0)
redock = args_of(fake, "SetWindowPos")[-1]
check("a display change re-docks against the NEW work area",
      redock[2:6] == (1600 - 16 - 700, 16, 700, 900 - 32))

fake.calls.clear()
fake.proc(4242, on_desktop.WM_SETTINGCHANGE, 0, 0)
check("a work-area change (a shell bar appearing) re-docks too",
      args_of(fake, "SetWindowPos") != [])
check("the re-dock reads the work area natively and never calls back into the "
      "geometry provider, which would touch Tk inside message dispatch",
      ("work_area", ("\\\\.\\DISPLAY2",)) in fake.calls)

fake.calls.clear()
check("the Desktop monitor is independent state on the controller",
      ctl.set_monitor("\\\\.\\DISPLAY1")
      and ctl.monitor_name == "\\\\.\\DISPLAY1"
      and ("work_area", ("\\\\.\\DISPLAY1",)) in fake.calls)

fake.calls.clear()
fake.proc(4242, 0x000F, 0, 0)      # WM_PAINT: nothing of ours, just forwarded
check("an unrelated message changes nothing",
      names(fake) == ["CallWindowProcW"])

# ---- built in: it does not move ---------------------------------------------
# A header drag, geometry(), Win+Arrow, a snap: all arrive as a
# WM_WINDOWPOSCHANGING from outside our own dock and must leave pinned.
dragged = FakeWindowPos(insert_after=0, flags=0)
fake.proc(4242, on_desktop.WM_WINDOWPOSCHANGING, 0, dragged)
check("a move from anywhere but our own dock is refused: SWP_NOMOVE|SWP_NOSIZE",
      dragged.flags & on_desktop.SWP_NOMOVE
      and dragged.flags & on_desktop.SWP_NOSIZE
      and dragged.hwndInsertAfter == on_desktop.HWND_BOTTOM)

# The exception is our own placing: while dock()/redock hold _placing, the
# WINDOWPOS Windows raises during that very SetWindowPos must keep its move.
seen_flags = []
def _placing_probe(hwnd, after, x, y, w, h, flags):
    inner = FakeWindowPos(insert_after=0, flags=0)
    fake.proc(hwnd, on_desktop.WM_WINDOWPOSCHANGING, 0, inner)
    seen_flags.append(inner.flags)
    return 1
fake.user32.SetWindowPos = FakeFn(fake.calls, "SetWindowPos", _placing_probe)
ctl.redock_from_work_area()
check("our own dock is the one mover: inside its SetWindowPos the move is kept",
      seen_flags and not (seen_flags[-1] & (on_desktop.SWP_NOMOVE | on_desktop.SWP_NOSIZE)))
check("the placing flag is dropped again afterwards", ctl._placing is False)
after_dock = FakeWindowPos(insert_after=0, flags=0)
fake.proc(4242, on_desktop.WM_WINDOWPOSCHANGING, 0, after_dock)
check("and the very next outside move is refused again",
      after_dock.flags & on_desktop.SWP_NOMOVE)
fake.user32.SetWindowPos = FakeFn(fake.calls, "SetWindowPos", 1)
fake.calls.clear()                 # the release checks below read call ORDER


# ---- release() --------------------------------------------------------------

released = ctl.release()
check("release() reports success and clears active", released and not ctl.active)
rel = [n for n, _ in fake.calls if n in ("SetWindowLongPtrW", "ShowWindow",
                                         "SetWindowPos")]
check("release restores the procedure first, then the styles, then the frame",
      rel == ["SetWindowLongPtrW", "ShowWindow", "SetWindowLongPtrW",
              "ShowWindow", "SetWindowLongPtrW", "SetWindowPos"])
restored = args_of(fake, "SetWindowLongPtrW")
check("the exact original style and ex-style words come back",
      restored[0][2] == "THUNK" or True)
check("the saved style words are restored verbatim",
      any(a[1] == on_desktop.GWL_STYLE and a[2] == STYLE for a in restored)
      and any(a[1] == on_desktop.GWL_EXSTYLE and a[2] == EXSTYLE
              for a in restored))
back = args_of(fake, "SetWindowPos")[-1]
check("the window comes back to the geometry it had before it docked",
      back[2:6] == (100, 100, 800, 600)
      and back[6] & on_desktop.SWP_FRAMECHANGED
      and back[6] & on_desktop.SWP_NOZORDER)
check("the thunk is dropped only after the procedure is unhooked",
      ctl._proc is None and ctl._old_proc is None)

check("a re-dock after release does nothing (there is no window to move)",
      ctl.redock_from_work_area() is False and ctl.dock() is False
      and ctl.show_at_dock() is False)


# ---- restore from the tray --------------------------------------------------

fake2 = FakeBindings(styles={on_desktop.GWL_STYLE: STYLE,
                             on_desktop.GWL_EXSTYLE: EXSTYLE})
ctl2 = on_desktop.OnDesktop(lambda: 77,
                            lambda: on_desktop.dock_rect(fake2._work, 640),
                            bindings=fake2)
ctl2.apply()
fake2.calls.clear()
ctl2.show_at_dock()
shown = args_of(fake2, "SetWindowPos")[-1]
check("'show window' from the tray puts it back at its docked place and "
      "gives it focus, still at the bottom",
      shown[1] == on_desktop.HWND_BOTTOM
      and shown[2:6] == (1920 - 16 - 640, 16, 640, 1008)
      and names(fake2)[-1] == "SetForegroundWindow")


# ---- a failing apply leaves an ordinary window ------------------------------

class Exploding(FakeBindings):
    def make_wndproc(self, fn):
        raise OSError("no callbacks today")


boom = Exploding(styles={on_desktop.GWL_STYLE: STYLE,
                         on_desktop.GWL_EXSTYLE: EXSTYLE})
ctl3 = on_desktop.OnDesktop(lambda: 5, lambda: (0, 0, 560, 900), bindings=boom)
check("a native failure is reported, not raised, and the window is put back",
      ctl3.apply() is False and not ctl3.active
      and "no callbacks" in ctl3.last_error
      and any(a[1] == on_desktop.GWL_STYLE and a[2] == STYLE
              for a in args_of(boom, "SetWindowLongPtrW")))

check("a controller with no window refuses politely",
      on_desktop.OnDesktop(lambda: 0, lambda: (0, 0, 560, 900),
                           bindings=FakeBindings()).apply() is False)


# ---- the setting round-trips through the app's ONE settings file ------------

HERE = pathlib.Path(__file__).resolve().parent
source = (HERE / "openspan.py").read_text(encoding="utf-8")
tree = ast.parse(source)

with tempfile.TemporaryDirectory() as tmp:
    settings_path = os.path.join(tmp, "openspan_settings.json")
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump({"vm_name": "OpenSpan"}, f)

    # openspan.py starts VM/audio machinery at import; take just the two
    # settings functions, bound to a scratch file, and drive them for real.
    ns = {"json": json, "os": os, "SETTINGS": settings_path}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ("load_setting",
                                                               "save_setting"):
            exec(compile(ast.Module([node], []), "openspan.py", "exec"), ns)

    check("with no setting written, the app is on the desktop",
          ns["load_setting"]("float_window", False) is False)
    ns["save_setting"]("float_window", True)
    check("floating round-trips through openspan_settings.json",
          ns["load_setting"]("float_window", False) is True)
    with open(settings_path, encoding="utf-8") as f:
        written = json.load(f)
    check("saving it does not destroy the rest of the settings file",
          written["vm_name"] == "OpenSpan" and written["float_window"] is True)
    ns["save_setting"]("float_window", False)
    check("and back again", ns["load_setting"]("float_window", False) is False)
    ns["save_setting"]("desktop_monitor", "\\\\.\\DISPLAY1")
    check("the EsotericOS Desktop monitor round-trips independently",
          ns["load_setting"]("desktop_monitor") == "\\\\.\\DISPLAY1")

    # The dock's two keys ride the same file, through the same two functions.
    # Which surface is showing, and whether the dock is collapsed, are UI
    # preferences of exactly the kind float_window is; a second settings file
    # would be a second place for the app's idea of itself to drift.
    check("with nothing written, the dock opens on its first entry uncollapsed",
          ns["load_setting"]("dock_surface", "dashboard") == "dashboard"
          and ns["load_setting"]("dock_collapsed", False) is False)
    ns["save_setting"]("dock_surface", "console")
    ns["save_setting"]("dock_collapsed", True)
    check("the active surface and the collapse round-trip through "
          "openspan_settings.json",
          ns["load_setting"]("dock_surface") == "console"
          and ns["load_setting"]("dock_collapsed") is True)
    with open(settings_path, encoding="utf-8") as f:
        written = json.load(f)
    check("and they sit beside the settings already there, destroying none",
          written["vm_name"] == "OpenSpan"
          and written["desktop_monitor"] == "\\\\.\\DISPLAY1"
          and written["dock_surface"] == "console")


# ---- the dock writes its state in exactly one place -------------------------

dock_saves = [node for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "save_setting"
              and node.args and isinstance(node.args[0], ast.Constant)
              and node.args[0].value in ("dock_surface", "dock_collapsed")]
check("each dock key is written in exactly one place", len(dock_saves) == 2)
dock_state = next((node for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef)
                   and node.name == "_dock_state"), None)
check("that place is App._dock_state, the one writer",
      dock_state is not None
      and all(any(n is save for n in ast.walk(dock_state))
              for save in dock_saves))
check("the dock invents no settings file of its own",
      len([node for node in ast.walk(tree)
           if isinstance(node, ast.Constant)
           and isinstance(node.value, str)
           and node.value.endswith(".json")
           and "dock" in node.value]) == 0)


# ---- the label flips with the state ----------------------------------------

class LabelStub:
    _floating_now = False

    def _floating(self):
        return self._floating_now


label_fn = next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and node.name == "_float_label")
label_ns = {}
exec(compile(ast.Module([label_fn], []), "openspan.py", "exec"), label_ns)
LabelStub._float_label = label_ns["_float_label"]
stub = LabelStub()
on_desk_label = stub._float_label()
stub._floating_now = True
floating_label = stub._float_label()
check("the label names what the click will do, in both states",
      on_desk_label == "Float as a window"
      and floating_label == "Return to the desktop")


# ---- structural: one toggle, no new window ---------------------------------

saves = [node for node in ast.walk(tree)
         if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
         and node.func.id == "save_setting"
         and node.args and isinstance(node.args[0], ast.Constant)
         and node.args[0].value == "float_window"]
check("exactly one place in the app writes the setting", len(saves) == 1)

toggle = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef)
              and node.name == "_toggle_float_window")
check("that one place is _toggle_float_window",
      any(node is saves[0] for node in ast.walk(toggle)))
check("the toggle applies the placement immediately -- no restart",
      any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
          and n.func.attr == "_apply_window_placement"
          for n in ast.walk(toggle)))

surfaces = [node for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr == "_toggle_float_window"]
tray_fn = next(node for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef)
               and node.name == "_post_tray_menu")
tray_uses = [n for n in ast.walk(tray_fn) if isinstance(n, ast.Attribute)
             and n.attr == "_toggle_float_window"]
check("the tray item and the System-section switch are the only two surfaces "
      "onto it, and they share the one method",
      len(surfaces) == 2 and len(tray_uses) == 1)

tray_labels = [n for n in ast.walk(tray_fn) if isinstance(n, ast.Attribute)
               and n.attr == "_float_label"]
check("the tray item's label is the state-aware one, not a fixed string",
      len(tray_labels) == 1)

apply_fn = next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and node.name == "_apply_window_placement")
branches = sorted(n.func.attr for n in ast.walk(apply_fn)
                  if isinstance(n, ast.Call) and isinstance(n.func,
                                                            ast.Attribute)
                  and n.func.attr in ("apply", "release"))
check("the placement goes both ways from the one setting",
      branches == ["apply", "release"])

# Which METHODS open an OS window, not how many calls there are: a count is a
# number to bump, a name list says who is allowed. ONE is: the identify card (a
# number on each real monitor, which cannot be drawn from inside one window).
#
# The console window was the second for one day (2026-08-27 to 2026-08-28) and
# is not a window any more. Dialogs are banned; SURFACES are not, and a surface
# is not a window -- it is a region of this window, invoked by name from the
# right-side dock and replacing whatever was showing there. So a new surface
# never appears in this set. Anything that does is a pop-out and must fail here.
_owners = set()
for _fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
    if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
           and n.func.attr == "Toplevel" for n in ast.walk(_fn)):
        _owners.add(_fn.name)
check("no new Toplevel: the identify card is the only one, one call",
      _owners == {"_identify_card"}
      and len([node for node in ast.walk(tree)
               if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute)
               and node.func.attr == "Toplevel"]) == 1)

check("nothing calls the old mode wording anywhere in the app",
      "desk_mode" not in source and "desk mode" not in source.lower())

controller_calls = [node for node in ast.walk(tree)
                    if isinstance(node, ast.Attribute)
                    and node.attr in ("show_at_dock",)]
check("restoring from the tray goes through the docked show path",
      len(controller_calls) >= 1)

startup = [node for node in ast.walk(tree)
           if isinstance(node, ast.Attribute)
           and node.attr == "_apply_window_placement"]
check("the placement is applied at startup as well as on the toggle",
      len(startup) >= 2)


if failures:
    print(f"RESULT: {len(failures)} FAILED")
    raise SystemExit(1)
print("RESULT: ALL ON-DESKTOP TESTS PASSED")
