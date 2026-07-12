# cold-test.ps1 -- automate the OpenSpan provisioner cold-test.
#
# You provide ONE thing: a fresh, SSH-reachable Debian 12 VM with the host key
# (id_openspan.pub) in root's authorized_keys -- i.e. seeded the same way the
# original VM was. This script does everything else that doesn't need a human:
# copies guest/ + the key in, runs provision.sh, runs verify-provision.sh, and
# prints a PASS/FAIL report. The radio attach + iPad/earbud pairing stay manual
# (only a person can drive the Bluetooth radio).
#
#   .\cold-test.ps1 -Port 2223
#   .\cold-test.ps1 -Port 2223 -User someuser -Key .\other_key   # non-root
#
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
if (-not (Test-Path $guest)) { throw "guest/ not found under $RepoRoot" }
if (-not (Test-Path $pub))   { throw "id_openspan.pub missing -- run: ssh-keygen -y -f id_openspan > id_openspan.pub" }
if (-not (Test-Path $Key))   { throw "SSH key not found: $Key" }

$target  = "$User@$VmHost"
$sshBase = @("-p", "$Port", "-i", $Key, "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=8")
$scpBase = @("-P", "$Port", "-i", $Key, "-o", "StrictHostKeyChecking=accept-new")

function Invoke-Guest { param([string]$cmd) & ssh.exe @sshBase $target $cmd; if ($LASTEXITCODE -ne 0) { throw "ssh failed: $cmd" } }

Write-Host "== waiting for SSH on ${target}:$Port ==" -ForegroundColor Cyan
$up = $false
for ($i = 0; $i -lt 30; $i++) {
  & ssh.exe @sshBase $target "true" 2>$null
  if ($LASTEXITCODE -eq 0) { $up = $true; break }
  Start-Sleep -Seconds 3
}
if (-not $up) { throw "no SSH after 90s -- is the VM booted and is the key in root's authorized_keys?" }

Write-Host "== copying guest/ + key into the VM ==" -ForegroundColor Cyan
& scp.exe @scpBase -r $guest "${target}:/root/"
if ($LASTEXITCODE -ne 0) { throw "scp guest failed" }
& scp.exe @scpBase $pub "${target}:/root/guest/id_openspan.pub"
if ($LASTEXITCODE -ne 0) { throw "scp key failed" }

Write-Host "== running provision.sh all ==" -ForegroundColor Cyan
Invoke-Guest "cd /root/guest && bash provision.sh all"

Write-Host "== verifying (non-radio) ==" -ForegroundColor Cyan
& ssh.exe @sshBase $target "bash /root/guest/verify-provision.sh"
$verifyRc = $LASTEXITCODE

Write-Host ""
if ($verifyRc -eq 0) {
  Write-Host "PROVISIONER OK (non-radio checks passed)." -ForegroundColor Green
} else {
  Write-Host "PROVISIONER: some non-radio checks FAILED (see above)." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Remaining MANUAL steps (Bluetooth -- a human only):"
Write-Host "  1. Attach the BT dongle to this VM, then cold-reboot it."
Write-Host "  2. iPad Bluetooth -> tap 'OpenSpan Keyboard' -> pair : the mouse moves."
Write-Host "  3. Connect earbuds : PC audio plays clean."
exit $verifyRc
