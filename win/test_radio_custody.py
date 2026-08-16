"""Fake-driven and structural checks for radio_custody.py and its wiring.

No device node is opened, no driver is bound, no VBoxManage runs: the
controller talks to a recording fake, so every native call, its ORDER and its
arguments are assertable here. The live read-only proof against the real
machine is `radio_custody.py audit`, which is safe to run at any time.

The rows below are the REAL shapes probed on Doug's desk on 2026-08-16, with
one correction that cost the first version of the verdict table: a healthy
dongle under runtime VirtualBox capture reports `present = False` on its real
node too, because VBoxUSBMon tore that node down and stood a
USB\\VID_80EE&PID_CAFE proxy up in its place. Only the VirtualBox host state
separates that from the wedge.

Exit 0 = all pass.
"""

import ast
import os
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import radio_custody as RC  # noqa: E402

failures = []


def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name
          + ("" if condition or not detail else "\n      " + detail))
    if not condition:
        failures.append(name)


# ---- recording fake --------------------------------------------------------

class FakeBindings:
    """Every call the controller can make, recorded in order."""

    def __init__(self, nodes=None, elevated=True, inf=True, cat=True,
                 drivers=None, device_drivers=None):
        self.calls = []
        self.nodes = nodes or {}
        self._elevated = elevated
        self._inf, self._cat = inf, cat
        self._drivers = drivers if drivers is not None else [
            {"description": RC.VBOX_DRIVER_DESC, "mfg": "Oracle Corporation",
             "provider": "Oracle Corporation", "version": "7.2.12.24389",
             "_drvinfo": object()}]
        self._device_drivers = (device_drivers if device_drivers is not None
                                else list(self._drivers))
        self.set_class_ok = True

    def _log(self, name, *args):
        self.calls.append((name, args))

    # reads
    def is_elevated(self):
        self._log("is_elevated")
        return self._elevated

    def file_exists(self, path):
        self._log("file_exists", path)
        return self._cat if path.endswith(".cat") else self._inf

    def usb_instance_ids(self):
        self._log("usb_instance_ids")
        return list(self.nodes)

    def open_device(self, iid):
        self._log("open_device", iid)
        return (("H:" + iid), {"iid": iid}) if iid in self.nodes else (None,
                                                                       None)

    def close_device(self, handle):
        self._log("close_device", handle)

    def get_property(self, handle, info, prop):
        self._log("get_property", info["iid"], prop)
        node = self.nodes[info["iid"]]
        return {RC.SPDRP_SERVICE: node.get("service", ""),
                RC.SPDRP_DEVICEDESC: node.get("desc", ""),
                RC.SPDRP_CLASSGUID: node.get("class", "")}.get(prop, "")

    def get_property_dword(self, handle, info, prop):
        self._log("get_property_dword", info["iid"], prop)
        return self.nodes[info["iid"]].get("removal_policy")

    def is_present(self, iid):
        self._log("is_present", iid)
        return bool(self.nodes.get(iid, {}).get("present"))

    def node_exists(self, iid):
        self._log("node_exists", iid)
        return iid in self.nodes

    def find_vbox_driver_at_set_level(self, inf):
        self._log("find_vbox_driver_at_set_level", inf)
        return list(self._drivers)

    # state changers
    def set_class_guid(self, handle, info, guid):
        self._log("set_class_guid", info["iid"], guid)
        return self.set_class_ok

    def set_device_driver_path(self, handle, info, inf):
        self._log("set_device_driver_path", info["iid"], inf)
        return True

    def build_device_driver_list(self, handle, info):
        self._log("build_device_driver_list", info["iid"])
        return list(self._device_drivers)

    def set_selected_driver(self, handle, info, drvinfo):
        self._log("set_selected_driver", info["iid"])
        return True

    def install_device(self, handle, info, drvinfo):
        self._log("install_device", info["iid"])
        return True, False, 0

    def remove_device(self, iid):
        self._log("remove_device", iid)
        return 0, "removed"

    def scan_devices(self):
        self._log("scan_devices")
        return 0, "scanned"


INTEL_IID = r"USB\VID_8087&PID_0AAA\5&3B2D9A0D&0&14"
INTEL_PROXY = r"USB\VID_80EE&PID_CAFE\5&3B2D9A0D&0&14"
TPLINK_IID = r"USB\VID_2357&PID_0604\ACA7F1299FCB"
TPLINK_PROXY = r"USB\VID_80EE&PID_CAFE\ACA7F1299FCB"


def real_nodes(intel_proxy=True, intel_present=False, intel_service="BTHUSB"):
    nodes = {
        INTEL_IID: {"present": intel_present, "service": intel_service,
                    "desc": "Intel(R) Wireless Bluetooth(R)",
                    "class": "{e0cbf06c-cd8b-4647-bb8a-263b43f0f974}",
                    "removal_policy": None},
        TPLINK_IID: {"present": False, "service": "BTHUSB",
                     "desc": "TP-Link Bluetooth 5.4 USB Adapter",
                     "class": "{e0cbf06c-cd8b-4647-bb8a-263b43f0f974}",
                     "removal_policy": None},
        TPLINK_PROXY: {"present": True, "service": "VBoxUSB",
                       "desc": RC.VBOX_DRIVER_DESC,
                       "class": RC.USB_CLASS_GUID.lower(),
                       "removal_policy": 3},
    }
    if intel_proxy:
        nodes[INTEL_PROXY] = {"present": True, "service": "VBoxUSB",
                              "desc": RC.VBOX_DRIVER_DESC,
                              "class": RC.USB_CLASS_GUID.lower(),
                              "removal_policy": 1}
    return nodes


def state(intel_state="Captured", tplink_state="Captured", held=()):
    return {
        "mine": [
            {"vendor": "0x8087", "product_id": "0x0aaa", "serial": "",
             "port": "14", "state": intel_state, "uuid": "u-intel",
             "uuids": {"u-intel"}, "name": "Intel Corp."},
            {"vendor": "0x2357", "product_id": "0x0604",
             "serial": "ACA7F1299FCB", "port": "4", "state": tplink_state,
             "uuid": "u-tp1", "uuids": {"u-tp1"},
             "name": "TP-Link UB500 Adapter"},
        ],
        "attached": [], "absent": [], "held": set(held),
    }


def controller(bindings, radio_state=None):
    return RC.RadioCustody(bindings=bindings,
                           radio_state=radio_state or state(),
                           vm="OpenSpan-Codex")


# ---- 1. the verdict table --------------------------------------------------

print("\n-- verdict table (present, service, vbox_state, node, proxy) --")

cases = [
    # (present, service, vbox_state, node_exists, proxy_present, expected)
    (True, "BTHUSB", "Busy", True, False, RC.WINDOWS_OWNED),
    (True, "", "Available", True, False, RC.WINDOWS_OWNED),
    (True, "VBoxUSB", "Captured", True, False, RC.CUSTODY),
    (True, "vboxusb", "Captured", True, True, RC.CUSTODY),
    (False, "BTHUSB", "Captured", True, True, RC.WINDOWS_OWNED),
    (False, "BTHUSB", "Busy", True, True, RC.WINDOWS_OWNED),
    (False, "BTHUSB", "Held", True, True, RC.WINDOWS_OWNED),
    (False, "BTHUSB", "Unavailable", True, True, RC.PHANTOM),
    (False, "BTHUSB", "Captured", True, False, RC.PHANTOM),
    (False, "BTHUSB", "", True, False, RC.PHANTOM),
    (False, "BTHUSB", "Captured", False, True, RC.ABSENT),
    (True, "VBoxUSB", "Captured", False, False, RC.ABSENT),
]
for present, service, vstate, exists, proxy, want in cases:
    got = RC.verdict(present, service, vstate, node_exists=exists,
                     proxy_present=proxy)
    check(f"verdict(present={present}, {service or 'none'!r}, {vstate!r}, "
          f"node={exists}, proxy={proxy}) == {want}", got == want,
          f"got {got}")

check("a torn-down real node with a live proxy and a USABLE host state is NOT "
      "called a phantom -- that mistake would flag two working dongles",
      RC.verdict(False, "BTHUSB", "Captured", True, True) == RC.WINDOWS_OWNED)
check("the same shape with host state Unavailable IS the wedge",
      RC.verdict(False, "BTHUSB", "Unavailable", True, True) == RC.PHANTOM)


# ---- 2. built-in vs dongle, and the recovery each gets ---------------------

print("\n-- built-in vs dongle --")

check("removal policy 1 (ExpectNoRemoval) means built in",
      RC.is_builtin({"removal_policy": 1, "serial": ""}))
check("removal policy 3 (surprise removal) means it has a plug",
      not RC.is_builtin({"removal_policy": 3, "serial": "ACA7F1299FCB"}))
check("with no removal policy the serial decides: none = built in",
      RC.is_builtin({"removal_policy": None, "serial": ""}))
check("with no removal policy a serial means a dongle",
      not RC.is_builtin({"removal_policy": None, "serial": "ACA7F1299FCB"}))

builtin_advice = RC.recovery_for({"removal_policy": 1, "serial": ""})
check("the built-in radio's recovery says restart Windows",
      "restart Windows" in builtin_advice)
check("and says it must be with NO VM CAPTURES HELD -- a bare restart re-runs "
      "the same race and is what produced this state twice",
      "NO VM CAPTURES HELD" in builtin_advice)
check("a dongle is told to replug, not to restart",
      "plug it back in" in RC.recovery_for({"removal_policy": 3,
                                            "serial": "AC"})
      and "restart Windows" not in RC.recovery_for({"removal_policy": 3,
                                                    "serial": "AC"}))


# ---- 3. custody lines ------------------------------------------------------

print("\n-- the custody line per radio --")

line = RC.custody_line({"label": "Intel Corp.", "verdict": RC.WINDOWS_OWNED,
                        "service": "bthusb", "present": True})
check("Windows-owned names the service and what a bind does and does not do",
      "Windows-owned (bthusb)" in line and "keeps bthusb off it" in line
      and "does not stop the VM-start teardown" in line, line)

line = RC.custody_line({"label": "Intel Corp.", "verdict": RC.CUSTODY})
check("custody reads as a bind, and never promises the race is over",
      "bound to VBoxUSB" in line and "does not" in line
      and "stop the capture race" in line and "✔" not in line, line)

line = RC.custody_line({"label": "Intel Corp.", "verdict": RC.PHANTOM,
                        "vbox_state": "Unavailable", "removal_policy": 1,
                        "serial": ""})
check("PHANTOM says so and carries the built-in recovery",
      line.startswith("Intel Corp.") and "PHANTOM" in line
      and "NO VM CAPTURES HELD" in line, line)

line = RC.custody_line({"label": "a filter", "verdict": RC.ABSENT})
check("ABSENT says no node matches", "ABSENT" in line, line)

line = RC.custody_line({"label": "TP", "verdict": RC.WINDOWS_OWNED,
                        "service": "BTHUSB", "present": False,
                        "proxy_present": True, "vbox_state": "Captured"})
check("a runtime capture is described as runtime, not as custody",
      "RUNTIME" in line and "persistent binding is still Windows" in line,
      line)


# ---- 4. refusals -----------------------------------------------------------

print("\n-- refusals --")

OK_CTX = {"elevated": True, "inf_present": True, "cat_present": True,
          "inf": RC.VBOX_USB_INF, "vm": "OpenSpan-Codex"}
GOOD_ROW = {"verdict": RC.WINDOWS_OWNED, "present": True,
            "proxy_present": False, "label": "r", "instance_id": INTEL_IID}

check("a clean case refuses nothing", RC.take_refusals(GOOD_ROW, OK_CTX) == [],
      str(RC.take_refusals(GOOD_ROW, OK_CTX)))

r = RC.take_refusals(GOOD_ROW, dict(OK_CTX, elevated=False))
check("not elevated is refused", any("Not elevated" in x for x in r))

r = RC.take_refusals(GOOD_ROW, dict(OK_CTX, inf_present=False))
check("a missing VBoxUSB.inf is refused",
      any("VBoxUSB.inf is missing" in x for x in r))

r = RC.take_refusals(GOOD_ROW, dict(OK_CTX, cat_present=False))
check("a missing VBoxUSB.cat is refused",
      any("VBoxUSB.cat is missing" in x for x in r))

r = RC.take_refusals(dict(GOOD_ROW, verdict=RC.PHANTOM, removal_policy=1,
                          serial=""), OK_CTX)
check("a phantom is refused",
      any("PHANTOM" in x for x in r))
check("and the phantom refusal carries the exact built-in recovery",
      any("NO VM CAPTURES HELD" in x for x in r))

r = RC.take_refusals(dict(GOOD_ROW, verdict=RC.PHANTOM, removal_policy=3,
                          serial="ACA7F1299FCB"), OK_CTX)
check("a phantom DONGLE is told to replug instead",
      any("plug it back in" in x for x in r))

r = RC.take_refusals(dict(GOOD_ROW, verdict=RC.ABSENT), OK_CTX)
check("an absent radio is refused", any("no Windows device node" in x
                                        for x in r))

r = RC.take_refusals(dict(GOOD_ROW, verdict=RC.CUSTODY), OK_CTX)
check("a radio already in custody is refused as nothing to do",
      any("Already in EsotericOS custody" in x for x in r))

r = RC.take_refusals(dict(GOOD_ROW, proxy_present=True), OK_CTX)
check("a LIVE VirtualBox runtime capture is refused -- rewriting the driver "
      "under one is the layered ownership that wedged this stack",
      any("RUNTIME capture" in x for x in r))

check("return refuses when the VM is holding the device",
      any("currently has this device attached" in x for x in
          RC.return_refusals(dict(GOOD_ROW, vm_holds=True), OK_CTX)))
check("return refuses when not elevated",
      any("Not elevated" in x for x in
          RC.return_refusals(GOOD_ROW, dict(OK_CTX, elevated=False))))
check("return of a held device is refused even when elevated",
      RC.return_refusals(dict(GOOD_ROW, vm_holds=True), OK_CTX) != [])
check("return of a free, present, elevated device refuses nothing",
      RC.return_refusals(dict(GOOD_ROW, vm_holds=False), OK_CTX) == [])


# ---- 5. the dry-run plan text ---------------------------------------------

print("\n-- dry-run plan text --")

plan = RC.take_plan(GOOD_ROW, OK_CTX)
text = RC.plan_text(plan, {"current_service": "BTHUSB"})
check("the dry run is labelled a DRY RUN", text.startswith("DRY RUN"))
check("it says the read steps really ran", "these ran, for real" in text)
check("it shows what those reads found", "current_service" in text)
check("it says it stopped before the first state change",
      "Stopped before the first state change." in text)
check("the plan names every apply call in order",
      [name for name, _, _ in plan["apply_steps"]] ==
      ["SetupDiSetDeviceRegistryPropertyW", "SetupDiSetDeviceInstallParamsW",
       "SetupDiBuildDriverInfoList", "SetupDiEnumDriverInfo",
       "SetupDiSetSelectedDriverW", "DiInstallDevice"],
      str([n for n, _, _ in plan["apply_steps"]]))
check("the class change names the USB class GUID from VBoxUSB.inf",
      RC.USB_CLASS_GUID in text)
check("the driver-list step names DI_FLAGSEX_ALLOWEXCLUDEDDRVS -- without it "
      "a PnP device's list is empty even with the classes matched",
      "DI_FLAGSEX_ALLOWEXCLUDEDDRVS" in text)
check("and DI_ENUMSINGLEINF, so only VBoxUSB.inf is consulted",
      "DI_ENUMSINGLEINF" in text)

refused = RC.plan_text(RC.take_plan(dict(GOOD_ROW, proxy_present=True),
                                    OK_CTX))
check("a REFUSED dry run still prints the whole plan -- a refusal you cannot "
      "see the shape of is just a no", "DiInstallDevice" in refused)
check("and says plainly that --apply would change nothing",
      "REFUSED" in refused and "would change nothing" in refused)

rplan = RC.return_plan(GOOD_ROW, OK_CTX)
check("the return plan is remove-device then scan-devices, in that order",
      [(n, a[0]) for n, a, _ in rplan["apply_steps"]] ==
      [("pnputil", "/remove-device"), ("pnputil", "/scan-devices")],
      str(rplan["apply_steps"]))


# ---- 6. instance-id matching and proxy pairing -----------------------------

print("\n-- matching a VirtualBox record to a Windows node --")

ids = list(real_nodes())
check("a dongle is matched by its serial, not by arrival order",
      RC.match_instance_id(ids, "0x2357", "0x0604", "ACA7F1299FCB")
      == TPLINK_IID)
check("the built-in radio, which has no serial, is matched by VID/PID prefix",
      RC.match_instance_id(ids, "0x8087", "0x0aaa", "") == INTEL_IID)
check("a vendor with no node matches nothing",
      RC.match_instance_id(ids, "0xdead", "0xbeef", "") is None)
check("the proxy is paired by the SHARED instance-id suffix",
      RC.proxy_for(ids, INTEL_IID) == INTEL_PROXY
      and RC.proxy_for(ids, TPLINK_IID) == TPLINK_PROXY)
check("a real node with no proxy pairs with nothing",
      RC.proxy_for([INTEL_IID], INTEL_IID) is None)


# ---- 7. the audit, end to end against the fake -----------------------------

print("\n-- audit against the recorded machine shape --")

fake = FakeBindings(nodes=real_nodes())
rows = controller(fake).rows()
by_iid = {r["instance_id"]: r for r in rows}
check("both configured radios are reported", len(rows) == 2, str(len(rows)))
check("the Intel picks up its BTHUSB service from the REAL node, not the proxy",
      by_iid[INTEL_IID]["service"] == "BTHUSB")
check("the Intel's removal policy is read off the PROXY when the torn-down "
      "real node will not answer -- that is what identifies a built-in radio",
      by_iid[INTEL_IID]["removal_policy"] == 1)
check("both radios under runtime capture read WINDOWS-OWNED, not PHANTOM",
      all(r["verdict"] == RC.WINDOWS_OWNED for r in rows),
      str([(r["instance_id"], r["verdict"]) for r in rows]))
check("the audit made no state-changing call",
      not any(name in ("set_class_guid", "set_selected_driver",
                       "install_device", "remove_device", "scan_devices",
                       "set_device_driver_path", "build_device_driver_list")
              for name, _ in fake.calls),
      str([n for n, _ in fake.calls]))

wedged = FakeBindings(nodes=real_nodes(intel_proxy=False))
rows = RC.RadioCustody(bindings=wedged,
                       radio_state=state(intel_state="Unavailable"),
                       vm="V").rows()
intel = [r for r in rows if r["instance_id"] == INTEL_IID][0]
check("with the proxy gone and the host state Unavailable, the Intel IS a "
      "phantom", intel["verdict"] == RC.PHANTOM, intel["verdict"])
check("and with no proxy to ask, the serial-less Intel is still called built in",
      RC.is_builtin(intel))

absent = RC.RadioCustody(
    bindings=FakeBindings(nodes={}),
    radio_state={"mine": [], "attached": [], "held": set(),
                 "absent": [{"name": "IntelBT", "vendor": "0x8087",
                             "product_id": "0x0aaa"}]}, vm="V").rows()
check("a filter matching nothing is still reported, as ABSENT",
      len(absent) == 1 and absent[0]["verdict"] == RC.ABSENT)

check("the audit text names each field a person needs",
      all(word in RC.audit_text(rows) for word in
          ("instance id", "present", "service", "VBox host", "verdict")))


# ---- 8. the call ORDER of an apply -----------------------------------------

print("\n-- the apply sequence, in order, against the fake --")

fake = FakeBindings(nodes=real_nodes(intel_proxy=False, intel_present=True))
cust = RC.RadioCustody(bindings=fake, radio_state=state(intel_state="Busy"),
                       vm="V")
row = [r for r in cust.rows() if r["instance_id"] == INTEL_IID][0]
check("with no live proxy and the node present, taking custody is allowed",
      RC.take_refusals(row, cust.context()) == [],
      str(RC.take_refusals(row, cust.context())))

fake.calls.clear()
dry = cust.take(row, apply=False)
mutators = [n for n, _ in fake.calls
            if n in ("set_class_guid", "set_device_driver_path",
                     "build_device_driver_list", "set_selected_driver",
                     "install_device", "remove_device", "scan_devices")]
check("a DRY RUN calls nothing that changes state", mutators == [],
      str(mutators))
check("but it does run the real read-only probe",
      "find_vbox_driver_at_set_level" in [n for n, _ in fake.calls])
check("and it reports it did not apply", dry["applied"] is False)
check("the dry run found the Oracle driver node and says which",
      dry["probe"]["driver_nodes_in_VBoxUSB.inf"] ==
      ["VirtualBox USB Driver / Oracle Corporation / 7.2.12.24389"])

fake.calls.clear()
done = cust.take(row, apply=True)
order = [n for n, _ in fake.calls
         if n in ("set_class_guid", "set_device_driver_path",
                  "build_device_driver_list", "set_selected_driver",
                  "install_device")]
check("the class change comes FIRST -- the device's driver list is filtered "
      "by its own class, so nothing can be selected before it",
      order[0] == "set_class_guid" if order else False, str(order))
check("the full order is class, install params, build list, select, install",
      order == ["set_class_guid", "set_device_driver_path",
                "build_device_driver_list", "set_selected_driver",
                "install_device"], str(order))
class_args = [a for n, a in fake.calls if n == "set_class_guid"]
check("the class it writes is the USB class from VBoxUSB.inf",
      class_args and class_args[0][1] == RC.USB_CLASS_GUID, str(class_args))
path_args = [a for n, a in fake.calls if n == "set_device_driver_path"]
check("the INF it points at is VBoxUSB.inf",
      path_args and path_args[0][1].endswith("VBoxUSB.inf"), str(path_args))
check("an applied take reports applied and ok", done["applied"] and done["ok"])
check("and reports whether a restart is needed",
      done["reboot_required"] is False)

fake.set_class_ok = False
fake.calls.clear()
stopped = cust.take(row, apply=True)
after = [n for n, _ in fake.calls
         if n in ("set_device_driver_path", "build_device_driver_list",
                  "set_selected_driver", "install_device")]
check("a refused class change STOPS the sequence -- nothing is installed onto "
      "a device still in the wrong class", after == [], str(after))
check("and it says the class change is the step that failed",
      "class change was refused" in stopped["error"], stopped["error"])

missing = FakeBindings(nodes=real_nodes(intel_proxy=False,
                                        intel_present=True),
                       device_drivers=[])
cust2 = RC.RadioCustody(bindings=missing, radio_state=state("Busy"), vm="V")
row2 = [r for r in cust2.rows() if r["instance_id"] == INTEL_IID][0]
res = cust2.take(row2, apply=True)
check("if the Oracle node is still absent after the class change, nothing is "
      "installed", "not in the device's driver list" in res["error"],
      res["error"])
check("and DiInstallDevice was never reached",
      "install_device" not in [n for n, _ in missing.calls])


# ---- 9. the return path ----------------------------------------------------

print("\n-- return --")

fake = FakeBindings(nodes=real_nodes(intel_proxy=False, intel_present=True))
cust = RC.RadioCustody(bindings=fake, radio_state=state(intel_state="Busy"),
                       vm="V")
row = [r for r in cust.rows() if r["instance_id"] == INTEL_IID][0]

fake.calls.clear()
dry = cust.give_back(row, apply=False)
check("a return DRY RUN removes nothing and rescans nothing",
      not any(n in ("remove_device", "scan_devices")
              for n, _ in fake.calls))
fake.calls.clear()
cust.give_back(row, apply=True)
check("an applied return is remove then scan, in that order",
      [n for n, _ in fake.calls
       if n in ("remove_device", "scan_devices")]
      == ["remove_device", "scan_devices"], str(fake.calls))

held = RC.RadioCustody(bindings=FakeBindings(nodes=real_nodes(
    intel_proxy=False, intel_present=True)),
    radio_state=state(intel_state="Busy", held=["u-intel"]), vm="V")
row3 = [r for r in held.rows() if r["instance_id"] == INTEL_IID][0]
check("a device the VM is holding is seen as held", row3["vm_holds"])
result = held.give_back(row3, apply=True)
check("and returning it is refused, with nothing run",
      result["plan"]["refusals"] and not result["applied"])


# ---- 10. AST: no state change is reachable without apply -------------------

print("\n-- AST: apply gating --")

MODULE = pathlib.Path(RC.__file__)
source = MODULE.read_text(encoding="utf-8")
tree = ast.parse(source)

# The four bindings methods that change the machine, plus the two that write
# install parameters onto a real device. Anything here must sit inside an `if`
# whose test mentions `apply`.
STATE_CHANGING = {"set_class_guid", "set_device_driver_path",
                  "build_device_driver_list", "set_selected_driver",
                  "install_device", "remove_device", "scan_devices"}


def guarded_by_apply(tree, target):
    """Every call to `target` that is NOT lexically inside an `if apply:`."""
    unguarded = []

    def walk(node, inside):
        if isinstance(node, ast.If):
            names = {n.id for n in ast.walk(node.test)
                     if isinstance(n, ast.Name)}
            for child in node.body:
                walk(child, inside or ("apply" in names))
            for child in node.orelse:
                walk(child, inside)
            return
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == target and not inside):
            unguarded.append(node.lineno)
        for child in ast.iter_child_nodes(node):
            walk(child, inside)

    walk(tree, False)
    return unguarded


for name in sorted(STATE_CHANGING):
    # The definitions on Win32Bindings itself are declarations, not calls, so
    # only call sites are examined.
    loose = guarded_by_apply(tree, name)
    check(f"every call to {name}() is inside an `if apply:`", loose == [],
          f"unguarded at line(s) {loose}")

check("pnputil is only ever INVOKED from the two bindings methods -- the other "
      "mentions of it are the plan text, which is printed, not run",
      source.count("[PNPUTIL,") == 2, str(source.count("[PNPUTIL,")))
check("--apply defaults to off",
      "\"--apply\", action=\"store_true\"" in source)
check("both take() and give_back() default apply to False",
      source.count("def take(self, row, apply=False)") == 1
      and source.count("def give_back(self, row, apply=False)") == 1)

names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
check("the pure decision functions exist separately from the Win32 edge",
      {"verdict", "custody_line", "take_plan", "return_plan", "plan_text",
       "take_refusals", "return_refusals"} <= names)
check("there is a Win32Bindings class the tests can fake, as on_desktop has",
      any(isinstance(n, ast.ClassDef) and n.name == "Win32Bindings"
          for n in ast.walk(tree)))


# ---- 11. AST: the app side -------------------------------------------------

print("\n-- AST: the Bluetooth panel wiring --")

APP = MODULE.parent / "openspan.py"
app_source = APP.read_text(encoding="utf-8")
app_tree = ast.parse(app_source)

check("the panel has a custody status line", "radio_custody_text" in app_source)
check("there is exactly ONE custody button",
      app_source.count("self.custody_btn = ttk.Button") == 1,
      str(app_source.count("self.custody_btn = ttk.Button")))
check("its command is the take-custody handler",
      "command=self._take_custody" in app_source)

funcs = {n.name: n for n in ast.walk(app_tree)
         if isinstance(n, ast.FunctionDef)}
for name in ("_take_custody", "_custody_check"):
    check(f"{name} exists", name in funcs)
    if name in funcs:
        threads = [n for n in ast.walk(funcs[name])
                   if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "Thread"]
        check(f"{name} does its work on a worker thread", len(threads) == 1,
              f"{len(threads)} Thread() calls")

take_fn = funcs.get("_take_custody")
if take_fn:
    src = ast.get_source_segment(app_source, take_fn) or ""
    check("the first click is a dry run and the second applies -- apply is "
          "bound to the armed flag, never hardcoded True",
          "apply=armed" in src and "apply=True" not in src, src[:0])
    check("the plan is shown in-window, in the console",
          "plan_text" in src and "_log" in src)

check("no custody path opens a Toplevel",
      "_take_custody" in app_source
      and not any(seg for seg in (ast.get_source_segment(app_source, f) or ""
                                  for n, f in funcs.items()
                                  if n.startswith("_custody")
                                  or n == "_take_custody")
                  if "Toplevel" in seg or "messagebox" in seg))

check("the launch audit is scheduled and only reports",
      "self.after(1800, self._custody_check)" in app_source)
audit_fn = funcs.get("_custody_check")
if audit_fn:
    src = ast.get_source_segment(app_source, audit_fn) or ""
    check("the launch audit only reports: it never takes custody",
          ".take(" not in src and "apply=True" not in src
          and "give_back" not in src, src[:0])


# ---- 12. bake-in.ps1 and the docs ------------------------------------------

print("\n-- install/uninstall path and docs --")

BAKE = MODULE.parent.parent / "bake-in.ps1"
bake = BAKE.read_text(encoding="utf-8")
check("bake-in.ps1 has a -Custody switch", "[switch]$Custody" in bake)
check("-Custody runs the audit", "audit" in bake)
check("-Undo prints the return commands", "$Verb -eq 'return'" in bake)
check("bake-in.ps1 never passes --apply itself",
      "--apply'" not in bake.replace('"', "'").replace("  ", " ")
      or "Nothing above has been run" in bake)
check("it says custody is never automatic",
      "NEVER AUTOMATIC" in bake.upper())
check("it tells Doug to do a TP-Link first",
      "TP-Link" in bake and "FIRST" in bake)

DOC = MODULE.parent.parent / "docs" / "RADIO-CUSTODY.md"
check("docs/RADIO-CUSTODY.md exists", DOC.exists())
if DOC.exists():
    doc = DOC.read_text(encoding="utf-8")
    for needle in ("SetupDiSetDeviceRegistryProperty", "DiInstallDevice",
                   "DI_FLAGSEX_ALLOWEXCLUDEDDRVS", "DI_ENUMSINGLEINF",
                   "NO VM CAPTURES HELD", "learn.microsoft.com",
                   "DeviceInstall", "TP-Link"):
        check(f"the doc covers {needle}", needle in doc)


if failures:
    print(f"\nRESULT: {len(failures)} FAILED")
    raise SystemExit(1)
print("\nRESULT: ALL RADIO-CUSTODY TESTS PASSED")
