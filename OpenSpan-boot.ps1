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
    Set-WindowsBluetooth $false

    # At boot the VBox host services/drivers take ~20-40s to be ready, so
    # startvm can no-op silently. Wait for VBox, then retry until the VM
    # actually stays running.
    Start-Sleep -Seconds 25
    $up = $false
    for ($i = 0; $i -lt 12; $i++) {
        # Match the quoted name exactly: a bare substring match would let
        # "OpenSpan" pass for "OpenSpan-Codex" and vice versa.
        if (& $VBOX list runningvms | Select-String ('"' + [regex]::Escape($vm) + '"')) {
            $up = $true; break
        }
        & $VBOX startvm $vm --type headless | Out-Null
        Start-Sleep -Seconds 8
    }
    Log ("boot: command-station VM " + $(if ($up) { "running" } else { "start FAILED after retries" }))

    if ($up) {
        # Say what the VM actually holds. "Started" is not "owns the radios",
        # and the difference is the whole failure mode this script exists for.
        $held = (& $VBOX showvminfo $vm 2>$null |
            Select-String 'Currently attached USB devices' -Context 0,40 |
            Select-String 'ProductId:' | Measure-Object).Count
        Log "boot: VM holds $held USB device(s)"
    }
} else {
    Set-WindowsBluetooth $true
    Log "boot: windows mode - radios left with Windows"
}
