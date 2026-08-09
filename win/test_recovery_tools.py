"""Headless checks for the extreme-failure give-back tool and recovery
timeouts. The give-back must stay GENERIC — it ships to machines whose
adapters we have never seen, so any hardware literal in it is a defect."""

import pathlib
import re


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


ROOT = pathlib.Path(__file__).parent.parent
bat = (ROOT / "Return-Radios-To-Windows.bat").read_text(encoding="utf-8")
ps1 = (ROOT / "win" / "return_radios.ps1").read_text(encoding="utf-8")

check("bat wrapper exists and invokes the PowerShell implementation",
      "return_radios.ps1" in bat and "-ExecutionPolicy Bypass" in bat)
check("give-back carries no adapter MAC or serial literals",
      not re.search(r"[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}", ps1)
      and not re.search(r'"[0-9A-Fa-f]{12}"', ps1))
check("give-back stops the VM gently before it ever pulls the plug",
      ps1.index("controlvm $VmName acpipowerbutton")
      < ps1.index("controlvm $VmName poweroff"))
check("give-back heals only unhealthy Bluetooth nodes",
      '@("Error", "Unknown")' in ps1
      and "Get-PnpDevice -Class Bluetooth" in ps1)
check("give-back survives a machine with no VirtualBox at all",
      "skipping VM stop" in ps1)

import sys  # noqa: E402
sys.path.insert(0, str(ROOT / "win"))
import openspan as A  # noqa: E402

# --- gentle_release: ordered, verified, one attempt per radio --------------
calls = []
held = {"aaa", "bbb"}


def fake_vbox(*args):
    calls.append(" ".join(args))
    if args[2] == "usbdetach" and args[3] != "stuck":
        held.discard(args[3])


rel, kept = A.gentle_release(vbox_run=fake_vbox, verify=lambda: set(held),
                             settle=0, log=lambda m: None)
check("gentle release detaches every held radio, one verified pass",
      sorted(rel) == ["aaa", "bbb"] and not kept and len(calls) == 2)

calls.clear()
held = {"stuck", "ccc"}
rel, kept = A.gentle_release(vbox_run=fake_vbox, verify=lambda: set(held),
                             settle=0, log=lambda m: None)
check("a radio that will not detach is reported, never retried",
      rel == ["ccc"] and kept == ["stuck"]
      and len([c for c in calls if "stuck" in c]) == 1)

# --- _pnp_kick: generic instance-ID construction and phantom honesty -------
runs = []


def fake_runner(args):
    runs.append(args)

    class R:
        returncode = 0
        stderr = ""
        stdout = ("Restarting device...\nDevice restarted successfully."
                  if args[1] == "/restart-device" else
                  "Instance ID: USB\\VID_8087&PID_0AAA\\5&AB&0&14\n")
    return R()


ok, detail = A._pnp_kick(
    {"vendor": "0x2357", "product_id": "0x0604", "serial": "AABBCCDDEEFF"},
    runner=fake_runner)
check("kick builds the instance ID from the device's own VID/PID/serial",
      ok and runs[-1] == ["pnputil", "/restart-device",
                          "USB\\VID_2357&PID_0604\\AABBCCDDEEFF"])

runs.clear()
ok, detail = A._pnp_kick(
    {"vendor": "8087", "product_id": "0aaa", "serial": ""},
    runner=fake_runner)
check("a serial-less adapter is discovered by VID/PID prefix, never assumed",
      ok and runs[0][1] == "/enum-devices"
      and runs[-1][2] == "USB\\VID_8087&PID_0AAA\\5&AB&0&14")


def phantom_runner(args):
    class R:
        returncode = 1
        stderr = ""
        stdout = "Failed to restart device.\nThe device is not connected."
    return R()


ok, detail = A._pnp_kick(
    {"vendor": "2357", "product_id": "0604", "serial": "X"},
    runner=phantom_runner)
check("a phantom node is named as such -- replug territory, not retry",
      not ok and "phantom" in detail)

app_src = (ROOT / "win" / "openspan.py").read_text(encoding="utf-8")
check("every power-off path releases gently before pulling the plug",
      app_src.count("gentle_release()")
      == app_src.count('vbox("controlvm", VM, "acpipowerbutton")')
      + app_src.count('vbox("controlvm", VM, "poweroff")')
      - 1)  # cold restart owns BOTH: ACPI first, poweroff as fallback
check("cold restart asks the guest to shut down before pulling any plug",
      app_src.index('vbox("controlvm", VM, "acpipowerbutton")')
      < app_src.index("guest ignored ACPI"))
check("the cold-restart dialog no longer claims a hardware power-cycle",
      "never power-cycle" in app_src and "Power-cycle the whole VM" not in app_src)

app = app_src
check("preflight-bearing guest chains get recovery-path headroom (90s)",
      app.count("timeout=90") >= 2
      and "hangs up at 45s reports" in app)
check("no preflight-bearing chain still uses the starved 45s budget",
      "timeout=45, quiet=True)" not in app)
