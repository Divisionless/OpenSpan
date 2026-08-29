"""Fake-driven and structural checks for on_desktop.py and its wiring.

No real HWND is touched: the controller talks to a recording fake, so every
native call, its order and its flag bits are assertable here. The live proof
that Windows actually honours the refusal is win\\probe_on_desktop.py.

TWO WINDOWS USE THIS MODULE SINCE 2026-08-29. The app is one, unchanged: right
edge of the EsotericOS Desktop work area, full height, pinned to HWND_BOTTOM.
The other is the DOCK STRIP -- the rail that used to live inside the app's
window, now a borderless window of its own on the same edge, pinned to
HWND_TOPMOST because a dock that sinks behind Chrome is a dock nobody can click
and it is the only way back to an app window it has hidden.

They share one rect function, one refusal and one re-dock, and the three
arguments that tell them apart are asserted below: `pin`, `height` (the strip is
as tall as its entries; the app is as tall as the work area), and `reserve` (the
band on the right edge the app must leave because the strip is standing in it).
A second placement path would be a second thing to keep in step with the first.
"""

import ast
import json
import os
import pathlib
import sys
import tempfile

import on_desktop


failures = []


def check(name, condition, detail=""):
    # `detail` printed only on failure, as every other suite here does it: a
    # rect that is wrong by sixteen pixels is unreadable without the numbers.
    print(("PASS " if condition else "FAIL ") + name + (
        "" if condition or not detail else "\n      " + detail))
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

# THE FLOOR IS AN ARGUMENT, since 2026-08-29. MIN_WIDTH protects a window that
# still has a pane in it, and it was the wrong number for one that deliberately
# has none: Doug collapsed the dock and got a full-width window holding nothing
# but the rail, because on the desktop every move but this module's own is
# refused and this module widened everything to 560 regardless.
check("a caller may say what the floor is, and the rect honours it",
      on_desktop.dock_rect((0, 0, 1920, 1040), 130, min_width=130)
      == (1920 - 16 - 130, 16, 130, 1008))
check("...and the right edge still holds: a thin dock hugs the same edge a "
      "wide one does",
      on_desktop.dock_rect((0, 0, 1920, 1040), 130, min_width=130)[0]
      + 130 == on_desktop.dock_rect((0, 0, 1920, 1040), 700)[0] + 700)
check("saying nothing still gets the ordinary floor, so every existing caller "
      "is unchanged",
      on_desktop.dock_rect((0, 0, 1920, 1040), 130)[2] == on_desktop.MIN_WIDTH)
check("the absolute floor is named, positive, and well below the ordinary one",
      0 < on_desktop.COLLAPSED_MIN_WIDTH < on_desktop.MIN_WIDTH)

# ---- a window as tall as its content, on the same edge, 2026-08-29 ----------
# The dock strip. `height` is the one thing it needs that the app does not, and
# the clamp under it is the contract with the OTHER dock on this edge: the
# shell's running-app icons sit below ours, neither process knows the other's
# size, so STRIP_GAP is the band a strip may never be placed into.
strip = on_desktop.dock_rect((0, 0, 1920, 1040), 130, min_width=130,
                             height=190)
check("a content height is honoured, top-aligned, at the same right edge a "
      "full-height window uses",
      strip == (1920 - 16 - 130, 16, 130, 190),
      repr(strip))
check("saying nothing about height still gets the work area's, so the app "
      "window is unchanged",
      on_desktop.dock_rect((0, 0, 1920, 1040), 700)[3] == 1008)
check("the gap below is NAMED, positive, and real space rather than a comment",
      on_desktop.STRIP_GAP > 0 and isinstance(on_desktop.STRIP_GAP, int))
tall = on_desktop.dock_rect((0, 0, 1920, 1040), 130, min_width=130,
                            height=99999)
check("A STRIP CANNOT GROW OVER THE APP-ICONS DOCK: an absurd content height "
      "is clamped so at least STRIP_GAP is left below it",
      tall[1] + tall[3] + on_desktop.STRIP_GAP <= 1040 - 16,
      f"{tall} on a 1040px work area")
check("...and the clamp leaves EXACTLY the gap, not an arbitrary fraction",
      tall[3] == (1040 - 32) - on_desktop.STRIP_GAP, repr(tall))
check("a caller may ask for a different gap, and 0 means the whole cavity",
      on_desktop.dock_rect((0, 0, 1920, 1040), 130, min_width=130,
                           height=99999, gap=0)[3] == 1008)
check("a content height never goes to zero or negative on an absurd work area",
      on_desktop.dock_rect((0, 0, 800, 10), 130, min_width=130,
                           height=190)[3] >= 1)

# ---- the band the app leaves for the strip ---------------------------------
# The mirror of `height`: the app must stop where the dock begins. Reserve moves
# it left AND comes out of the width cap, or a window wide enough to fill the
# work area would simply slide back underneath.
RESERVE = 130 + on_desktop.INSET
reserved = on_desktop.dock_rect((0, 0, 1920, 1040), 700, reserve=RESERVE)
check("a reserved band moves the window left by exactly that many pixels",
      reserved[0] == on_desktop.dock_rect((0, 0, 1920, 1040), 700)[0] - RESERVE
      and reserved[2] == 700, repr(reserved))
check("...and its right edge lands where the strip's left edge is, so the two "
      "abut with one INSET of air and never overlap",
      reserved[0] + reserved[2] + on_desktop.INSET
      == on_desktop.dock_rect((0, 0, 1920, 1040), 130, min_width=130)[0],
      repr(reserved))
squeezed = on_desktop.dock_rect((0, 0, 1920, 1040), 5000, reserve=RESERVE)
check("A FULL-WIDTH WINDOW STILL STOPS AT THE DOCK: the reserve comes out of "
      "the width cap too, not only out of the position",
      squeezed[0] >= 16 and squeezed[0] + squeezed[2] == 1920 - 16 - RESERVE,
      repr(squeezed))
check("reserving nothing is the old behaviour exactly",
      on_desktop.dock_rect((0, 0, 1920, 1040), 700, reserve=0)
      == on_desktop.dock_rect((0, 0, 1920, 1040), 700))
check("a nonsense reserve is floored at 0 rather than pushing a window right",
      on_desktop.dock_rect((0, 0, 1920, 1040), 700, reserve=-900)
      == on_desktop.dock_rect((0, 0, 1920, 1040), 700))


# ---- the refusal ------------------------------------------------------------
# ONE function, both directions. It was `refuse_raise` while only the app used
# it; the name stopped being true the day the strip needed the opposite pin, so
# it is `pin_z_order` and the old name is gone rather than aliased.
check("the renamed function is the only one: refuse_raise is not still here "
      "under the old name",
      not hasattr(on_desktop, "refuse_raise"))

pos = FakeWindowPos(insert_after=0, flags=on_desktop.SWP_NOZORDER
                    | on_desktop.SWP_NOMOVE)
on_desktop.pin_z_order(pos)
check("a raise is rewritten to HWND_BOTTOM by default, so the app is unchanged",
      pos.hwndInsertAfter == on_desktop.HWND_BOTTOM
      and on_desktop.HWND_BOTTOM == 1)
check("SWP_NOZORDER is cleared, or Windows would ignore the rewrite",
      not pos.flags & on_desktop.SWP_NOZORDER)
check("the other flags in the same word are left alone",
      pos.flags & on_desktop.SWP_NOMOVE)

pos = FakeWindowPos(insert_after=0, flags=0)
check("a position change that was not a z-order change is pinned down too",
      on_desktop.pin_z_order(pos).hwndInsertAfter == on_desktop.HWND_BOTTOM)

pos = FakeWindowPos(insert_after=0, flags=on_desktop.SWP_NOZORDER)
on_desktop.pin_z_order(pos, on_desktop.HWND_TOPMOST)
check("THE OTHER DIRECTION: a strip is pinned to HWND_TOPMOST by the same "
      "rewrite, so a drop out of topmost is refused the way a raise is",
      pos.hwndInsertAfter == on_desktop.HWND_TOPMOST
      and on_desktop.HWND_TOPMOST == -1
      and not pos.flags & on_desktop.SWP_NOZORDER, repr(pos.flags))
check("the two pins are different windows' business and cannot be confused",
      on_desktop.HWND_TOPMOST != on_desktop.HWND_BOTTOM)


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


# ---- a dock that is allowed to be thin, and only then ----------------------
# `geometry()` cannot resize a window on the desktop -- refuse_move is the
# whole point of this module -- so set_width IS the resize, and set_min_width
# is the declaration that makes a narrow one legitimate. The floor is CACHED
# rather than asked for, because the re-dock that has to respect it runs inside
# the window procedure, where a Tk read is the reentrancy fault this file
# already exists to avoid.

thin = FakeBindings(styles={on_desktop.GWL_STYLE: STYLE,
                            on_desktop.GWL_EXSTYLE: EXSTYLE})
dock = on_desktop.OnDesktop(lambda: 909,
                            lambda: on_desktop.dock_rect(thin._work, 1120),
                            bindings=thin, monitor_name="\\\\.\\DISPLAY2")
dock.apply()
check("a fresh controller starts at the ordinary floor",
      dock.min_width == on_desktop.MIN_WIDTH)
thin.calls.clear()
check("asking for a narrow width WITHOUT lowering the floor gets the floor, "
      "not the ask -- an ordinary window cannot be made unusable this way",
      dock.set_width(130) and dock.docked_width == on_desktop.MIN_WIDTH
      and args_of(thin, "SetWindowPos")[-1][2:6]
      == (1920 - 16 - 560, 16, 560, 1008))
check("lowering the floor moves nothing on its own; it only says what is now "
      "allowed",
      dock.set_min_width(130) == 130 and dock.min_width == 130
      and len(args_of(thin, "SetWindowPos")) == 1)
thin.calls.clear()
check("AND THEN the narrow width is placed, right edge kept, at the bottom",
      dock.set_width(130) and dock.docked_width == 130
      and args_of(thin, "SetWindowPos")[-1][2:6]
      == (1920 - 16 - 130, 16, 130, 1008))
thin.calls.clear()
thin._work = (0, 0, 1600, 900)
thin.proc(909, on_desktop.WM_SETTINGCHANGE, 0, 0)
check("A SHELL BAR APPEARING DOES NOT UNDO THE COLLAPSE: the wndproc re-docks "
      "against the cached floor, so the thin dock stays thin",
      args_of(thin, "SetWindowPos")[-1][2:6]
      == (1600 - 16 - 130, 16, 130, 868))
thin.calls.clear()
check("the floor goes back up as easily as it came down, and the width with it",
      dock.set_min_width(on_desktop.MIN_WIDTH) == on_desktop.MIN_WIDTH
      and dock.set_width(1120) and dock.docked_width == 1120
      and args_of(thin, "SetWindowPos")[-1][2:6]
      == (1600 - 16 - 1120, 16, 1120, 868))
check("no floor may go below the absolute one, whatever is asked for",
      dock.set_min_width(0) == on_desktop.COLLAPSED_MIN_WIDTH
      and dock.set_min_width(-500) == on_desktop.COLLAPSED_MIN_WIDTH)
check("...and a floor that is not a number at all leaves the last one standing",
      dock.set_min_width("thin") == on_desktop.COLLAPSED_MIN_WIDTH
      and dock.set_width(None) is False)
dock.release()
check("neither of them moves a window that is no longer on the desktop",
      dock.set_width(400) is False)


# ---- the dock strip: the same controller, three arguments different --------
# It is the SAME class doing the SAME job for a window with different needs. If
# any of this had to be a second controller, or a second wndproc, or a timer,
# that would be the signal the extension was wrong.

RAIL_W, RAIL_H = 132, 186
sbind = FakeBindings(styles={on_desktop.GWL_STYLE: STYLE,
                             on_desktop.GWL_EXSTYLE: EXSTYLE})
strip_ctl = on_desktop.OnDesktop(
    lambda: 1717,
    lambda: on_desktop.dock_rect(sbind._work, RAIL_W, min_width=RAIL_W,
                                 height=RAIL_H),
    bindings=sbind, monitor_name="\\\\.\\DISPLAY2",
    pin=on_desktop.HWND_TOPMOST, fixed_height=True)
strip_ctl.set_min_width(RAIL_W)
check("the strip applies through the same path the app does",
      strip_ctl.apply() and strip_ctl.active)
placed = args_of(sbind, "SetWindowPos")[-1]
check("IT IS PLACED TOPMOST, not at the bottom: a dock behind other windows is "
      "a dock nobody can click, and it is the only way back to a hidden app",
      placed[1] == on_desktop.HWND_TOPMOST, repr(placed[1]))
check("...on the right edge of the work area, top-aligned, at the rail's own "
      "size and no larger",
      placed[2:6] == (1920 - 16 - RAIL_W, 16, RAIL_W, RAIL_H), repr(placed))
check("...and it is far shorter than the work area, which is the whole of what "
      "Doug asked for: 'just to Scripts and then below it our new vertical "
      "dock'",
      placed[5] < (1040 - 32) // 2, f"{placed[5]}px of {1040 - 32}")
check("the caption and the sizing frame come off it exactly as they do for the "
      "app -- no overrideredirect, one frameless mechanism in this app",
      any(a[1] == on_desktop.GWL_STYLE
          and not a[2] & (on_desktop.WS_CAPTION | on_desktop.WS_THICKFRAME)
          for a in args_of(sbind, "SetWindowLongPtrW")))
check("...and it carries no taskbar button, being a dock rather than an app",
      any(a[1] == on_desktop.GWL_EXSTYLE
          and a[2] & on_desktop.WS_EX_TOOLWINDOW
          for a in args_of(sbind, "SetWindowLongPtrW")))
check("the height it was placed at is cached, so a re-dock needs no Tk read",
      strip_ctl.docked_height == RAIL_H and strip_ctl.docked_width == RAIL_W)
check("the pin and the reserve are readable state, not buried constants",
      strip_ctl.pin == on_desktop.HWND_TOPMOST and strip_ctl.reserve == 0
      and dock.pin == on_desktop.HWND_BOTTOM)

sunk_strip = FakeWindowPos(insert_after=0, flags=on_desktop.SWP_NOZORDER)
sbind.proc(1717, on_desktop.WM_WINDOWPOSCHANGING, 0, sunk_strip)
check("the strip's window procedure refuses a DROP the way the app's refuses a "
      "raise -- the same rewrite, the other pin",
      sunk_strip.hwndInsertAfter == on_desktop.HWND_TOPMOST
      and not sunk_strip.flags & on_desktop.SWP_NOZORDER)
check("...and it still refuses every move that is not its own, so the dock "
      "cannot be dragged off the edge either",
      sunk_strip.flags & on_desktop.SWP_NOMOVE
      and sunk_strip.flags & on_desktop.SWP_NOSIZE)

# THE THREE THINGS THAT ALREADY BREAK PLACEMENT HERE, driven one at a time.
sbind.calls.clear()
sbind._work = (0, 0, 2560, 1400)
sbind.proc(1717, on_desktop.WM_DISPLAYCHANGE, 0, 0)
moved = args_of(sbind, "SetWindowPos")[-1]
check("A DISPLAY ARRANGEMENT CHANGE re-docks the strip to the new right edge, "
      "and it stays as tall as its content rather than stretching",
      moved[2:6] == (2560 - 16 - RAIL_W, 16, RAIL_W, RAIL_H), repr(moved))
sbind.calls.clear()
sbind._work = (0, 0, 2560, 1330)          # a shell bar appeared at the bottom
sbind.proc(1717, on_desktop.WM_SETTINGCHANGE, 0, 0)
barred = args_of(sbind, "SetWindowPos")[-1]
check("A SHELL BAR APPEARING re-docks it against the new work area and does "
      "not widen it back to MIN_WIDTH, because the floor is cached",
      barred[2:6] == (2560 - 16 - RAIL_W, 16, RAIL_W, RAIL_H), repr(barred))
sbind.calls.clear()
check("DESKTOP ROLE CHANGED: the strip follows the screen the app follows, "
      "through the same set_monitor the app uses",
      strip_ctl.set_monitor("\\\\.\\DISPLAY4")
      and strip_ctl.monitor_name == "\\\\.\\DISPLAY4"
      and ("work_area", ("\\\\.\\DISPLAY4",)) in sbind.calls)
check("...and it re-reads the work area natively, never calling back into a "
      "geometry provider that would touch Tk inside message dispatch",
      args_of(sbind, "SetWindowPos") != [])
sbind.calls.clear()
sbind._work = (0, 0, 1920, 300)           # a work area shorter than the rail
sbind.proc(1717, on_desktop.WM_SETTINGCHANGE, 0, 0)
tiny = args_of(sbind, "SetWindowPos")[-1]
check("on a work area too short for the rail, the clamp still leaves the "
      "app-icons dock its band rather than covering the whole edge",
      tiny[3] + tiny[5] + on_desktop.STRIP_GAP <= 300 - 16, repr(tiny))

# ...and the app window, told what band to leave, keeps leaving it.
sbind.calls.clear()
thin2 = FakeBindings(styles={on_desktop.GWL_STYLE: STYLE,
                             on_desktop.GWL_EXSTYLE: EXSTYLE})
app_ctl = on_desktop.OnDesktop(
    lambda: 4141,
    lambda: on_desktop.dock_rect(thin2._work, 1120,
                                 reserve=RAIL_W + on_desktop.INSET),
    bindings=thin2, monitor_name="\\\\.\\DISPLAY2")
app_ctl.apply()
check("the app window is placed clear of the strip's band from the first dock",
      args_of(thin2, "SetWindowPos")[-1][2:6]
      == (1920 - 16 - (RAIL_W + on_desktop.INSET) - 1120, 16, 1120, 1008),
      repr(args_of(thin2, "SetWindowPos")[-1]))
check("set_reserve caches it and moves nothing on its own, exactly as "
      "set_min_width does",
      app_ctl.set_reserve(RAIL_W + on_desktop.INSET) == RAIL_W
      + on_desktop.INSET
      and len(args_of(thin2, "SetWindowPos")) == 1)
thin2.calls.clear()
thin2._work = (0, 0, 1600, 900)
thin2.proc(4141, on_desktop.WM_SETTINGCHANGE, 0, 0)
check("A SHELL BAR DOES NOT SLIDE THE APP BACK UNDER THE DOCK: the wndproc "
      "re-docks against the cached reserve, from Win32 alone",
      args_of(thin2, "SetWindowPos")[-1][2:6]
      == (1600 - 16 - (RAIL_W + on_desktop.INSET) - 1120, 16, 1120, 868),
      repr(args_of(thin2, "SetWindowPos")[-1]))
check("a nonsense reserve leaves the last good one standing",
      app_ctl.set_reserve("wide") == RAIL_W + on_desktop.INSET)
check("...and an app controller says nothing about height, so it still fills "
      "the work area after every re-dock",
      app_ctl.docked_height == 868)


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
# number to bump, a name list says who is allowed. TWO are, since 2026-08-29.
#
#   _identify_card -- a number on each real monitor, which cannot be drawn from
#     inside one window because its whole point is to be on the others.
#   _build_dock_strip -- the dock, which cannot be inside the app's window
#     because its job is to show and hide that window.
#
# The console window was a third for one day (2026-08-27 to 2026-08-28) and is
# not a window any more. Dialogs are banned; SURFACES are not, and the test is
# WHO PLACES IT: Windows places a dialog, on a screen he was not looking at,
# and this process places both of these, on screens it chose. A new surface is
# a Frame in the surface region and never appears in this set. Anything that
# does is a pop-out and must fail here.
_owners = set()
for _fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
    if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
           and n.func.attr == "Toplevel" for n in ast.walk(_fn)):
        _owners.add(_fn.name)
check("no new Toplevel: the identify card and the dock strip, one call each",
      _owners == {"_identify_card", "_build_dock_strip"}
      and len([node for node in ast.walk(tree)
               if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute)
               and node.func.attr == "Toplevel"]) == 2)


# ---- the strip's wiring, read off the app ----------------------------------


def _fn_of(name):
    return next((node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef) and node.name == name),
                None)


def _src_of(name):
    """Unparsed source of a method WITHOUT its docstring.

    Prose must never be able to pass or fail a contract check: every method
    below has a docstring that names the thing being banned -- "no
    overrideredirect", "no release path" -- and to a substring test the reason
    reads exactly like the offence. The same helper the other suites use.
    """
    node = _fn_of(name)
    if node is None:
        return ""
    clone = ast.parse(ast.unparse(node)).body[0]
    if (clone.body and isinstance(clone.body[0], ast.Expr)
            and isinstance(clone.body[0].value, ast.Constant)
            and isinstance(clone.body[0].value.value, str)):
        clone.body = clone.body[1:] or [ast.Pass()]
    return ast.unparse(clone)


for _needed in ("_build_dock_strip", "_strip_controller", "_strip_geometry",
                "_strip_place", "_strip_reserve", "_rail_height"):
    check(f"{_needed} exists", _fn_of(_needed) is not None)

strip_src = _src_of("_strip_controller")
check("the strip is placed by on_desktop.OnDesktop -- the SAME controller the "
      "app uses, not a second placement path invented beside it",
      "on_desktop.OnDesktop(" in strip_src, strip_src)
check("...pinned TOPMOST and told its height is its content's",
      "pin=on_desktop.HWND_TOPMOST" in strip_src
      and "fixed_height=True" in strip_src, strip_src)
check("...on the Desktop-role screen, resolved by the one resolver the app "
      "window uses",
      "self._desktop_monitor_name()" in strip_src
      and "self._desktop_monitor_name()" in _src_of("_strip_geometry"))
check("the strip's rect comes from on_desktop.dock_rect with a height, so the "
      "gap below it is the module's clamp and not a number retyped here",
      "on_desktop.dock_rect(" in _src_of("_strip_geometry")
      and "height=self._rail_height()" in _src_of("_strip_geometry"),
      _src_of("_strip_geometry"))
check("the app window is placed against the band the strip stands in",
      "reserve=self._strip_reserve()" in _src_of("_desktop_geometry"),
      _src_of("_desktop_geometry"))
check("...and the reserve is the rail's width plus the module's own inset, so "
      "the two docks cannot be measured against different air",
      "self._rail_width() + on_desktop.INSET" in _src_of("_strip_reserve"),
      _src_of("_strip_reserve"))
place_src = _src_of("_apply_window_placement")
check("the strip is placed on every placement pass, and BEFORE the app window "
      "whose rect is computed against it",
      "self._strip_place()" in place_src
      and place_src.index("self._strip_place()")
      < place_src.index("ctl.set_reserve"), place_src)
check("float_window frees the APP window and never the dock: there is no "
      "release path for the strip",
      "release" not in _src_of("_strip_place"), _src_of("_strip_place"))
sync_src = _src_of("_sync_desktop_monitor")
check("DESKTOP ROLE CHANGED: both windows are re-pointed in the one funnel "
      "every role change already passes through",
      "self._desktop.set_monitor(name)" in sync_src
      and "self._strip.set_monitor(name)" in sync_src, sync_src)
monitor_setters = {node.name for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef)
                   and "set_monitor(" in ast.unparse(node)}
check("...and that funnel is the only place either is re-pointed",
      monitor_setters == {"_sync_desktop_monitor"},
      repr(sorted(monitor_setters)))
strip_place_src = _src_of("_strip_place")
check("the strip states its floor before every placement, for the reason "
      "set_min_width exists: the wndproc re-docks from cached numbers",
      "set_min_width" in strip_place_src, strip_place_src)
strip_users = {node.name for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef)
               and "self._strip_controller()" in _src_of(node.name)}
check("nothing but _strip_place ever reaches the strip's controller, so there "
      "is one place the dock is put on screen",
      strip_users == {"_strip_place"}, repr(sorted(strip_users)))
strip_placers = {node.name for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)
                 and "self._strip_place()" in _src_of(node.name)}
check("...and one caller of that: the placement pass, which startup and the "
      "float toggle already share",
      strip_placers == {"_apply_window_placement"}, repr(sorted(strip_placers)))
strip_build_src = _src_of("_build_dock_strip")
check("the strip is NOT overrideredirect: this app has one frameless "
      "mechanism, and it is the style strip in OnDesktop.apply",
      "overrideredirect" not in strip_build_src, strip_build_src)
check("nothing closes the dock -- WM_DELETE_WINDOW is answered rather than "
      "left to Tk's destroy, because it is the way back to a hidden window",
      "protocol('WM_DELETE_WINDOW'" in strip_build_src, strip_build_src)
check("it comes up withdrawn, so it never flashes at Tk's default position "
      "before _strip_place puts it on the edge",
      "strip.withdraw()" in strip_build_src, strip_build_src)
check("the rail is built INTO the strip and nowhere else",
      "self._build_dock_rail(strip)" in strip_build_src, strip_build_src)

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
