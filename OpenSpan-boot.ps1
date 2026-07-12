# OpenSpan boot orchestrator - runs at every startup (scheduled task).
# Reads the persisted radio-ownership mode and brings the machine up in it.
#   station : the OpenSpan app owns the Bluetooth radio (command station +
#             iPad bridge, near-bare-metal). Windows BT goes dark.
#   windows : Windows keeps the radio (native Bluetooth + audio). Default.
# Switching between modes is done by the app: it writes mode.txt and
# reboots, so the handover is always clean.
$ErrorActionPreference = 'SilentlyContinue'
$ROOT = 'D:\OpenSpan'
$VBOX = 'C:\Program Files\Oracle\VirtualBox\VBoxManage.exe'
$log  = Join-Path $ROOT 'boot.log'
function Log($m){ "$(Get-Date -Format s)  $m" | Out-File $log -Append -Encoding ascii }

$mode = 'windows'
try { $mode = (Get-Content (Join-Path $ROOT 'mode.txt') -Raw).Trim().ToLower() } catch {}
Log "boot: mode=$mode"

if ($mode -eq 'station') {
    # The app owns the radio. Bring up the Debian command station; its USB
    # filter claims the Bluetooth controller (Windows loses BT until the
    # next switch back).
    # At boot the VBox host services/drivers take ~20-40s to be ready, so
    # startvm can no-op silently. Wait for VBox, then retry until the VM
    # actually stays running.
    Start-Sleep -Seconds 25
    $up = $false
    for ($i = 0; $i -lt 12; $i++) {
        if (& $VBOX list runningvms | Select-String '"OpenSpan"') { $up = $true; break }
        & $VBOX startvm OpenSpan --type headless | Out-Null
        Start-Sleep -Seconds 8
    }
    Log ("boot: command-station VM " + ($(if ($up) { "running" } else { "start FAILED after retries" })))
} else {
    # Windows mode: do nothing. Windows keeps the Bluetooth radio.
    Log "boot: windows mode - leaving radio with Windows"
}
