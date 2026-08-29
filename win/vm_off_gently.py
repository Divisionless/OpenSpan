r"""Power the bridge VM off the way the app does it, from the command line.

Ordered handback first -- each VM-held radio detached one at a time and
verified -- then an ACPI power button so the guest shuts down cleanly, then a
hard poweroff only if the guest ignores ACPI for 30 s, so a custody take or a
Windows restart can be prepared without the GUI (the app must already be
closed; a running app would notice the VM going down and try to bring it back).

This used to be described as mirroring openspan.py's cold_restart_vm() and
_full_stop(). cold_restart_vm is GONE -- Doug removed the VM buttons on
2026-08-29 because they raced a live Windows device stack, and a machine
restart is the supported recovery path now. _full_stop() survives (the window's
X and the tray reach it) and still calls the same gentle_release(). So this is
the operator's copy of that ordered handback, deliberately kept: it is a
command a person runs on purpose, not a button that can be hit by accident,
which was the whole objection to the buttons.

    C:\Python313\python.exe win\vm_off_gently.py            # do it
    C:\Python313\python.exe win\vm_off_gently.py --status   # read-only

Reads the VM name from openspan_settings.json beside the app; nothing here is
written out that the app does not already derive the same way.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def vboxmanage():
    for base in (os.environ.get("VBOX_MSI_INSTALL_PATH", ""),
                 os.environ.get("VBOX_INSTALL_PATH", ""),
                 os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                              "Oracle", "VirtualBox")):
        if base:
            cand = os.path.join(base, "VBoxManage.exe")
            if os.path.isfile(cand):
                return cand
    return shutil.which("VBoxManage") or ""


def vm_name():
    try:
        with open(os.path.join(ROOT, "openspan_settings.json"), encoding="utf-8") as fh:
            return str(json.load(fh).get("vm_name", "OpenSpan")).strip() or "OpenSpan"
    except Exception:  # noqa: BLE001
        return "OpenSpan"


def run(vbm, *args):
    return subprocess.run([vbm, *args], capture_output=True, text=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def showvminfo(vbm, vm):
    return run(vbm, "showvminfo", vm, "--machinereadable").stdout


def vm_state(vbm, vm):
    m = re.search(r'^VMState="([^"]*)"', showvminfo(vbm, vm), re.M)
    return m.group(1) if m else "unknown"


def attached(vbm, vm):
    return set(re.findall(r'^USBAttachActive\d+="([^"]+)"', showvminfo(vbm, vm), re.M))


def captured(vbm):
    out = run(vbm, "list", "usbhost").stdout
    rows, cur = [], {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("UUID:"):
            cur = {"uuid": line.split(":", 1)[1].strip()}
        elif ":" in line and cur is not None:
            k, v = line.split(":", 1)
            cur[k.strip()] = v.strip()
            if k.strip() == "Current State":
                rows.append(cur)
    return [r for r in rows if r.get("Current State") == "Captured"]


def main(argv):
    vbm = vboxmanage()
    if not vbm:
        print("VBoxManage not found"); return 2
    vm = vm_name()
    state = vm_state(vbm, vm)
    print(f"VM {vm}: {state}; attached: {sorted(attached(vbm, vm))}")
    if "--status" in argv:
        for r in captured(vbm):
            print(f"  Captured: {r.get('uuid')} {r.get('VendorId','')}:{r.get('ProductId','')} "
                  f"{r.get('SerialNumber','')} {r.get('Address','')}")
        return 0
    if state == "running":
        released, kept = [], []
        for uuid in sorted(attached(vbm, vm)):
            run(vbm, "controlvm", vm, "usbdetach", uuid)
            time.sleep(0.5)
            (kept if uuid in attached(vbm, vm) else released).append(uuid)
        print(f"released {len(released)}, would not detach {len(kept)}")
        run(vbm, "controlvm", vm, "acpipowerbutton")
        for _ in range(30):
            if vm_state(vbm, vm) != "running":
                break
            time.sleep(1)
        if vm_state(vbm, vm) == "running":
            print("guest ignored ACPI -- hard poweroff")
            run(vbm, "controlvm", vm, "poweroff")
            for _ in range(15):
                if vm_state(vbm, vm) != "running":
                    break
                time.sleep(1)
    print(f"VM {vm}: {vm_state(vbm, vm)}")
    time.sleep(2)
    caps = captured(vbm)
    print(f"still Captured on the host: {len(caps)}")
    for r in caps:
        print(f"  {r.get('uuid')} {r.get('VendorId','')}:{r.get('ProductId','')} "
              f"{r.get('SerialNumber','')} {r.get('Address','')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
