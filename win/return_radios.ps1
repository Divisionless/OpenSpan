# Return-Radios-To-Windows — the extreme-failure give-back.
#
# When OpenSpan (or VirtualBox under it) is wedged, broken, or gone, this
# hands every Bluetooth radio back to Windows so the desk still has working
# Bluetooth. It is deliberately independent of the app: plain PowerShell,
# VBoxManage, and pnputil — nothing else.
#
# It is GENERIC on purpose: no adapter serials, no MACs, no machine-specific
# paths. It operates on (1) the named VM and (2) whatever Bluetooth-class
# devices Windows reports unhealthy. It must work on any machine OpenSpan
# ships to.
param(
    [string]$VmName = "OpenSpan-Codex"
)

$ErrorActionPreference = "Continue"

function Find-VBoxManage {
    $candidates = @(
        (Join-Path $env:ProgramFiles "Oracle\VirtualBox\VBoxManage.exe"),
        "VBoxManage.exe"
    )
    foreach ($c in $candidates) {
        if (Get-Command $c -ErrorAction SilentlyContinue) { return $c }
    }
    return $null
}

$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Output "NOTE: not elevated - device restarts may be refused."
    Write-Output "      Right-click the .bat and Run as administrator if so."
}

$vbox = Find-VBoxManage
if ($vbox) {
    $running = & $vbox list runningvms 2>$null
    if ($running -match [regex]::Escape($VmName)) {
        # Gentle first: ACPI power button, up to 30s. The VM's mass USB
        # release at hard poweroff is the exact event that injured the PnP
        # tree on 2026-08-08 - give the guest a chance to go down cleanly.
        Write-Output "Stopping VM '$VmName' (ACPI, up to 30s)..."
        & $vbox controlvm $VmName acpipowerbutton 2>$null
        $stopped = $false
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep 1
            if (-not ((& $vbox list runningvms 2>$null) -match [regex]::Escape($VmName))) {
                $stopped = $true; break
            }
        }
        if (-not $stopped) {
            Write-Output "ACPI ignored - hard poweroff."
            & $vbox controlvm $VmName poweroff 2>$null
            Start-Sleep 3
        }
        Write-Output "VM stopped."
    } else {
        Write-Output "VM '$VmName' is not running."
    }
} else {
    Write-Output "VBoxManage not found - skipping VM stop (fine if VirtualBox is gone)."
}

# With the VM down and VBoxSVC idle, Windows re-enumerates released radios by
# itself. What remains is healing any node the day's wedges left behind:
# Bluetooth-class devices in Error or Unknown state get one restart each.
Write-Output ""
Write-Output "Bluetooth devices as Windows sees them:"
$radios = Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue |
    Where-Object { $_.InstanceId -like "USB\*" }
foreach ($r in $radios) {
    Write-Output ("  [{0}] {1}" -f $r.Status, $r.FriendlyName)
}

$sick = $radios | Where-Object { $_.Status -in @("Error", "Unknown") }
foreach ($r in $sick) {
    Write-Output ("Restarting: " + $r.FriendlyName)
    pnputil /restart-device "$($r.InstanceId)" | Select-Object -Last 1
}
if (-not $sick) {
    Write-Output "No unhealthy radio nodes - nothing to heal."
}

Start-Sleep 3
Write-Output ""
Write-Output "Final state:"
Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue |
    Where-Object { $_.InstanceId -like "USB\*" } |
    ForEach-Object { Write-Output ("  [{0}] {1}" -f $_.Status, $_.FriendlyName) }
Write-Output ""
Write-Output "Done. Windows owns every radio it can see. If one is still"
Write-Output "missing entirely, unplug it and plug it back in - a fresh"
Write-Output "arrival always re-enumerates."
