"""Telling "the dongle is unplugged" apart from "the device won't connect".

Doug, 29 July: *"I had to unplug my external bluetooth devices - how can i get
openspan to recognize them again and get back to functioning state"*.

A Bluetooth dongle reaches the guest by USB passthrough, and Windows takes it
back the moment it is replugged: VirtualBox only auto-captures a filtered device
as it ARRIVES, so a dongle that Windows binds first sits on the host marked
`Busy` forever. It is plugged in, its filter matches, and the guest cannot see it
at all.

From inside the app that looked exactly like a device that would not connect,
because nothing here had ever looked at the host's USB list. Recovery meant two
`VBoxManage` commands and knowing to run them.

The fixtures below are the REAL output from Doug's machine in exactly that state
-- one radio attached, two taken back -- with the dongle addresses removed. No VM
is contacted and no device is attached; the decision is a pure function and this
is that function.

Exit 0 = all pass.
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openspan as A  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (
        "" if cond or not detail else "\n      " + detail))
    if not cond:
        fails.append(name)


# The real shape, including the parts that make it awkward: a stanza with no
# Product line, a Manufacturer with trailing spaces, and two dongles sharing one
# vendor:product pair.
USBHOST = """UUID:               4311e301-2486-4011-8ef2-069317c35875
VendorId:           0xb58e (B58E)
ProductId:          0x9e84 (9E84)
Revision:           0x0100 (0100)
Manufacturer:       Blue Microphones
Product:            Yeti Stereo Microphone
Current State:      Busy

UUID:               f84f64f6-7755-4fd0-b03d-110762dba72c
VendorId:           0x8087 (8087)
ProductId:          0x0aaa (0AAA)
Revision:           0x0002 (0002)
Manufacturer:       Intel Corp.
Current State:      Captured

UUID:               ff68aac3-03b2-4f97-818d-93803323a0be
VendorId:           0x2357 (2357)
ProductId:          0x0604 (0604)
Port:               4
USB version/speed:  1/Full
Manufacturer:
Product:            TP-Link UB500 Adapter
SerialNumber:       ACA7F1299FCB
Current State:      Busy

UUID:               a21a565a-cd65-4a78-961f-4407a8e9f779
VendorId:           0x2357 (2357)
ProductId:          0x0604 (0604)
Port:               3
USB version/speed:  1/Full
Manufacturer:
Product:            TP-Link Bluetooth USB Adapter
SerialNumber:       3C6AD23CD44E
Current State:      Busy
"""

INFO = """name="OpenSpan-Codex"
usb="off"
ehci="off"
xhci="on"
USBFilterActive1="on"
USBFilterName1="IntelBT"
USBFilterVendorId1="8087"
USBFilterProductId1="0aaa"
USBFilterSerialNumber1=""
USBFilterActive2="on"
USBFilterName2="TPLinkBT-Port1"
USBFilterVendorId2="2357"
USBFilterProductId2="0604"
USBFilterActive3="on"
USBFilterName3="TPLinkBT-Port2"
USBFilterVendorId3="2357"
USBFilterProductId3="0604"
USBAttachActive1="f84f64f6-7755-4fd0-b03d-110762dba72c"
USBAttachVendorId1=0x8087
USBAttachProductId1=0x0aaa
"""

# ---- reading the host's USB list -------------------------------------------
devices = A.parse_usb_host(USBHOST)
check("every device is read, including the last with no trailing blank line",
      len(devices) == 4, str(len(devices)))
check("a stanza with no Product line still parses",
      any(d["uuid"].startswith("f84f64f6") and not d.get("name")
          for d in devices))
check("vendor and product ids are read without their echoed form",
      devices[0]["vendor"] == "0xb58e"
      and devices[0]["product_id"] == "0x9e84",
      f"{devices[0].get('vendor')} {devices[0].get('product_id')}")
check("the host's claim on a device is read",
      [d["state"] for d in devices]
      == ["Busy", "Captured", "Busy", "Busy"])
check("nothing is invented from empty input", A.parse_usb_host("") == []
      and A.parse_usb_host(None) == [])

# ---- reading the VM's own statement of what belongs to it -------------------
filters = A.parse_usb_filters(INFO)
check("every active filter is read, by name",
      sorted(f["name"] for f in filters)
      == ["IntelBT", "TPLinkBT-Port1", "TPLinkBT-Port2"],
      str([f["name"] for f in filters]))
check("a bare id is normalised to the form the host reports",
      all(f["vendor"].startswith("0x") for f in filters))
check("`usb=off` is not mistaken for a filter",
      not [f for f in filters if not f["name"]])
off = INFO.replace('USBFilterActive3="on"', 'USBFilterActive3="off"')
check("a disabled filter does not claim anything",
      len(A.parse_usb_filters(off)) == 2)

held = A.parse_usb_attached(INFO)
check("what the VM holds is read", held
      == {"f84f64f6-7755-4fd0-b03d-110762dba72c"}, str(held))
check("a VM holding nothing reads as nothing, not as an error",
      A.parse_usb_attached('name="x"\nusb="off"\n') == set())

# ---- the decision ----------------------------------------------------------
report = A.radio_report(USBHOST, INFO)
check("only devices the VM claims are considered ours",
      len(report["mine"]) == 3
      and not [d for d in report["mine"] if "Yeti" in (d.get("name") or "")],
      str([A.usb_label(d) for d in report["mine"]]))
check("the microphone is left alone even though it is also Busy",
      "Yeti Stereo Microphone" not in
      [A.usb_label(d) for d in report["lost"]])
check("both dongles the VM has lost are named",
      sorted(A.usb_label(d) for d in report["lost"])
      == ["TP-Link Bluetooth USB Adapter", "TP-Link UB500 Adapter"],
      str([A.usb_label(d) for d in report["lost"]]))
check("the one it already holds is not offered for reclaiming",
      "Intel Corp." not in [A.usb_label(d) for d in report["lost"]])
check("two dongles sharing one vendor:product are handled as two devices",
      len({d["uuid"] for d in report["lost"]}) == 2)
check("a device with no Product line is still nameable",
      A.usb_label(next(d for d in report["mine"]
                       if d["uuid"].startswith("f84f64f6"))) == "Intel Corp.")

healthy = INFO + ('USBAttachActive2="ff68aac3-03b2-4f97-818d-93803323a0be"\n'
                  'USBAttachActive3="a21a565a-cd65-4a78-961f-4407a8e9f779"\n')
check("with everything attached there is nothing to report",
      not A.radio_report(USBHOST, healthy)["lost"]
      and len(A.radio_report(USBHOST, healthy)["mine"]) == 3)

check("a machine with no matching adapter reports no radios at all, "
      "rather than claiming one is missing",
      A.radio_report(USBHOST, 'name="x"\n')["mine"] == [])

# a filter with no product id matches the whole vendor -- some adapters change
# their product id across firmware revisions
vendor_only = 'USBFilterActive1="on"\nUSBFilterName1="AnyTPLink"\n' \
              'USBFilterVendorId1="2357"\nUSBFilterProductId1=""\n'
check("a vendor-only filter claims every device of that vendor",
      len(A.radio_report(USBHOST, vendor_only)["mine"]) == 2)

# ---- naming a dongle something a human can find -----------------------------
# "TP-Link Bluetooth USB Adapter" cannot be picked out of two identical dongles
# behind a machine. It turns out it never had to be: a Bluetooth dongle's USB
# SerialNumber IS its adapter address, so the host alone -- with the VM down and
# the guest unreachable, which is exactly when it matters -- can say which of the
# user's machines the thing in their hand belongs to.
CONFIG = {"devices": [
    {"id": "ipad", "name": "iPad", "radio": "58:A0:23:CD:6A:B7"},
    {"id": "mac", "name": "Managed Mac", "radio": "AC:A7:F1:29:9F:CB"},
    {"id": "device-1", "name": "Managed Laptop", "radio": "3C:6A:D2:3C:D4:4E"},
]}

for serial, expect in (("ACA7F1299FCB", "AC:A7:F1:29:9F:CB"),
                       ("3c6ad23cd44e", "3C:6A:D2:3C:D4:4E"),
                       ("AC:A7:F1:29:9F:CB", "AC:A7:F1:29:9F:CB")):
    check(f"serial {serial} reads as a radio address",
          A.serial_to_radio(serial) == expect, A.serial_to_radio(serial))
for junk in ("", None, "abc", "NOTHEX123456", "ACA7F1299FC"):
    check(f"{junk!r} is not mistaken for one", A.serial_to_radio(junk) == "")

lost = A.radio_report(USBHOST, INFO)["lost"]
named = sorted(A.usb_label(d, CONFIG) for d in lost)
check("a dongle is named by the machine it serves",
      named == ["Managed Laptop’s dongle", "Managed Mac’s dongle"],
      str(named))
check("and falls back to its product string when nothing identifies it",
      A.usb_label({"name": "Some Dongle", "serial": "zz"}, CONFIG)
      == "Some Dongle")
check("with no config it still names something",
      A.usb_label(lost[0]) and "dongle" not in A.usb_label(lost[0]))

# ---- the root cause: two filters that cannot tell two dongles apart ---------
# Replugging both recovered one and left the other captured-away-from-Windows-
# but-never-delivered. Both filters matched 2357:0604 and nothing else, so two
# identical dongles arriving together raced two identical filters. A filter
# carrying the dongle's serial matches exactly one device and there is nothing
# left to race -- and `usbfilter modify` accepts it on a RUNNING VM, so this is a
# repair the app can just do.
plan = A.radio_filter_plan(CONFIG, USBHOST, INFO)
check("both ambiguous filters are planned for pinning",
      len(plan) == 2, str(plan))
check("each is pinned to a different dongle",
      len({step["serial"] for step in plan}) == 2, str(plan))
check("and each is described by the machine it will serve",
      sorted(step["label"] for step in plan)
      == ["Managed Laptop’s dongle", "Managed Mac’s dongle"],
      str([step["label"] for step in plan]))
check("the index is the 0-based one the usbfilter command wants, not the "
      "1-based one --machinereadable prints",
      sorted(step["index"] for step in plan) == [1, 2],
      str([step["index"] for step in plan]))

pinned_info = INFO.replace(
    'USBFilterProductId2="0604"',
    'USBFilterProductId2="0604"\nUSBFilterSerialNumber2="ACA7F1299FCB"'
).replace(
    'USBFilterProductId3="0604"',
    'USBFilterProductId3="0604"\nUSBFilterSerialNumber3="3C6AD23CD44E"')
check("an already-pinned filter is left alone",
      A.radio_filter_plan(CONFIG, USBHOST, pinned_info) == [],
      str(A.radio_filter_plan(CONFIG, USBHOST, pinned_info)))
check("the lone Intel filter is not pinned -- there is nothing to confuse it "
      "with, and pinning it would break the day the adapter changes",
      not [step for step in plan if step["name"] == "IntelBT"])

# ---- an attach that is accepted but never lands -----------------------------
# Doug: "I clicked reclaim, don't think anything happened, take a look."
#
# It had happened. VirtualBox accepted both requests and took both dongles off
# Windows -- and never handed them to the guest. `lsusb` in the guest showed one
# adapter, its dmesg showed no USB event since boot, and every further attach
# returned "is busy with a previous request". The app reported success, because it
# believed the exit code, and an exit code is about whether the REQUEST was
# accepted. From outside, a success message next to a line still reading "1 of 3"
# is indistinguishable from nothing happening.
calls = []


def fake_vbox(*args, **kwargs):
    calls.append(args)

    class R:
        returncode = 0
        stdout = ""
        stderr = ""
    return R()


def wedged_vbox(*args, **kwargs):
    calls.append(args)

    class R:
        returncode = 1
        stdout = ""
        stderr = ("VBoxManage.exe: error: USB device '  TP-Link UB500 Adapter' "
                  "with UUID {ff68} is busy with a previous request. Please try "
                  "again later")
    return R()


real_vbox, real_state = A.vbox, A.read_radio_state
LOST = [{"uuid": "ff68aac3", "name": "TP-Link UB500 Adapter", "state": "Busy",
         "filter": "TPLinkBT-Port1"}]
try:
    A.read_radio_state = lambda: {"mine": LOST, "lost": list(LOST),
                                  "held": set(), "filters": []}

    A.vbox = fake_vbox
    calls.clear()
    got, failed = A.reclaim_radios(settle=0, attempts=2, verify=lambda: set())
    check("an attach VirtualBox accepts but the VM never takes is a FAILURE",
          not got and len(failed) == 1, f"recovered={got} failed={failed}")
    check("and it says what actually clears it",
          "plug it back in" in failed[0][1], failed[0][1][:90])

    calls.clear()
    got, failed = A.reclaim_radios(settle=0, attempts=2,
                                   verify=lambda: {"ff68aac3"})
    check("an attach the VM really took is a success",
          got == ["TP-Link UB500 Adapter"] and not failed, str(failed))
    check("and it stops as soon as it has landed, rather than attaching twice",
          len([c for c in calls if "usbattach" in c]) == 1,
          str([c for c in calls if "usbattach" in c]))

    A.vbox = wedged_vbox
    calls.clear()
    got, failed = A.reclaim_radios(settle=0, attempts=3, verify=lambda: set())
    check("“busy with a previous request” is reported as itself",
          not got and failed[0][1].startswith(A.WEDGED_ADVICE),
          str(failed)[:140])
    check("and it is not retried, because retrying provably cannot clear it",
          len([c for c in calls if "usbattach" in c]) == 1,
          f"tried {len([c for c in calls if 'usbattach' in c])} times")
finally:
    A.vbox, A.read_radio_state = real_vbox, real_state

# ---- the wiring ------------------------------------------------------------
import inspect  # noqa: E402
src = inspect.getsource(A.BtPanel)
check("the check runs on a worker thread, never in front of the UI",
      "threading.Thread(target=work, daemon=True).start()"
      in inspect.getsource(A.BtPanel._radio_usb_check))
check("and every widget it touches is marshaled to the UI thread",
      "self.app.ui(apply)" in inspect.getsource(A.BtPanel._radio_usb_apply))
check("repairing is one button, and it says how many radios it covers",
      "self.reclaim_btn" in src and "Repair {lost} radio" in src)
check("reclaiming does not scan, pair or connect anything",
      not [word for word in ("bluetoothctl", "openspan_bt.py", "pair ", "trust")
           if word in inspect.getsource(A.reclaim_radios)])
check("the VM being down is reported as itself, not as a missing radio",
      "The VM is not running" in inspect.getsource(A.BtPanel._radio_usb_check))

reclaim_src = inspect.getsource(A.BtPanel._reclaim_radios)
check("the outcome reaches the status line, not only the log box",
      reclaim_src.count("_radio_usb_apply") >= 2, reclaim_src.count(
          "_radio_usb_apply"))
# the count is only re-read when nothing failed; otherwise the sentence naming
# the physical action would be replaced by "2 of 3 radios are attached", which is
# true, useless, and exactly what made the last build look like it did nothing
after = reclaim_src.split('if outcome["failed"]:', 1)
check("a wedged radio's explanation is not overwritten by a bare count",
      len(after) == 2
      and after[1].index("else:") < after[1].index("_radio_usb_check()"))
check("every Tk call in the worker goes through app.ui, including after()",
      "self.app.ui(lambda: self.after(" in reclaim_src)

print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
