# OpenSpan boot orchestrator - runs at every startup (scheduled task).
# Reads the persisted radio-ownership mode and brings the machine up in it.
#   station : EsotericOS owns the Bluetooth radios. Windows BT goes dark, and
#             that is ENFORCED here, not hoped for.
#   windows : Windows keeps the radios (native Bluetooth + audio). Default.
# Switching between modes is done by the app: it writes mode.txt and reboots,
# so the handover is always clean.
#
# Two things this script got wrong until 2026-08-09, both proven from boot.log:
#
#   1. The VM name was the literal "OpenSpan". This machine's VM is
#      "OpenSpan-Codex", declared in openspan_settings.json, so EVERY boot
#      since the rename logged "start FAILED after retries" and the command
#      station never came up. The name is read from settings now; the literal
#      is only a last-resort default.
#   2. "Windows BT goes dark" was a comment, not an action. Nothing stopped
#      the Windows stack from binding the radios first, so ownership was a
#      race between bthserv and the VM's USB filters -- and a lost race is
#      how a radio ends up captured-but-never-delivered (a phantom PnP node
#      that only a restart clears). Station mode now stands the Windows
#      Bluetooth service down BEFORE the VM starts, and Windows mode puts it
#      back.
$ErrorActionPreference = 'SilentlyContinue'
$ROOT = 'D:\OpenSpan'
$VBOX = 'C:\Program Files\Oracle\VirtualBox\VBoxManage.exe'
$log  = Join-Path $ROOT 'boot.log'
function Log($m){ "$(Get-Date -Format s)  $m" | Out-File $log -Append -Encoding ascii }

$mode = 'windows'
try { $mode = (Get-Content (Join-Path $ROOT 'mode.txt') -Raw).Trim().ToLower() } catch {}

# The VM name belongs to the settings file, which is what the app reads. A
# hardcoded name here silently un-sticks itself the moment the VM is renamed.
$vm = 'OpenSpan'
try {
    $settings = Get-Content (Join-Path $ROOT 'openspan_settings.json') -Raw |
        ConvertFrom-Json
    if ($settings.vm_name) { $vm = [string]$settings.vm_name }
} catch { Log "boot: settings unreadable - falling back to VM '$vm'" }
Log "boot: mode=$mode vm=$vm"

function Set-WindowsBluetooth($enabled) {
    # The whole of "greedy": while EsotericOS owns the desk, the Windows
    # Bluetooth service does not get to hold a radio. Reversible by design --
    # switching back to Windows mode restores it on the next boot.
    if ($enabled) {
        Set-Service bthserv -StartupType Manual
        Start-Service bthserv
        Log "boot: Windows Bluetooth service restored (Manual, started)"
    } else {
        Stop-Service bthserv -Force
        Set-Service bthserv -StartupType Disabled
        Log "boot: Windows Bluetooth service stood down (stopped, disabled)"
    }
}

if ($mode -eq 'station') {
    # THE job of this task, and the only part it can actually do.
    #
    # It does NOT start the VM, and the retry loop that used to try was never
    # capable of it: this task runs as SYSTEM, and VirtualBox registers
    # machines PER USER. SYSTEM has no VirtualBox registry at all
    # (%SystemRoot%\System32\config\systemprofile\.VirtualBox is absent), so
    # `startvm` could not find the machine under any name -- which is why
    # every boot from 2026-08-02 to 2026-08-10 logged a failure. Fixing the
    # name in 2026-08-09 made the log honest about the name and no more.
    #
    # Starting the VM belongs to the app, which runs as Doug, elevated, and
    # already does it in station mode. What CANNOT wait for the app is
    # standing Windows' Bluetooth down: it has to happen before the Windows
    # stack binds the radios, and only something running at boot can do that.
    # So the task owns ownership, the app owns the VM, and each runs where it
    # is actually able to succeed.
    Set-WindowsBluetooth $false
    Log "boot: radios reserved for EsotericOS - the app starts the VM"
} else {
    Set-WindowsBluetooth $true
    Log "boot: windows mode - radios left with Windows"
}
