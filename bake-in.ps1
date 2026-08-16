<#
.SYNOPSIS
  Bake EsotericOS into the desktop: start it at sign-in, on the desktop, every time.

.DESCRIPTION
  Writes HKCU\Software\Microsoft\Windows\CurrentVersion\Run : EsotericOS = "<this folder>\EsotericOS.exe".
  Per-user, no admin needed to write. Both Explorer and EsotericOS Shell run the Run key at
  sign-in, so this holds before and after the shell swap. UAC is off on this machine, so the
  process comes up elevated as the app requires (UIPI: hooks die under elevated windows otherwise).

  -Check   : report only.
  -Undo    : remove the value, and print the commands that hand the radios back to Windows.
  -Custody : report who owns each Bluetooth radio, and print the take commands.

  RADIO CUSTODY IS NEVER AUTOMATIC. This script will not take, or release, a radio. It audits
  and it prints commands. Custody is taken by Doug -- either the "Take custody" button on the
  Bluetooth panel (one click plans, a second click applies) or `radio_custody.py take <id>
  --apply` run by hand. Baking the app into sign-in must not quietly rewrite a driver binding,
  and uninstalling must not quietly leave one behind: see docs\RADIO-CUSTODY.md.
#>
param([switch]$Check, [switch]$Undo, [switch]$Custody)
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$exe = Join-Path $PSScriptRoot 'EsotericOS.exe'
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

$cur = (Get-ItemProperty -Path $key -Name EsotericOS -ErrorAction SilentlyContinue).EsotericOS
"Run\EsotericOS : " + $(if ($null -eq $cur) { '(absent)' } else { $cur })
if ($Custody) { Show-Custody -Verb 'take'; exit 0 }
if ($Check) { exit 0 }
if ($Undo) {
    if ($null -ne $cur) { Remove-ItemProperty -Path $key -Name EsotericOS; "removed" } else { "nothing to remove" }
    Show-Custody -Verb 'return'
    exit 0
}
if (-not (Test-Path $exe)) { "no EsotericOS.exe beside this script: $exe"; exit 1 }
$want = '"' + $exe + '"'
if ($cur -ne $want) {
    Set-ItemProperty -Path $key -Name EsotericOS -Value $want
    "Run\EsotericOS -> $want"
} else { "already baked in" }
