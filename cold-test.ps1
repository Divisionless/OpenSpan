# cold-test.ps1 -- automate the software half of an OpenSpan cold-clone test.
#
# Supply a fresh SSH-reachable Debian 12 VM whose root authorized_keys already
# contains id_openspan.pub. This stages guest/, provisions the generic
# multi-device lane template, and runs the software verifier. USB assignment,
# per-device lane configuration, pairing, HID traffic, and audio stay manual.
#
#   .\cold-test.ps1 -Port 2223
#   .\cold-test.ps1 -Port 2223 -Key .\other_key

param(
  [string]$RepoRoot = $PSScriptRoot,
  [string]$VmHost   = "127.0.0.1",
  [int]$Port        = 2223,
  [string]$User     = "root",
  [string]$Key      = ""            # defaults to <RepoRoot>\id_openspan
)
$ErrorActionPreference = "Stop"

$guest = Join-Path $RepoRoot "guest"
$pub   = Join-Path $RepoRoot "id_openspan.pub"
if (-not $Key) { $Key = Join-Path $RepoRoot "id_openspan" }
if ($User -ne "root") {
  throw "cold-test requires -User root because provision.sh and /root staging require it"
}

$required = @(
  (Join-Path $guest "provision.sh"),
  (Join-Path $guest "verify-provision.sh"),
  (Join-Path $guest "bt-preflight.sh"),
  (Join-Path $guest "set-hid-device.sh"),
  (Join-Path $guest "set-hid-radio.sh"),
  (Join-Path $guest "set-hid-target.sh"),
  (Join-Path $guest "system\openspanble@.service"),
  (Join-Path $guest "system\openspanble.service"),
  (Join-Path $guest "system\openspanble-mac.service"),
  (Join-Path $guest "system\openspanble.service.d\10-wait.conf"),
  (Join-Path $guest "system\openspanble.service.d\override.conf")
)
foreach ($path in $required) {
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    throw "required current-topology artifact missing: $path"
  }
}
if (-not (Test-Path -LiteralPath $pub -PathType Leaf)) {
  throw "id_openspan.pub missing -- derive it from the private key before testing"
}
if (-not (Test-Path -LiteralPath $Key -PathType Leaf)) {
  throw "SSH key not found: $Key"
}
foreach ($tool in @("ssh.exe", "scp.exe")) {
  if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
    throw "$tool is required and was not found on PATH"
  }
}

$target  = "$User@$VmHost"
$sshBase = @(
  "-p", "$Port", "-i", $Key,
  "-o", "BatchMode=yes",
  "-o", "StrictHostKeyChecking=accept-new",
  "-o", "ConnectTimeout=8"
)
$scpBase = @(
  "-P", "$Port", "-i", $Key,
  "-o", "BatchMode=yes",
  "-o", "StrictHostKeyChecking=accept-new",
  "-o", "ConnectTimeout=8"
)

function Invoke-Guest {
  param([string]$Command)
  & ssh.exe @sshBase $target $Command
  if ($LASTEXITCODE -ne 0) { throw "ssh failed: $Command" }
}

Write-Host "== waiting for SSH on ${target}:$Port ==" -ForegroundColor Cyan
$up = $false
for ($i = 0; $i -lt 30; $i++) {
  & ssh.exe @sshBase $target "true" 2>$null
  if ($LASTEXITCODE -eq 0) { $up = $true; break }
  Start-Sleep -Seconds 3
}
if (-not $up) {
  throw "no SSH after 90s -- is the VM booted and is the key authorized?"
}

Write-Host "== copying current guest tooling into the VM ==" -ForegroundColor Cyan
& scp.exe @scpBase -r $guest "${target}:/root/"
if ($LASTEXITCODE -ne 0) { throw "scp guest failed" }
& scp.exe @scpBase $pub "${target}:/root/guest/id_openspan.pub"
if ($LASTEXITCODE -ne 0) { throw "scp key failed" }

Write-Host "== running provision.sh all ==" -ForegroundColor Cyan
Invoke-Guest "cd /root/guest && bash provision.sh all"

Write-Host "== verifying software state (no radio required) ==" -ForegroundColor Cyan
& ssh.exe @sshBase $target "bash /root/guest/verify-provision.sh"
$verifyRc = $LASTEXITCODE

Write-Host ""
if ($verifyRc -eq 0) {
  Write-Host "PROVISIONER OK (software checks passed)." -ForegroundColor Green
} else {
  Write-Host "PROVISIONER FAILED a software check (see above)." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Remaining MANUAL multi-device checks:"
Write-Host "  1. Attach one controller per HID device, plus the audio/scan controller; cold-reboot."
Write-Host "  2. Confirm every controller appears: python3 /opt/openspan/openspan_bt.py list"
Write-Host "  3. Configure each lane in the app, or run once per device:"
Write-Host "       /opt/openspan/set-hid-device.sh ID CONTROLLER_MAC PORT 'OpenSpan NAME'"
Write-Host "  4. Ensure the VM has a TCP NAT forward for every configured device port."
Write-Host "  5. Re-run verify-provision.sh; each configured openspanble@ID must be enabled."
Write-Host "  6. Pair each advertised device and test edge crossing, keyboard, mouse, and return."
Write-Host "  7. Connect the audio device and confirm clean playback alongside active HID lanes."
exit $verifyRc
