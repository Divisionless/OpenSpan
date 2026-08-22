#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic source contracts for admin-only GUI startup."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OPENSPAN = (ROOT / "win" / "openspan.py").read_text(encoding="utf-8")
INSTALLER = (ROOT / "tools" / "app-autostart.ps1").read_text(encoding="utf-8")
BAKE_IN = (ROOT / "bake-in.ps1").read_text(encoding="utf-8")
failures = []


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        failures.append(name)


run_app = OPENSPAN[OPENSPAN.index("def run_app():"):]
check("non-admin GUI always requests an elevated replacement",
      "if not is_elevated():" in run_app
      and "if not _launch_elevated():" in run_app)
check("non-admin GUI cannot continue into app construction",
      run_app.index("if not is_elevated():")
      < run_app.index("return\n\n    key_ok = ensure_ssh_key()"))
check("the old optional elevation gate is absent",
      "def _elevation_gate(" not in OPENSPAN
      and "startup_choice" not in run_app)

check("autostart uses a Highest interactive logon task",
      "New-ScheduledTaskTrigger -AtLogOn" in INSTALLER
      and "-LogonType Interactive -RunLevel Highest" in INSTALLER)
check("autostart executes the selected app directly",
      "New-ScheduledTaskAction -Execute $ExePath" in INSTALLER)
check("legacy Run removal follows exact task verification",
      INSTALLER.index("Assert-TaskContract -Task $installed")
      < INSTALLER.index("Remove-ItemProperty -LiteralPath $RunKey"))
check("check mode rejects dual automatic launch ownership",
      "Legacy Run value '$RunName' is still present" in INSTALLER)
check("bake-in delegates startup ownership to the elevated task installer",
      "tools\\app-autostart.ps1" in BAKE_IN
      and "Set-ItemProperty -Path $key -Name EsotericOS" not in BAKE_IN)

if failures:
    raise SystemExit(f"\nRESULT: {len(failures)} failure(s): "
                     + ", ".join(failures))
print("\nRESULT: ALL PASS")
