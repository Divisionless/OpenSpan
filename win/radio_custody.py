"""Radio custody: EsotericOS takes a Bluetooth radio off Windows, permanently.

THE WEDGE
---------
Every VM start, VBoxUSBMon.sys arms a filter, tears the live `bthusb` device
stack down, and re-adds the device as a VirtualBox proxy -- it makes PnP see the
fake hardware id ``USB\\VID_80EE&PID_CAFE`` so Oracle's VBoxUSB.sys binds. That
is a race, run once per VM start, against a device Windows is actively using.

On a dongle the race is survivable: it loses, you replug, the fresh arrival is a
new device object and the armed filter takes it. On the ONBOARD Intel radio
there is no plug. When the re-add never completes, the node is left `present =
False` -- a PnP phantom -- and no command re-enumerates it. That is the state
this machine has reached repeatedly (DEVLOG 2026-08-08/09, and again today).

THE DESIGN
----------
Doug: *"exclusively taking control of this from Windows until the program is
uninstalled."*

Stop running the race. Bind VBoxUSB.sys as the FUNCTION DRIVER of the radio's
REAL device node, permanently, in the registry -- the Device Manager
"Have Disk / Let me pick / show all models" install, done programmatically. Then
PnP loads the VBox proxy at boot, `bthusb` never touches the radio, and there is
no runtime capture teardown to lose.

Return = uninstall the device node and rescan, so the vendor driver comes back.

WHY IT NEEDS A CLASS CHANGE (all four facts probed on this machine)
------------------------------------------------------------------
* ``SetupDiOpenDeviceInfoW`` on the Intel instance id in a class-less set works.
  Phantom nodes are openable. Good -- everything else can proceed.
* ``SetupDiBuildDriverInfoList(SPDIT_COMPATDRIVER)`` with DI_ENUMSINGLEINF on
  VBoxUSB.inf -> **0 nodes**. Expected: the INF's only hardware id is
  ``USB\\VID_80EE&PID_CAFE`` and the radio's is ``USB\\VID_8087&PID_0AAA``.
* ``SetupDiBuildDriverInfoList(SPDIT_CLASSDRIVER)`` on the DEVICE -> **0 nodes**,
  even with DI_FLAGSEX_ALLOWEXCLUDEDDRVS. This is the whole obstacle, and it is
  documented: *"If the driver list is associated with a device instance (that
  is, DeviceInfoData is specified), the resulting list is composed of drivers
  that have the same class as the device instance with which they are
  associated."* The radio's class is Bluetooth; the INF's class is USB.
* The same call at SET level on a set created for the USB class GUID -> **1
  node**, `VirtualBox USB Driver`, Oracle Corporation, 7.2.12.24389. The driver
  node is right there. Only the class filter hides it from the device.
* ``SetupDiOpenDeviceInfoW`` of the Bluetooth-class device INTO a USB-class set
  fails ERROR_CLASS_MISMATCH (0xE0000201), so "just use the other set" is out.

So the device's class is set to USB first, and only then is the device's own
driver list built. See ``docs/RADIO-CUSTODY.md`` for the citations.

SHAPE OF THIS FILE
------------------
Pure decision functions first, then one ``Win32Bindings`` object holding every
OS call, exactly as ``on_desktop.py`` does -- so the tests drive the whole
sequence against a recording fake and never touch a real device node.

**Every state-changing call is lexically inside an ``if apply:``.** The default
is a dry run that executes each read-only step for real and then PRINTS the
apply calls with their arguments. ``win\\test_radio_custody.py`` asserts that
property against the AST, so it cannot rot.
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


# ---- what we are binding ---------------------------------------------------

VBOX_DIR = r"C:\Program Files\Oracle\VirtualBox"
VBOX_USB_INF = os.path.join(VBOX_DIR, "drivers", "USB", "device", "VBoxUSB.inf")
VBOX_USB_CAT = os.path.join(VBOX_DIR, "drivers", "USB", "device", "VBoxUSB.cat")
VBOX_MANAGE = os.path.join(VBOX_DIR, "VBoxManage.exe")

# VBoxUSB.inf declares Class=USB / ClassGUID={36FC9E60-...}. Its driver node is
# only visible to a device whose own class is the same one.
USB_CLASS_GUID = "{36FC9E60-C465-11CF-8056-444553540000}"
VBOX_DRIVER_DESC = "VirtualBox USB Driver"
VBOX_SERVICE = "VBoxUSB"
WINDOWS_BT_SERVICE = "BTHUSB"

PNPUTIL = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                       "System32", "pnputil.exe")

# ---- verdicts --------------------------------------------------------------

WINDOWS_OWNED = "WINDOWS-OWNED"
CUSTODY = "ESOTERICOS-CUSTODY"
PHANTOM = "PHANTOM"
ABSENT = "ABSENT"

# The one recovery for a wedged BUILT-IN radio, said exactly. A plain "restart
# Windows" is what produced this state twice: the restart re-runs the same
# boot-time race with the VM's captures still armed. The captures have to be
# gone first.
BUILTIN_RECOVERY = (
    "restart Windows WITH NO VM CAPTURES HELD -- power the VM off, confirm "
    "`VBoxManage list usbhost` shows no radio Captured, and only then restart.")
DONGLE_RECOVERY = (
    "unplug it and plug it back in -- a fresh arrival is a new device object "
    "and it enumerates cleanly.")


# ============================================================================
# PURE DECISION FUNCTIONS -- no ctypes, no subprocess, no filesystem.
# ============================================================================

# VirtualBox's own vendor:product. While VBoxUSBMon holds a runtime capture,
# the REAL device node is torn down (present = False) and a sibling node under
# this id stands in for it, bound to VBoxUSB. Probed 2026-08-16: all three
# radios on this desk were in exactly that shape.
VBOX_PROXY_PREFIX = "USB\\VID_80EE&PID_CAFE\\"

# The VirtualBox host states in which the device object is usable. "Unavailable"
# is the one that is not: it is what a wedged radio reports while its proxy node
# still sits there looking healthy.
VBOX_USABLE = ("busy", "available", "captured", "held")


def verdict(present, service, vbox_state, node_exists=True,
            proxy_present=False):
    """Who owns this radio, from the facts and nothing else.

    The naive reading -- "real node present = False means phantom" -- is WRONG,
    and probing this machine is what proved it. A perfectly healthy dongle
    under runtime VirtualBox capture ALSO reports present = False on its real
    node, because VBoxUSBMon tore that node down and stood a
    ``USB\\VID_80EE&PID_CAFE\\<same suffix>`` proxy up in its place. On
    2026-08-16 all three radios read present = False; two of them were working.

    What separates them is VirtualBox's own host state. Both TP-Links read
    `Captured` (proxy alive, device usable, VM holding them). The onboard Intel
    read `Unavailable` (proxy alive, device NOT usable) -- that is the wedge,
    and that is the phantom worth the name.

    ``node_exists`` is False when Windows has no node for the radio at all.
    """
    if not node_exists:
        return ABSENT
    if present:
        if str(service or "").strip().lower() == VBOX_SERVICE.lower():
            return CUSTODY
        return WINDOWS_OWNED
    # Real node torn down. A live proxy plus a usable host state is the normal
    # runtime capture -- Windows still owns the PERSISTENT binding (bthusb is
    # still the real node's service), so the race will run again next boot.
    if proxy_present and str(vbox_state or "").strip().lower() in VBOX_USABLE:
        return WINDOWS_OWNED
    return PHANTOM


def is_builtin(row):
    """Whether this radio has a plug a human can pull.

    SPDRP_REMOVAL_POLICY is the device's own statement: 1 = ExpectNoRemoval,
    2/3 = orderly/surprise removal. When the property cannot be read (it often
    cannot on a phantom) the fallback is the serial: the TP-Link dongles report
    twelve hex digits, the onboard Intel reports none.
    """
    policy = row.get("removal_policy")
    if policy in (1,):
        return True
    if policy in (2, 3):
        return False
    return not str(row.get("serial") or "").strip()


def recovery_for(row):
    """The one physical action that un-wedges this particular radio."""
    return BUILTIN_RECOVERY if is_builtin(row) else DONGLE_RECOVERY


def custody_line(row):
    """One sentence for the Bluetooth panel, per radio."""
    label = row.get("label") or row.get("instance_id") or "this radio"
    state = row.get("verdict")
    if state == CUSTODY:
        return f"{label} \u2014 in EsotericOS custody (VBoxUSB) \u2714"
    if state == PHANTOM:
        return (f"{label} \u2014 PHANTOM: the device node is registered but "
                f"not enumerated and VirtualBox reports "
                f"\u201c{row.get('vbox_state') or 'nothing'}\u201d, so nothing "
                f"can bind to it. To recover, {recovery_for(row)}")
    if state == ABSENT:
        return (f"{label} \u2014 ABSENT: no Windows device node matches this "
                f"radio. Check that it is plugged in.")
    service = row.get("service") or "no driver"
    tail = ("; EsotericOS custody would stop the capture race")
    if row.get("proxy_present") and not row.get("present"):
        return (f"{label} \u2014 Windows-owned ({service}), under a RUNTIME "
                f"VirtualBox capture right now (host state "
                f"\u201c{row.get('vbox_state')}\u201d). The persistent binding "
                f"is still Windows', so the race runs again at every VM "
                f"start{tail}")
    return f"{label} \u2014 Windows-owned ({service}){tail}"


def take_refusals(row, ctx):
    """Every reason NOT to bind, in the order a person should hear them.

    A refusal is a sentence naming the one thing that has to change. This is
    the whole safety surface of ``take``: an empty list is the only thing that
    lets an apply run.
    """
    out = []
    if not ctx.get("elevated"):
        out.append("Not elevated. Binding a driver needs Administrator; "
                   "SetupDiSetDeviceRegistryProperty and DiInstallDevice both "
                   "require it (DiInstallDevice returns ERROR_ACCESS_DENIED).")
    if not ctx.get("inf_present"):
        out.append(f"VBoxUSB.inf is missing at {VBOX_USB_INF}. There is "
                   "nothing to bind. Reinstall VirtualBox.")
    if not ctx.get("cat_present"):
        out.append(f"VBoxUSB.cat is missing at {VBOX_USB_CAT}. Windows will "
                   "refuse an unsigned driver package. Reinstall VirtualBox.")
    state = row.get("verdict")
    if state == ABSENT:
        out.append("There is no Windows device node for this radio. Nothing "
                   "to bind to.")
    elif state == PHANTOM:
        out.append("The node is a PHANTOM (present = False): it is registered "
                   "but not enumerated, so PnP will not load any driver onto "
                   "it and the bind would be written into a node that never "
                   "starts. To recover, " + recovery_for(row))
    elif state == CUSTODY:
        out.append("Already in EsotericOS custody (VBoxUSB is the function "
                   "driver). Nothing to do.")
    if row.get("proxy_present"):
        out.append(
            "VirtualBox is holding a RUNTIME capture on this radio right now "
            f"({VBOX_PROXY_PREFIX}… is present and bound to VBoxUSB). "
            "Rewriting the real node's driver underneath a live capture is "
            "precisely the layered ownership that wedged this stack before. "
            "Power the VM off first, confirm `VBoxManage list usbhost` shows "
            "the radio no longer Captured, and take custody then.")
    return out


def return_refusals(row, ctx):
    """Every reason NOT to hand a radio back."""
    out = []
    if not ctx.get("elevated"):
        out.append("Not elevated. Removing a device node needs Administrator.")
    if row.get("verdict") == ABSENT:
        out.append("There is no Windows device node for this radio. Nothing "
                   "to remove.")
    if row.get("vm_holds"):
        out.append(f"The VM \u201c{ctx.get('vm') or '?'}\u201d currently has "
                   "this device attached. Removing the node underneath a live "
                   "passthrough is how the stack got wedged in the first "
                   "place. Detach it in the VM (or power the VM off) first.")
    return out


def take_plan(row, ctx):
    """The full sequence, read steps and apply steps, as data.

    Read steps are executed for real by the dry run. Apply steps are only ever
    PRINTED unless --apply was passed. Keeping them in one ordered list is what
    makes the dry run an honest rehearsal rather than a separate story.
    """
    iid = row.get("instance_id") or "<no node>"
    inf = ctx.get("inf") or VBOX_USB_INF
    read = [
        ("SetupDiCreateDeviceInfoList", ["ClassGuid=NULL", "hwndParent=NULL"],
         "a class-LESS set: the only kind that will accept a device whose "
         "class does not match"),
        ("SetupDiOpenDeviceInfoW", [iid, "OpenFlags=0"],
         "phantom nodes are openable; this is what makes the audit possible "
         "at all"),
        ("SetupDiGetDeviceRegistryPropertyW", [iid, "SPDRP_CLASSGUID"],
         "the class that is currently hiding the Oracle driver node"),
        ("SetupDiGetDeviceRegistryPropertyW", [iid, "SPDRP_SERVICE"],
         "who the function driver is right now"),
        ("SetupDiGetDeviceRegistryPropertyW", [iid, "SPDRP_DEVICEDESC"], ""),
        ("CM_Locate_DevNodeW", [iid, "CM_LOCATE_DEVNODE_NORMAL"],
         "CR_NO_SUCH_DEVINST here is exactly what 'phantom' means"),
        ("SetupDiGetClassDevsW", [f"ClassGuid={USB_CLASS_GUID}", "Flags=0"],
         "a second, USB-class set, used only to PROVE the driver node exists"),
        ("SetupDiBuildDriverInfoList", ["DeviceInfoData=NULL",
                                        "SPDIT_CLASSDRIVER",
                                        "DI_ENUMSINGLEINF",
                                        f"DriverPath={inf}"],
         "at SET level this finds 'VirtualBox USB Driver'; at DEVICE level it "
         "finds nothing until the class below is changed"),
        ("SetupDiEnumDriverInfo", ["SPDIT_CLASSDRIVER", "MemberIndex=0.."],
         "read the description / provider / version of what would be bound"),
    ]
    apply = [
        ("SetupDiSetDeviceRegistryPropertyW",
         [iid, "SPDRP_CLASSGUID", USB_CLASS_GUID],
         "THE class change. Documented: 'If the ClassGuid property is set, "
         "DeviceInfoData.ClassGuid is set upon return to the new class for "
         "the device' and 'When the ClassGUID property changes, "
         "SetupDiSetDeviceRegistryProperty automatically cleans up any "
         "software keys associated with the device.' After this the device "
         "and VBoxUSB.inf share a class."),
        ("SetupDiSetDeviceInstallParamsW",
         [iid, "Flags |= DI_ENUMSINGLEINF",
          "FlagsEx |= DI_FLAGSEX_ALLOWEXCLUDEDDRVS", f"DriverPath={inf}"],
         "single INF only, and ALLOWEXCLUDEDDRVS because 'Drivers for PnP "
         "devices are typically Exclude From Select' -- without it the list "
         "is empty even with the classes matched"),
        ("SetupDiBuildDriverInfoList",
         [iid, "SPDIT_CLASSDRIVER"],
         "now at DEVICE level. The class filter that returned 0 nodes before "
         "the change should now return the Oracle node"),
        ("SetupDiEnumDriverInfo",
         [iid, "SPDIT_CLASSDRIVER", f"pick Description == {VBOX_DRIVER_DESC!r}"],
         "one SP_DRVINFO_DATA_V2_W identifying exactly what to install"),
        ("SetupDiSetSelectedDriverW",
         [iid, f"DrvInfo({VBOX_DRIVER_DESC})"],
         "the programmatic half of clicking the driver in the Have Disk list"),
        ("DiInstallDevice",
         ["hwndParent=NULL", iid, f"DrvInfo({VBOX_DRIVER_DESC})", "Flags=0",
          "NeedReboot=&out"],
         "newdev.dll. Installs a specific driver on a specific device -- the "
         "one API documented for that ('Only call DiInstallDevice if it is "
         "necessary to install a specific driver on a specific device'). "
         "The driver must already be in the driver store, which it is: "
         "VirtualBox put it there and the two TP-Links are bound to it now. "
         "NeedReboot is read rather than left NULL so nothing pops a dialog."),
    ]
    return {
        "verb": "take",
        "instance_id": iid,
        "label": row.get("label") or iid,
        "refusals": take_refusals(row, ctx),
        "read_steps": read,
        "apply_steps": apply,
    }


def return_plan(row, ctx):
    """Give the radio back: uninstall the node, then rescan."""
    iid = row.get("instance_id") or "<no node>"
    read = [
        ("SetupDiCreateDeviceInfoList", ["ClassGuid=NULL"], ""),
        ("SetupDiOpenDeviceInfoW", [iid, "OpenFlags=0"], ""),
        ("SetupDiGetDeviceRegistryPropertyW", [iid, "SPDRP_SERVICE"],
         "confirm VBoxUSB is what is bound before removing it"),
        ("VBoxManage", ["showvminfo", ctx.get("vm") or "?",
                        "--machinereadable"],
         "USBAttachActive: refuse if the VM is holding this device right now"),
    ]
    apply = [
        ("pnputil", ["/remove-device", iid],
         "uninstalls the device node and its driver binding. The custody "
         "registry state goes with it."),
        ("pnputil", ["/scan-devices"],
         "PnP re-enumerates, finds the real hardware id again, and the vendor "
         "driver (Intel Wireless Bluetooth / bthusb) binds as it did before."),
    ]
    return {
        "verb": "return",
        "instance_id": iid,
        "label": row.get("label") or iid,
        "refusals": return_refusals(row, ctx),
        "read_steps": read,
        "apply_steps": apply,
    }


def _fmt_step(index, step):
    name, args, note = step
    line = f"  {index:>2}. {name}({', '.join(str(a) for a in args)})"
    if note:
        line += "\n      \u2014 " + note
    return line


def plan_text(plan, probe=None):
    """The dry run, printed. This is what the button shows in-window."""
    refused = bool(plan["refusals"])
    out = [f"DRY RUN \u2014 {plan['verb']} custody of {plan['label']}",
           f"  device: {plan['instance_id']}"]
    out.append("")
    out.append("Read-only steps (these ran, for real, just now):")
    out.extend(_fmt_step(i, s) for i, s in enumerate(plan["read_steps"], 1))
    if probe:
        out.append("")
        out.append("What those reads found:")
        for key in sorted(probe):
            out.append(f"    {key} = {probe[key]!r}")
    out.append("")
    # The plan is printed even when refused. A refusal you cannot see the shape
    # of is just a "no", and the whole point of a dry run is to be readable.
    header = ("WOULD CALL, IN THIS ORDER \u2014 but see REFUSED below; --apply "
              "would change nothing:" if refused else
              "Would then call, IN THIS ORDER (not run \u2014 pass --apply):")
    out.append(header)
    out.extend(_fmt_step(i, s) for i, s in enumerate(plan["apply_steps"], 1))
    out.append("")
    if refused:
        out.append("REFUSED. Nothing would be changed:")
        out.extend("  \u00b7 " + reason for reason in plan["refusals"])
    else:
        out.append("Stopped before the first state change.")
    return "\n".join(out)


def audit_text(rows):
    """The whole audit as lines a person reads top to bottom."""
    if not rows:
        return ("No radios are configured. The VM has no active USB filters, "
                "so there is nothing for EsotericOS to take custody of.")
    out = []
    for row in rows:
        out.append(custody_line(row))
        out.append(f"    instance id : {row.get('instance_id') or '(none)'}")
        out.append(f"    present     : {row.get('present')}")
        out.append(f"    service     : {row.get('service') or '(none)'}")
        out.append(f"    driver      : {row.get('driver_desc') or '(none)'}")
        out.append("    VBox host   : "
                   + (row.get("vbox_state") or "(not listed)"))
        out.append("    VBox proxy  : "
                   + (f"{row['proxy_instance_id']} (present="
                      f"{row.get('proxy_present')})"
                      if row.get("proxy_instance_id") else "(none)"))
        out.append(f"    VM holds it : {row.get('vm_holds')}")
        out.append(f"    verdict     : {row.get('verdict')}")
        out.append("")
    return "\n".join(out).rstrip()


# ============================================================================
# THE WIN32 EDGE -- one swappable object, like on_desktop.Win32Bindings.
# ============================================================================

MAX_PATH = 260
LINE_LEN = 256

DIGCF_PRESENT = 0x02
DIGCF_ALLCLASSES = 0x04

SPDIT_CLASSDRIVER = 0x01
SPDIT_COMPATDRIVER = 0x02

DI_ENUMSINGLEINF = 0x00010000
DI_FLAGSEX_ALLOWEXCLUDEDDRVS = 0x00000800

SPDRP_DEVICEDESC = 0x00
SPDRP_SERVICE = 0x04
SPDRP_CLASSGUID = 0x08
SPDRP_MFG = 0x0B
SPDRP_REMOVAL_POLICY = 0x1F

CM_LOCATE_DEVNODE_NORMAL = 0x00000000
CM_LOCATE_DEVNODE_PHANTOM = 0x00000001
CR_SUCCESS = 0x00000000

NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("ClassGuid", GUID),
                ("DevInst", wt.DWORD), ("Reserved", ctypes.c_void_p)]


class SP_DEVINSTALL_PARAMS_W(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("Flags", wt.DWORD),
                ("FlagsEx", wt.DWORD), ("hwndParent", wt.HWND),
                ("InstallMsgHandler", ctypes.c_void_p),
                ("InstallMsgHandlerContext", ctypes.c_void_p),
                ("FileQueue", ctypes.c_void_p),
                ("ClassInstallReserved", ctypes.c_void_p),
                ("Reserved", wt.DWORD),
                ("DriverPath", ctypes.c_wchar * MAX_PATH)]


class SP_DRVINFO_DATA_V2_W(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("DriverType", wt.DWORD),
                ("Reserved", ctypes.c_void_p),
                ("Description", ctypes.c_wchar * LINE_LEN),
                ("MfgName", ctypes.c_wchar * LINE_LEN),
                ("ProviderName", ctypes.c_wchar * LINE_LEN),
                ("DriverDate", wt.FILETIME),
                ("DriverVersion", ctypes.c_ulonglong)]


class Win32Bindings:
    """Every OS call radio custody makes, in one object the tests can fake.

    Read methods are safe to call at any time. The four methods that change
    system state -- ``set_class_guid``, ``set_selected_driver``,
    ``install_device`` and ``remove_and_rescan`` -- are only ever reached from
    inside an ``if apply:`` in the controller below.
    """

    def __init__(self):
        self.setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
        self.cfgmgr = ctypes.WinDLL("cfgmgr32", use_last_error=True)
        self.newdev = None          # loaded lazily: only apply needs it
        self._sets = []

    # -- environment ------------------------------------------------------
    def is_elevated(self):
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # noqa: BLE001
            return False

    def file_exists(self, path):
        return os.path.isfile(path)

    def run(self, argv, timeout=60):
        """One subprocess contract. Returns (returncode, combined output)."""
        try:
            r = subprocess.run(argv, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=timeout, creationflags=NO_WINDOW)
            return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
        except Exception as exc:  # noqa: BLE001
            return 1, str(exc)

    # -- enumerating the USB tree (phantoms included) ---------------------
    def usb_instance_ids(self):
        """Every device node under the USB enumerator, phantoms included.

        DIGCF_PRESENT is deliberately NOT passed: the whole point is to see the
        node that is registered but not enumerated.
        """
        api = self.setupapi
        api.SetupDiGetClassDevsW.restype = ctypes.c_void_p
        handle = api.SetupDiGetClassDevsW(None, "USB", None, DIGCF_ALLCLASSES)
        if handle in (None, -1, 0xFFFFFFFFFFFFFFFF):
            return []
        found = []
        try:
            info = SP_DEVINFO_DATA()
            info.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            index = 0
            while api.SetupDiEnumDeviceInfo(ctypes.c_void_p(handle), index,
                                            ctypes.byref(info)):
                buf = ctypes.create_unicode_buffer(512)
                need = wt.DWORD(0)
                if api.SetupDiGetDeviceInstanceIdW(
                        ctypes.c_void_p(handle), ctypes.byref(info), buf,
                        512, ctypes.byref(need)):
                    found.append(buf.value)
                index += 1
        finally:
            api.SetupDiDestroyDeviceInfoList(ctypes.c_void_p(handle))
        return found

    # -- one device -------------------------------------------------------
    def open_device(self, instance_id):
        """(set_handle, SP_DEVINFO_DATA) for one instance id, or (None, None).

        The set is created CLASS-LESS. A set created for the USB class refuses
        a Bluetooth-class device with ERROR_CLASS_MISMATCH (0xE0000201), which
        is exactly the wall this whole module exists to get around.
        """
        api = self.setupapi
        api.SetupDiCreateDeviceInfoList.restype = ctypes.c_void_p
        handle = api.SetupDiCreateDeviceInfoList(None, None)
        if handle in (None, -1, 0xFFFFFFFFFFFFFFFF):
            return None, None
        info = SP_DEVINFO_DATA()
        info.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
        ok = api.SetupDiOpenDeviceInfoW(ctypes.c_void_p(handle), instance_id,
                                        None, 0, ctypes.byref(info))
        if not ok:
            api.SetupDiDestroyDeviceInfoList(ctypes.c_void_p(handle))
            return None, None
        self._sets.append(handle)
        return handle, info

    def close_device(self, handle):
        if handle is None:
            return
        try:
            self.setupapi.SetupDiDestroyDeviceInfoList(ctypes.c_void_p(handle))
        except Exception:  # noqa: BLE001
            pass
        if handle in self._sets:
            self._sets.remove(handle)

    def get_property(self, handle, info, prop):
        """One SPDRP_* as text, or "" when the device has not got it."""
        buf = ctypes.create_unicode_buffer(1024)
        need = wt.DWORD(0)
        ok = self.setupapi.SetupDiGetDeviceRegistryPropertyW(
            ctypes.c_void_p(handle), ctypes.byref(info), prop, None,
            ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)), 2048,
            ctypes.byref(need))
        return buf.value if ok else ""

    def get_property_dword(self, handle, info, prop):
        """One SPDRP_* as a DWORD, or None."""
        value = wt.DWORD(0)
        need = wt.DWORD(0)
        ok = self.setupapi.SetupDiGetDeviceRegistryPropertyW(
            ctypes.c_void_p(handle), ctypes.byref(info), prop, None,
            ctypes.cast(ctypes.byref(value),
                        ctypes.POINTER(ctypes.c_ubyte)),
            ctypes.sizeof(value), ctypes.byref(need))
        return value.value if ok else None

    def is_present(self, instance_id):
        """CR_NO_SUCH_DEVINST from a NORMAL locate IS the definition of phantom."""
        devinst = wt.DWORD(0)
        rc = self.cfgmgr.CM_Locate_DevNodeW(ctypes.byref(devinst),
                                            ctypes.c_wchar_p(instance_id),
                                            CM_LOCATE_DEVNODE_NORMAL)
        return rc == CR_SUCCESS

    def node_exists(self, instance_id):
        devinst = wt.DWORD(0)
        rc = self.cfgmgr.CM_Locate_DevNodeW(ctypes.byref(devinst),
                                            ctypes.c_wchar_p(instance_id),
                                            CM_LOCATE_DEVNODE_PHANTOM)
        return rc == CR_SUCCESS

    # -- the Oracle driver node (read-only proof it is there) -------------
    def find_vbox_driver_at_set_level(self, inf_path):
        """Build the USB-class list from VBoxUSB.inf alone and describe it.

        SET level, DeviceInfoData = NULL. This is the call that returns 1 node
        on this machine while the DEVICE-level call returns 0, and the whole
        reason the class change is necessary.
        """
        api = self.setupapi
        guid = self._guid(USB_CLASS_GUID)
        api.SetupDiCreateDeviceInfoList.restype = ctypes.c_void_p
        handle = api.SetupDiCreateDeviceInfoList(ctypes.byref(guid), None)
        if handle in (None, -1, 0xFFFFFFFFFFFFFFFF):
            return []
        try:
            params = SP_DEVINSTALL_PARAMS_W()
            params.cbSize = ctypes.sizeof(SP_DEVINSTALL_PARAMS_W)
            api.SetupDiGetDeviceInstallParamsW(ctypes.c_void_p(handle), None,
                                               ctypes.byref(params))
            params.Flags |= DI_ENUMSINGLEINF
            params.FlagsEx |= DI_FLAGSEX_ALLOWEXCLUDEDDRVS
            params.DriverPath = inf_path
            api.SetupDiSetDeviceInstallParamsW(ctypes.c_void_p(handle), None,
                                               ctypes.byref(params))
            if not api.SetupDiBuildDriverInfoList(ctypes.c_void_p(handle),
                                                  None, SPDIT_CLASSDRIVER):
                return []
            return self._enum_drivers(handle, None)
        finally:
            api.SetupDiDestroyDriverInfoList(ctypes.c_void_p(handle), None,
                                             SPDIT_CLASSDRIVER)
            api.SetupDiDestroyDeviceInfoList(ctypes.c_void_p(handle))

    def _enum_drivers(self, handle, info):
        api, out, index = self.setupapi, [], 0
        while True:
            drv = SP_DRVINFO_DATA_V2_W()
            drv.cbSize = ctypes.sizeof(SP_DRVINFO_DATA_V2_W)
            ref = ctypes.byref(info) if info is not None else None
            if not api.SetupDiEnumDriverInfoW(ctypes.c_void_p(handle), ref,
                                              SPDIT_CLASSDRIVER, index,
                                              ctypes.byref(drv)):
                break
            version = drv.DriverVersion
            out.append({
                "description": drv.Description,
                "mfg": drv.MfgName,
                "provider": drv.ProviderName,
                "version": ".".join(str((version >> shift) & 0xFFFF)
                                    for shift in (48, 32, 16, 0)),
                "_drvinfo": drv,
            })
            index += 1
        return out

    @staticmethod
    def _guid(text):
        guid = GUID()
        ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(text),
                                            ctypes.byref(guid))
        return guid

    # -- VirtualBox host view (read-only) ---------------------------------
    def vbox_usbhost(self):
        return self.run([VBOX_MANAGE, "list", "usbhost"])[1]

    def vbox_vminfo(self, vm):
        return self.run([VBOX_MANAGE, "showvminfo", vm,
                         "--machinereadable"])[1]

    # ---- STATE-CHANGING. Only reached from inside an `if apply:`. -------
    def set_class_guid(self, handle, info, class_guid):
        text = (class_guid + "\0")
        buf = ctypes.create_unicode_buffer(text)
        return bool(self.setupapi.SetupDiSetDeviceRegistryPropertyW(
            ctypes.c_void_p(handle), ctypes.byref(info), SPDRP_CLASSGUID,
            ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)),
            ctypes.sizeof(buf)))

    def set_device_driver_path(self, handle, info, inf_path):
        api = self.setupapi
        params = SP_DEVINSTALL_PARAMS_W()
        params.cbSize = ctypes.sizeof(SP_DEVINSTALL_PARAMS_W)
        api.SetupDiGetDeviceInstallParamsW(ctypes.c_void_p(handle),
                                           ctypes.byref(info),
                                           ctypes.byref(params))
        params.Flags |= DI_ENUMSINGLEINF
        params.FlagsEx |= DI_FLAGSEX_ALLOWEXCLUDEDDRVS
        params.DriverPath = inf_path
        return bool(api.SetupDiSetDeviceInstallParamsW(
            ctypes.c_void_p(handle), ctypes.byref(info),
            ctypes.byref(params)))

    def build_device_driver_list(self, handle, info):
        if not self.setupapi.SetupDiBuildDriverInfoList(
                ctypes.c_void_p(handle), ctypes.byref(info),
                SPDIT_CLASSDRIVER):
            return []
        return self._enum_drivers(handle, info)

    def set_selected_driver(self, handle, info, drvinfo):
        return bool(self.setupapi.SetupDiSetSelectedDriverW(
            ctypes.c_void_p(handle), ctypes.byref(info),
            ctypes.byref(drvinfo)))

    def install_device(self, handle, info, drvinfo):
        """DiInstallDevice. Returns (ok, need_reboot, last_error)."""
        if self.newdev is None:
            self.newdev = ctypes.WinDLL("newdev", use_last_error=True)
        need = wt.BOOL(0)
        ok = self.newdev.DiInstallDevice(None, ctypes.c_void_p(handle),
                                         ctypes.byref(info),
                                         ctypes.byref(drvinfo), 0,
                                         ctypes.byref(need))
        return bool(ok), bool(need.value), ctypes.get_last_error()

    def remove_device(self, instance_id):
        return self.run([PNPUTIL, "/remove-device", instance_id])

    def scan_devices(self):
        return self.run([PNPUTIL, "/scan-devices"])


# ============================================================================
# THE CONTROLLER
# ============================================================================

def _norm(value):
    return str(value or "").strip().upper().removeprefix("0X")


def match_instance_id(instance_ids, vendor, product_id, serial=""):
    """Which Windows node is this VirtualBox record?

    The dongles carry their serial as the last component of the instance id;
    the onboard Intel carries a bus-relative id instead. So a serial match is
    tried first and a prefix match is the fallback -- the same rule
    ``openspan._pnp_kick`` already uses, kept identical on purpose.
    """
    vid, pid = _norm(vendor), _norm(product_id)
    if not vid or not pid:
        return None
    prefix = f"USB\\VID_{vid}&PID_{pid}\\"
    candidates = [iid for iid in instance_ids
                  if iid.upper().startswith(prefix)]
    key = _norm(serial)
    if key:
        for iid in candidates:
            if iid.upper().rsplit("\\", 1)[-1] == key:
                return iid
    return candidates[0] if candidates else None


def proxy_for(instance_ids, real_instance_id):
    """The VBoxUSB proxy node standing in for one real node, if there is one.

    Probed 2026-08-16: the proxy's instance id is the VirtualBox vendor/product
    with the REAL node's last component appended unchanged --
    ``USB\\VID_8087&PID_0AAA\\5&3B2D9A0D&0&14`` is shadowed by
    ``USB\\VID_80EE&PID_CAFE\\5&3B2D9A0D&0&14``, and the dongles' serials pair
    the same way. So the suffix is the join key.
    """
    suffix = str(real_instance_id or "").upper().rsplit("\\", 1)[-1]
    if not suffix:
        return None
    want = (VBOX_PROXY_PREFIX + suffix).upper()
    for iid in instance_ids:
        if iid.upper() == want:
            return iid
    return None


class RadioCustody:
    """Audit, take, and return -- against bindings that can be faked."""

    def __init__(self, bindings=None, radio_state=None, vm=None):
        self._b = bindings or Win32Bindings()
        self._radio_state = radio_state
        self._vm = vm

    # -- inputs -----------------------------------------------------------
    def vm_name(self):
        if self._vm:
            return self._vm
        try:
            import openspan
            return openspan.VM
        except Exception:  # noqa: BLE001
            return "OpenSpan"

    def radio_state(self):
        """The configured radios, read exactly the way read_radio_state does."""
        if self._radio_state is not None:
            return (self._radio_state() if callable(self._radio_state)
                    else self._radio_state)
        import openspan
        return openspan.read_radio_state()

    def context(self):
        b = self._b
        return {
            "elevated": b.is_elevated(),
            "inf": VBOX_USB_INF,
            "inf_present": b.file_exists(VBOX_USB_INF),
            "cat_present": b.file_exists(VBOX_USB_CAT),
            "vm": self.vm_name(),
        }

    # -- the audit --------------------------------------------------------
    def rows(self):
        """One record per radio the app configures. Read-only, always."""
        b = self._b
        state = self.radio_state()
        try:
            import openspan
            label_of = openspan.usb_label
            config = openspan.live_config()
        except Exception:  # noqa: BLE001
            label_of, config = (lambda d, c=None: d.get("name") or "?"), {}

        instance_ids = b.usb_instance_ids()
        held = {str(u).lower() for u in state.get("held", set())}
        for device in state.get("attached", []):
            held.update(str(u).lower()
                        for u in (device.get("uuids") or {device.get("uuid")})
                        if u)

        out = []
        for device in state.get("mine", []):
            iid = match_instance_id(instance_ids, device.get("vendor"),
                                    device.get("product_id"),
                                    device.get("serial"))
            row = {
                "label": label_of(device, config),
                "instance_id": iid,
                "vendor": device.get("vendor"),
                "product_id": device.get("product_id"),
                "serial": device.get("serial") or "",
                "port": device.get("port"),
                "vbox_state": device.get("state") or "",
                "vm_holds": bool(
                    {str(u).lower()
                     for u in (device.get("uuids") or {device.get("uuid")})
                     if u} & held),
                "present": False,
                "service": "",
                "driver_desc": "",
                "class_guid": "",
                "removal_policy": None,
            }
            row["proxy_instance_id"] = None
            row["proxy_present"] = False
            if iid:
                row["present"] = b.is_present(iid)
                self._fill_from_node(row, iid, b)
                # The proxy carries the SAME suffix as the real node -- serial
                # for a dongle, bus-relative instance path for the built-in
                # Intel. Verified on all three radios.
                proxy = proxy_for(instance_ids, iid)
                if proxy:
                    row["proxy_instance_id"] = proxy
                    row["proxy_present"] = b.is_present(proxy)
                    # A torn-down real node answers nothing; its proxy answers
                    # everything, including the removal policy that tells a
                    # built-in radio from one with a plug.
                    if row["removal_policy"] is None:
                        stand_in = {}
                        self._fill_from_node(stand_in, proxy, b)
                        row["removal_policy"] = stand_in.get("removal_policy")
            row["verdict"] = verdict(row["present"], row["service"],
                                     row["vbox_state"], node_exists=bool(iid),
                                     proxy_present=row["proxy_present"])
            out.append(row)

        # A filter with no host device at all is still a configured radio, and
        # saying nothing about it is how an unplugged dongle stayed invisible.
        for spec in state.get("absent", []):
            out.append({
                "label": f"filter \u201c{spec.get('name') or 'unnamed'}\u201d "
                         f"({spec.get('vendor')}:"
                         f"{spec.get('product_id') or '*'})",
                "instance_id": None, "vendor": spec.get("vendor"),
                "product_id": spec.get("product_id"), "serial": "",
                "port": None, "vbox_state": "", "vm_holds": False,
                "present": False, "service": "", "driver_desc": "",
                "class_guid": "", "removal_policy": None,
                "proxy_instance_id": None, "proxy_present": False,
                "verdict": ABSENT,
            })
        return out

    def _fill_from_node(self, row, instance_id, b):
        """Read one node's registry facts into `row`. Read-only."""
        handle, info = b.open_device(instance_id)
        if handle is None:
            return row
        try:
            row["service"] = b.get_property(handle, info, SPDRP_SERVICE)
            row["driver_desc"] = b.get_property(handle, info, SPDRP_DEVICEDESC)
            row["class_guid"] = b.get_property(handle, info, SPDRP_CLASSGUID)
            row["removal_policy"] = b.get_property_dword(handle, info,
                                                         SPDRP_REMOVAL_POLICY)
        finally:
            b.close_device(handle)
        return row

    def row_for(self, instance_id):
        for row in self.rows():
            if row.get("instance_id") == instance_id:
                return row
        return None

    # -- take -------------------------------------------------------------
    def take(self, row, apply=False):
        """Dry run by default. Every read step really runs; nothing changes.

        With apply=True the six-call sequence runs in the order the plan
        printed. Each state-changing call is inside its own ``if apply:`` --
        that is the property test_radio_custody.py checks in the AST, so a
        future edit cannot quietly move one out.
        """
        b, ctx = self._b, self.context()
        plan = take_plan(row, ctx)
        result = {"verb": "take", "label": row.get("label"),
                  "instance_id": row.get("instance_id"), "plan": plan,
                  "probe": {}, "applied": False, "ok": False,
                  "reboot_required": None, "steps": [], "error": ""}
        iid = row.get("instance_id")
        if not iid:
            return result
        chosen = None
        handle, info = b.open_device(iid)
        if handle is None:
            result["error"] = ("SetupDiOpenDeviceInfoW failed for " + iid
                               + " -- the node cannot be opened at all.")
            return result
        try:
            result["probe"] = {
                "current_class_guid": b.get_property(handle, info,
                                                     SPDRP_CLASSGUID),
                "current_service": b.get_property(handle, info, SPDRP_SERVICE),
                "current_driver_desc": b.get_property(handle, info,
                                                      SPDRP_DEVICEDESC),
                "present": b.is_present(iid),
                "node_exists": b.node_exists(iid),
                "vbox_proxy_node": row.get("proxy_instance_id"),
                "vbox_proxy_present": row.get("proxy_present"),
                "vbox_host_state": row.get("vbox_state"),
            }
            drivers = b.find_vbox_driver_at_set_level(ctx["inf"])
            result["probe"]["driver_nodes_in_VBoxUSB.inf"] = [
                f"{d['description']} / {d['provider']} / {d['version']}"
                for d in drivers]
            # Refusals are decided BEFORE any apply block is entered, and after
            # the reads -- so a refused dry run still shows what it found.
            if plan["refusals"]:
                return result
            if not drivers:
                result["error"] = (
                    "VBoxUSB.inf yielded no driver node even at USB-class set "
                    "level. Without it there is nothing to select; not "
                    "proceeding.")
                return result

            if apply:
                ok = b.set_class_guid(handle, info, USB_CLASS_GUID)
                result["steps"].append(
                    ("SetupDiSetDeviceRegistryPropertyW SPDRP_CLASSGUID "
                     + USB_CLASS_GUID, ok))
                if not ok:
                    result["error"] = (
                        "The class change was refused. SPDRP_CLASSGUID is "
                        "listed BOTH as settable (DeviceInfoData.ClassGuid is "
                        "updated on return) and as reserved in the same MSDN "
                        "page; this machine has answered the question. Nothing "
                        "further was attempted.")
                    return result
            if apply:
                ok = b.set_device_driver_path(handle, info, ctx["inf"])
                result["steps"].append(
                    ("SetupDiSetDeviceInstallParamsW DI_ENUMSINGLEINF | "
                     "DI_FLAGSEX_ALLOWEXCLUDEDDRVS " + ctx["inf"], ok))
            if apply:
                found = b.build_device_driver_list(handle, info)
                result["steps"].append(
                    ("SetupDiBuildDriverInfoList SPDIT_CLASSDRIVER (device "
                     f"level) -> {len(found)} node(s)", bool(found)))
                chosen = next((d for d in found
                               if d["description"] == VBOX_DRIVER_DESC), None)
                if chosen is None:
                    result["error"] = (
                        f"{VBOX_DRIVER_DESC!r} is not in the device's driver "
                        "list even after the class change. Not proceeding.")
                    return result
                ok = b.set_selected_driver(handle, info, chosen["_drvinfo"])
                result["steps"].append(("SetupDiSetSelectedDriverW "
                                        + VBOX_DRIVER_DESC, ok))
            if apply:
                ok, need_reboot, err = b.install_device(handle, info,
                                                        chosen["_drvinfo"])
                result["steps"].append((f"DiInstallDevice -> ok={ok} "
                                        f"needReboot={need_reboot} "
                                        f"lastError={err}", ok))
                result["applied"] = True
                result["ok"] = ok
                result["reboot_required"] = need_reboot
                if not ok:
                    result["error"] = f"DiInstallDevice failed, error {err}."
        finally:
            b.close_device(handle)
        return result

    # -- return -----------------------------------------------------------
    def give_back(self, row, apply=False):
        """Uninstall the node and rescan, so the vendor driver comes back."""
        b, ctx = self._b, self.context()
        plan = return_plan(row, ctx)
        result = {"verb": "return", "label": row.get("label"),
                  "instance_id": row.get("instance_id"), "plan": plan,
                  "probe": {}, "applied": False, "ok": False,
                  "steps": [], "error": ""}
        iid = row.get("instance_id")
        if iid:
            result["probe"] = {"present": b.is_present(iid),
                               "service": row.get("service"),
                               "vm_holds": row.get("vm_holds")}
        if plan["refusals"] or not iid:
            return result
        if apply:
            code, out = b.remove_device(iid)
            result["steps"].append((f"pnputil /remove-device {iid} -> "
                                    f"{code}: {out[-200:]}", code == 0))
        if apply:
            code, out = b.scan_devices()
            result["steps"].append((f"pnputil /scan-devices -> {code}: "
                                    f"{out[-200:]}", code == 0))
            result["applied"] = True
            result["ok"] = code == 0
        return result


# ============================================================================
# CLI
# ============================================================================

def _emit(payload, text, as_json):
    print(json.dumps(payload, indent=2, default=str) if as_json else text)


def _select(rows, target):
    if target == "--all" or target is None:
        return rows
    wanted = str(target).strip().upper()
    hits = [r for r in rows
            if (r.get("instance_id") or "").upper() == wanted]
    return hits


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="radio_custody",
        description="Take a Bluetooth radio out of Windows' hands and give it "
                    "to EsotericOS permanently -- or give it back.")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable output")
    sub = parser.add_subparsers(dest="verb", required=True)
    sub.add_parser("audit", help="report every configured radio; changes "
                                 "nothing, ever")
    for verb, helptext in (("take", "bind VBoxUSB.sys as the function driver"),
                           ("return", "uninstall the node so Windows rebinds")):
        p = sub.add_parser(verb, help=helptext)
        # argparse would swallow a bare "--all" as an unknown option, so it is
        # a real flag as well as an accepted positional value.
        p.add_argument("target", nargs="?", default=None,
                       help="a device instance id (or use --all)")
        p.add_argument("--all", dest="every", action="store_true",
                       help="every configured radio")
        p.add_argument("--apply", action="store_true",
                       help="actually do it (default is a dry run that "
                            "prints the exact calls and stops)")
    args = parser.parse_args(argv)

    custody = RadioCustody()
    rows = custody.rows()

    if args.verb == "audit":
        ctx = custody.context()
        payload = {"vm": ctx["vm"], "elevated": ctx["elevated"],
                   "inf": ctx["inf"], "inf_present": ctx["inf_present"],
                   "cat_present": ctx["cat_present"], "radios": rows}
        _emit(payload, audit_text(rows), args.json)
        return 0

    target = None if getattr(args, "every", False) else args.target
    if target is None and not getattr(args, "every", False):
        _emit({"error": "no target"},
              "Name a device instance id, or pass --all. Run "
              "`radio_custody.py audit` to see them.", args.json)
        return 2
    chosen = _select(rows, target)
    if not chosen:
        _emit({"error": "no such radio", "target": args.target},
              f"No configured radio has instance id {args.target}. Run "
              "`radio_custody.py audit` to see them.", args.json)
        return 2

    results = []
    for row in chosen:
        run = (custody.take if args.verb == "take" else custody.give_back)
        result = run(row, apply=args.apply)
        results.append(result)
        if not args.json:
            print(plan_text(result["plan"], result.get("probe")))
            for step, ok in result["steps"]:
                print(("  [done] " if ok else "  [FAIL] ") + step)
            if result["error"]:
                print("  ERROR: " + result["error"])
            if result.get("reboot_required"):
                print("  A RESTART IS REQUIRED to finish this bind.")
            print("")
    if args.json:
        _emit({"verb": args.verb, "apply": args.apply, "results": results},
              "", True)
    failed = [r for r in results if r["error"] or r["plan"]["refusals"]]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
