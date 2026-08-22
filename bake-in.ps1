# SPDX-License-Identifier: AGPL-3.0-or-later
<#
.SYNOPSIS
  Bake EsotericOS into the desktop: start it at sign-in, on the desktop, every time.

.DESCRIPTION
  Installs the per-user "EsotericOS App (elevated)" logon task through
  tools\app-autostart.ps1. The task is RunLevel Highest and becomes the sole
  automatic launch owner; the legacy HKCU Run value is removed only after the
  exact task contract is verified. This keeps the GUI elevated because UIPI
  silences its hooks whenever a higher-integrity window has focus.

  The same installer adds the Windows Firewall rules EsotericOS needs to be a
  LAN node: allow the exact PROGRAM EsotericOS.exe, inbound from LocalSubnet
  and outbound, on private networks.

  THE RULE ALLOWS A PROGRAM, NOT A PORT, and it has to. The LAN service port is assigned
  by the OS at every launch (bind to 0, read it back, advertise it) so that nothing has to
  pick a number that could already be taken on somebody else's machine. A port rule would
  therefore be stale by the next restart. Allowing the executable covers whatever port the
  OS hands out, and covers mDNS discovery with it.

  The install path adds this rule without a separate click, and it is not
  behind the user's back: running this script elevated IS the consent, it is
  idempotent, and it prints exactly what it installed. The app itself detects
  blocked inbound and offers an in-window "Allow EsotericOS through the
  firewall" repair action that installs the same narrow program contract.

  -Check   : report only.
  -Undo    : remove the value and the firewall rule, and print the commands that hand the
             radios back to Windows.
  -Custody : report who owns each Bluetooth radio, and print the take commands.

  RADIO CUSTODY IS NEVER AUTOMATIC. This script will not take, or release, a radio. It audits
  and it prints commands. Custody is taken by Doug -- either the "Take custody" button on the
  Bluetooth panel (one click plans, a second click applies) or `radio_custody.py take <id>
  --apply` run by hand. Baking the app into sign-in must not quietly rewrite a driver binding,
  and uninstalling must not quietly leave one behind: see docs\RADIO-CUSTODY.md.
#>
param([switch]$Check, [switch]$Undo, [switch]$Custody)
$exe = Join-Path $PSScriptRoot 'EsotericOS.exe'
$autostart = Join-Path $PSScriptRoot 'tools\app-autostart.ps1'
$py = 'C:\Python313\python.exe'
$custodyScript = Join-Path $PSScriptRoot 'win\radio_custody.py'

function Show-Custody {
    param([string]$Verb)
    "";
    "--- radio custody ($Verb) ---"
    if (-not (Test-Path $custodyScript)) { "no win\radio_custody.py beside this script"; return }
    if (-not (Test-Path $py)) { "no $py -- run the audit yourself with any Python 3"; return }
    & $py $custodyScript audit
    ""
    if ($Verb -eq 'return') {
        "To hand a radio back to Windows, run ONE of these, dry run first (no --apply):"
        "  & '$py' '$custodyScript' return <instance-id>"
        "  & '$py' '$custodyScript' return <instance-id> --apply"
        "Or every configured radio at once:"
        "  & '$py' '$custodyScript' return --all --apply"
    } else {
        "To put a radio into EsotericOS custody, run ONE of these, dry run first (no --apply):"
        "  & '$py' '$custodyScript' take <instance-id>"
        "  & '$py' '$custodyScript' take <instance-id> --apply"
        "Or every configured radio at once:"
        "  & '$py' '$custodyScript' take --all --apply"
        ""
        "Do a TP-Link dongle FIRST. If a bind goes wrong on a dongle you unplug it and"
        "plug it back in; if it goes wrong on the built-in Intel radio the only recovery"
        "is a Windows restart with no VM captures held. Prove the sequence on the cheap"
        "one before spending the expensive one."
    }
    "Nothing above has been run. --apply is yours to type."
}

if ($Custody) { Show-Custody -Verb 'take'; exit 0 }
if ($Check) {
    & $autostart -Check -ExePath $exe
    exit $LASTEXITCODE
}
if ($Undo) {
    & $autostart -Undo -ExePath $exe
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Show-Custody -Verb 'return'
    exit 0
}
if (-not (Test-Path $exe)) { "no EsotericOS.exe beside this script: $exe"; exit 1 }
& $autostart -ExePath $exe
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
