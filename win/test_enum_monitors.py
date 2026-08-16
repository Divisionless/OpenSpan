"""The app's eye must see every monitor Windows composes the desktop from.

Doug, 2026-08-15: "Mac 2k lost its third display." It was never lost, and it
was not stale config. enum_monitors() was dropping it on every call:
GetMonitorInfoW was called with no argtypes, so ctypes squeezed a pointer-
sized HMONITOR into a 32-bit int, the handle 0xffffffff8fc70da1 overflowed,
the exception was raised INSIDE the enumeration callback -- where ctypes'
rule is print-and-continue, i.e. silence in a --noconsole build -- and two
monitors came back. Every reader downstream then behaved correctly on wrong
input.

Two checks. The first is the one that FAILS on the previous commit on Doug's
desk: enum_monitors() must agree with an independent count of active
desktop-attached display devices. The second pins the fix's shape so a
refactor cannot quietly drop the argtypes again.
"""

import ctypes
import ctypes.wintypes as wt
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import openspan_setup  # noqa: E402


def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        if detail:
            print("      " + detail)
        raise AssertionError(name)


# ---- 1. agree with an INDEPENDENT enumeration -----------------------------
# EnumDisplayDevicesW walks adapters, not monitors, and knows nothing about
# HMONITOR handles -- so it cannot share the bug. Every device flagged
# ATTACHED_TO_DESKTOP is a monitor the desktop is composed of.

class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("DeviceName", wt.WCHAR * 32),
                ("DeviceString", wt.WCHAR * 128), ("StateFlags", wt.DWORD),
                ("DeviceID", wt.WCHAR * 128), ("DeviceKey", wt.WCHAR * 128)]


ATTACHED = 0x1
MIRRORING = 0x8
u32 = ctypes.WinDLL("user32", use_last_error=True)
u32.EnumDisplayDevicesW.restype = wt.BOOL
u32.EnumDisplayDevicesW.argtypes = [
    wt.LPCWSTR, wt.DWORD, ctypes.POINTER(DISPLAY_DEVICEW), wt.DWORD]

attached = []
i = 0
while True:
    dd = DISPLAY_DEVICEW()
    dd.cb = ctypes.sizeof(dd)
    if not u32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
        break
    if (dd.StateFlags & ATTACHED) and not (dd.StateFlags & MIRRORING):
        attached.append(dd.DeviceName)
    i += 1

seen = openspan_setup.enum_monitors()
names = sorted(m["name"] for m in seen)

if not attached:
    print("no desktop-attached displays reported (headless?) -- count check skipped")
else:
    check("enum_monitors() reports every desktop-attached display",
          len(seen) == len(attached),
          f"enum_monitors={names}  attached={sorted(attached)}  "
          f"errors={openspan_setup.LAST_ENUM_ERRORS}")
    check("and the SAME displays, by name",
          set(names) == set(attached),
          f"enum_monitors={names}  attached={sorted(attached)}")

check("a healthy enumeration reports no unreadable monitors",
      not openspan_setup.LAST_ENUM_ERRORS,
      str(openspan_setup.LAST_ENUM_ERRORS))
check("every reported monitor has a real size",
      all(m["w"] > 0 and m["h"] > 0 for m in seen))
check("exactly one monitor is primary",
      sum(1 for m in seen if m.get("primary")) == 1 or not seen)

# ---- 2. the shape of the fix -------------------------------------------------

src = (pathlib.Path(__file__).parent / "openspan_setup.py").read_text(
    encoding="utf-8")
check("GetMonitorInfoW declares its argtypes, so a wide handle is a pointer "
      "and never a 32-bit int",
      re.search(r"GetMonitorInfoW\.argtypes\s*=\s*\[\s*wt\.HMONITOR", src)
      is not None)
check("the enumeration callback declares HMONITOR, not c_void_p/c_double",
      re.search(r"WINFUNCTYPE\(\s*wt\.BOOL,\s*wt\.HMONITOR,\s*wt\.HDC,"
                r"\s*ctypes\.POINTER\(RECT\),\s*wt\.LPARAM\)", src) is not None)
check("enum_monitors uses its own user32 handle, not the process-shared one",
      'ctypes.WinDLL("user32"' in src
      and "user32 = ctypes.windll.user32" not in
      src.split("def enum_monitors")[1].split("return monitors")[0])
check("an unreadable monitor is recorded, not silently skipped",
      "LAST_ENUM_ERRORS.append" in src)
check("but never injected into the model as a phantom rectangle",
      '"<unreadable' not in src)
