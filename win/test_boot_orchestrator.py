"""Guards for the boot orchestrator.

Every boot from 2026-08-02 to 2026-08-09 logged "start FAILED after
retries" because the VM name was a literal that no longer matched the
machine. These checks make that class of drift a test failure instead of
a line in a log nobody reads."""

import json
import pathlib
import re


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


ROOT = pathlib.Path(__file__).parent.parent
boot = (ROOT / "OpenSpan-boot.ps1").read_text(encoding="utf-8")

check("the VM name is read from settings, not hardcoded",
      "openspan_settings.json" in boot and "$settings.vm_name" in boot)

# The task runs as SYSTEM, and VirtualBox registers machines PER USER, so
# SYSTEM cannot see this VM under any name. Attempting the start here can
# only write a false failure into the log -- which it did at every boot from
# 2026-08-02 to 2026-08-10.
check("the boot task does not try to start the VM",
      not re.search(r"\$VBOX\s+startvm", boot))
check("the reason is recorded where the next reader will look",
      "PER USER" in boot and "SYSTEM" in boot)

settings = json.loads((ROOT / "openspan_settings.json").read_text(
    encoding="utf-8"))
check("this machine's VM name is resolvable from settings",
      bool(settings.get("vm_name")))

# Doug's rule, 2026-08-09: "If EsotericOS is up i don't want windows
# touching my bluetooth." Station mode must ACT on that, not comment on it.
check("station mode stands the Windows Bluetooth service down",
      "Set-WindowsBluetooth $false" in boot
      and "Stop-Service bthserv" in boot)
check("windows mode restores it, so the switch is reversible",
      "Set-WindowsBluetooth $true" in boot
      and "Start-Service bthserv" in boot)
check("station mode reserves the radios and says so",
      "radios reserved for EsotericOS" in boot)
