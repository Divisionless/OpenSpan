"""Display identity guards.

A display profile is only as good as its ability to recognise the same physical
panel after a reboot or a cable swap, so every rung of the matching ladder is
pinned here. Pure core only: nothing in this file touches Windows."""

import dataclasses
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import monitor_identity as mi


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


check("importing the identity core does not pull in ctypes",
      "ctypes" not in sys.modules)

source = pathlib.Path(mi.__file__).read_text(encoding="utf-8")
check("every ctypes import sits inside a function",
      all(line.startswith(" ") for line in source.splitlines()
          if re.match(r"\s*import ctypes", line)))


def panel(manufacturer="DEL", product="A0B1", serial="SN12345",
          friendly="DELL U2720Q", w=3840, h=2160, x=0, y=0,
          device=r"\\.\DISPLAY1"):
    return mi.MonitorIdentity(
        manufacturer_id=manufacturer, product_code=product, serial_number=serial,
        friendly_name=friendly, native_width=w, native_height=h,
        virtual_x=x, virtual_y=y, device_name=device)


# ---- stable key ----------------------------------------------------------------

before = panel(device=r"\\.\DISPLAY1", x=0)
after = dataclasses.replace(before, device_name=r"\\.\DISPLAY3", virtual_x=-3840)
check("the stable key ignores the volatile device name and position",
      before.stable_key == after.stable_key)

anonymous = mi.MonitorIdentity(native_width=1920, native_height=1080)
check("panels without anything durable have no key",
      anonymous.stable_key is None)


# ---- the matching ladder -------------------------------------------------------

remembered = panel(serial="SN-A")
same_cable = dataclasses.replace(remembered, device_name=r"\\.\DISPLAY2",
                                 virtual_x=1920)
check("the serial number identifies the physical panel",
      mi.score(remembered, same_cable) == mi.MonitorMatch.SERIAL)

left = panel(serial="", x=0)
right = panel(serial="", x=1920)
check("identical models without serials match at model level",
      mi.score(left, right) == mi.MonitorMatch.MODEL)

bare_left = mi.MonitorIdentity(native_width=1920, native_height=1080, virtual_x=0)
bare_right = mi.MonitorIdentity(native_width=1920, native_height=1080, virtual_x=1920)
check("with no EDID at all only position can separate them",
      mi.score(bare_left, bare_left) == mi.MonitorMatch.POSITION
      and mi.score(bare_left, bare_right) == mi.MonitorMatch.NONE)

old_panel = panel(manufacturer="DEL", product="A0B1", serial="SN-A")
new_panel = panel(manufacturer="SAM", product="FFFF", serial="SN-B",
                  friendly="Samsung G7", w=2560, h=1440)
check("a replaced monitor of a different model does not match",
      mi.score(old_panel, new_panel) == mi.MonitorMatch.NONE)


# ---- assignment ----------------------------------------------------------------

remembered_pair = [panel(serial="SN-A", friendly="Panel A", x=0),
                   panel(serial="SN-B", friendly="Panel B", x=3840)]
# Reconnected in the opposite order, with new positions.
attached_pair = [panel(serial="SN-B", friendly="Panel B", x=0),
                 panel(serial="SN-A", friendly="Panel A", x=3840)]
assignment = mi.assign(remembered_pair, attached_pair)
check("assignment pairs serials before weaker signals",
      assignment[0] == 1 and assignment[1] == 0)

two = [panel(serial="SN-A"), panel(serial="SN-B", friendly="Panel B")]
one = [panel(serial="SN-A")]
assignment = mi.assign(two, one)
check("a disconnected monitor is simply absent from the assignment",
      assignment[0] == 0 and 1 not in assignment)

twin_remembered = [panel(serial="", friendly="Twin", x=0)]
twins = [panel(serial="", friendly="Twin", x=0),
         panel(serial="", friendly="Twin", x=1920)]
check("assignment is deterministic across repeated runs",
      all(mi.assign(twin_remembered, twins)[0] == 0 for _ in range(5)))


# ---- topology ------------------------------------------------------------------

replaced = [panel(serial="SN-A"),
            panel(serial="SN-C", friendly="Panel C", w=2560, h=1440)]
check("topology change detects arrival, departure and replacement",
      not mi.topology_changed(two, two)
      and mi.topology_changed(two, one)
      and mi.topology_changed(one, two)
      and mi.topology_changed(two, replaced))
