<#
.SYNOPSIS
  Bake EsotericOS into the desktop: start it at sign-in, on the desktop, every time.

.DESCRIPTION
  Writes HKCU\Software\Microsoft\Windows\CurrentVersion\Run : EsotericOS = "<this folder>\EsotericOS.exe".
  Per-user, no admin needed to write. Both Explorer and EsotericOS Shell run the Run key at
  sign-in, so this holds before and after the shell swap. UAC is off on this machine, so the
  process comes up elevated as the app requires (UIPI: hooks die under elevated windows otherwise).

  It also adds the Windows Firewall rule EsotericOS needs to be a LAN node: allow the
  PROGRAM EsotericOS.exe, inbound and outbound, on private networks.

  THE RULE ALLOWS A PROGRAM, NOT A PORT, and it has to. The LAN service port is assigned
  by the OS at every launch (bind to 0, read it back, advertise it) so that nothing has to
  pick a number that could already be taken on somebody else's machine. A port rule would
  therefore be stale by the next restart. Allowing the executable covers whatever port the
  OS hands out, and covers mDNS discovery with it.

  This is the one place a firewall rule is added without a click, and it is not behind the
  user's back: running this script elevated IS the consent, it is idempotent, and it prints
  exactly what it added. The app itself never adds a rule on its own -- it detects a blocked
  inbound and offers an in-window "Allow EsotericOS through the firewall" button that runs
  this same rule.

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

$ruleName = 'EsotericOS'

function Get-FirewallRuleState {
    $found = netsh advfirewall firewall show rule name="$ruleName" 2>$null
    if ($LASTEXITCODE -eq 0 -and $found -match 'Rule Name') { return $true }
    return $false
}

function Show-Firewall {
    ""
    "--- Windows Firewall (LAN nodes) ---"
    if (Get-FirewallRuleState) {
        "rule '$ruleName' : present (allows the PROGRAM $exe)"
    } else {
        "rule '$ruleName' : (absent) -- this machine cannot accept node pairings"
    }
}

function Add-FirewallRule {
    if (-not (Test-Path $exe)) { "no EsotericOS.exe beside this script; firewall rule skipped"; return }
    if (Get-FirewallRuleState) { "firewall rule '$ruleName' already present"; return }
    # A PROGRAM rule. The service port is the OS's to choose and changes every launch.
    netsh advfirewall firewall add rule name="$ruleName" dir=in action=allow program="$exe" enable=yes profile=private | Out-Null
    netsh advfirewall firewall add rule name="$ruleName" dir=out action=allow program="$exe" enable=yes profile=private | Out-Null
    if (Get-FirewallRuleState) {
        "firewall rule '$ruleName' added: allow program `"$exe`" in+out, private profile"
    } else {
        "firewall rule '$ruleName' could NOT be added -- run this script as administrator"
    }
}

$cur = (Get-ItemProperty -Path $key -Name EsotericOS -ErrorAction SilentlyContinue).EsotericOS
"Run\EsotericOS : " + $(if ($null -eq $cur) { '(absent)' } else { $cur })
if ($Custody) { Show-Custody -Verb 'take'; exit 0 }
if ($Check) { Show-Firewall; exit 0 }
if ($Undo) {
    if ($null -ne $cur) { Remove-ItemProperty -Path $key -Name EsotericOS; "removed" } else { "nothing to remove" }
    if (Get-FirewallRuleState) {
        netsh advfirewall firewall delete rule name="$ruleName" | Out-Null
        "firewall rule '$ruleName' removed"
    } else { "no firewall rule to remove" }
    Show-Custody -Verb 'return'
    exit 0
}
if (-not (Test-Path $exe)) { "no EsotericOS.exe beside this script: $exe"; exit 1 }
$want = '"' + $exe + '"'
if ($cur -ne $want) {
    Set-ItemProperty -Path $key -Name EsotericOS -Value $want
    "Run\EsotericOS -> $want"
} else { "already baked in" }
Add-FirewallRule
