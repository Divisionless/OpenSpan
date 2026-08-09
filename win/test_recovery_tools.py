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

app_src = (ROOT / "win" / "openspan.py").read_text(encoding="utf-8")
check("every power-off path releases gently before pulling the plug",
      app_src.count("gentle_release()")
      == app_src.count('vbox("controlvm", VM, "poweroff")'))

app = app_src
check("preflight-bearing guest chains get recovery-path headroom (90s)",
      app.count("timeout=90") >= 2
      and "hangs up at 45s reports" in app)
check("no preflight-bearing chain still uses the starved 45s budget",
      "timeout=45, quiet=True)" not in app)
