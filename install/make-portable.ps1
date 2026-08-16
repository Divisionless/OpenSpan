<#
.SYNOPSIS
  Assemble EsotericOS-portable\ -- the folder you drop on another Windows PC.

.DESCRIPTION
  The second laptop should take five minutes, not an afternoon. This builds (or
  adopts) EsotericOS.exe and assembles a folder that contains the program and
  NOTHING ABOUT THIS MACHINE.

  WHAT IS EXCLUDED IS THE POINT OF THIS SCRIPT.

  The app anchors every data file on the folder its executable sits in, which is
  exactly what makes it portable -- and exactly what makes a careless copy leak.
  openspan_config.json is a picture of Doug's desk. openspan_settings.json holds
  the clipboard relay TOKEN. id_openspan is a private SSH key. node.json is this
  node's private identity, and peers.json is the shared secret of every pairing
  it has made -- copying those two would not create a second node, it would
  create a CLONE with the same key, and two machines answering to one identity
  is a pairing that silently swaps under you.

  So the exclusion list is explicit and it is TESTED: win\test_portable.py names
  every file that must never appear and fails if this script would ship one.
  A denylist that nobody checks is a wish.

  The portable folder is a FRESH NODE. On first run it generates its own key,
  finds its own monitors, and pairs with this machine over the LAN by showing a
  six-digit code on both screens.

.PARAMETER Exe
  An existing EsotericOS.exe to package. Default: build one with build_exe.py.

.PARAMETER Out
  Where to assemble. Default: <repo>\EsotericOS-portable.

.PARAMETER Zip
  Also produce EsotericOS-portable.zip beside the folder.

.PARAMETER Python
  Interpreter used for the build. Default C:\Python313\python.exe.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install\make-portable.ps1 -Zip
#>
param(
    [string]$Exe = "",
    [string]$Out = "",
    [switch]$Zip,
    [string]$Python = "C:\Python313\python.exe"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
if (-not $Out) { $Out = Join-Path $repo "EsotericOS-portable" }

# ---------------------------------------------------------------------------
# THE DENYLIST. Every entry is a file that is about THIS machine, or a secret,
# or state the new node must mint for itself. Kept as patterns, not filenames:
# ".prev" is a class of file build_exe.py creates, and naming one instance is
# how eight copies of a 68 MiB binary got into git history.
# ---------------------------------------------------------------------------
$excluded = @(
    "openspan_config.json",     # this desk's arrangement + monitor identities
    "openspan_settings.json",   # HOLDS THE CLIPBOARD RELAY TOKEN
    "bt_prefs.json",            # which Bluetooth devices this machine knows
    "module_settings.json",     # what the modules OBSERVED on this machine
    "node.json",                # this node's private key -- never cloned
    "peers.json",               # every pairing's shared secret
    "status.json",
    "status.json.new",
    "mode.txt",
    "audio_balance.txt",
    "audio_gain.txt",
    "traytest.txt",
    "id_openspan",
    "id_openspan.pub",
    "id_laptop",
    "id_laptop.pub",
    "portal.log",
    "audio_send.log",
    "boot.log",
    "openspan_frozen.log",
    "buildlog.txt"
)
$excludedPatterns = @(
    "*.log",          # every log names paths, devices and times on this PC
    "*.prev",         # kept builds, not shipped ones
    "*.lnk",          # Windows shortcuts embed the creating machine's IDs
    "*.pem",
    "*.key",
    "*.raw",          # the VM disk image
    "id_openspan*",
    "id_laptop*"
)
# Directories that never travel. vm\ and the .raw are the guest disk; profiles\
# are named pictures of this user's actual desk.
$excludedDirs = @("vm", "profiles", "build", "dist", "__pycache__",
                  ".git", ".worktrees", ".pytest_cache", "docs", "guest")

function Test-Excluded {
    param([string]$Name)
    if ($excluded -contains $Name) { return $true }
    foreach ($p in $excludedPatterns) { if ($Name -like $p) { return $true } }
    return $false
}

# ---------------------------------------------------------------------------
# 1. The executable.
# ---------------------------------------------------------------------------
if (-not $Exe) {
    $Exe = Join-Path $repo "EsotericOS.exe"
    if (-not (Test-Path $Exe)) {
        if (-not (Test-Path $Python)) { throw "no interpreter at $Python -- pass -Python or -Exe" }
        Write-Host "no EsotericOS.exe yet; building one..."
        & $Python (Join-Path $repo "build_exe.py")
        if ($LASTEXITCODE -ne 0) { throw "build_exe.py failed ($LASTEXITCODE)" }
    }
}
if (-not (Test-Path $Exe)) { throw "no executable to package: $Exe" }

# ---------------------------------------------------------------------------
# 2. Assemble, from an explicit ALLOWLIST.
#
# Copying the folder and then deleting the secrets is the wrong way round: it
# is one forgotten pattern away from shipping a private key, and the failure is
# silent. Nothing lands here that is not named.
# ---------------------------------------------------------------------------
if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
New-Item -ItemType Directory -Path $Out -Force | Out-Null

$manifest = @()

function Add-Item-Copy {
    param([string]$Source, [string]$RelTarget)
    $name = Split-Path -Leaf $Source
    if (Test-Excluded $name) {
        Write-Host ("SKIP (machine-specific): " + $name)
        return
    }
    if (-not (Test-Path $Source)) { return }
    $dest = Join-Path $Out $RelTarget
    $parent = Split-Path -Parent $dest
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Copy-Item -Path $Source -Destination $dest -Force
    $script:manifest += $dest
}

Add-Item-Copy $Exe "EsotericOS.exe"
Add-Item-Copy (Join-Path $repo "bake-in.ps1") "bake-in.ps1"
# swap-build.ps1 belongs here: it is the SAFE way to replace the exe on the
# portable node when a new build is copied over, and it refuses while the
# binary is running (matched by path, not by name). Without it the update
# procedure on the second machine is "overwrite it and hope".
Add-Item-Copy (Join-Path $repo "swap-build.ps1") "swap-build.ps1"
Add-Item-Copy (Join-Path $repo "LICENSE") "LICENSE"
Add-Item-Copy (Join-Path $PSScriptRoot "README-PORTABLE.md") "README-PORTABLE.md"

# brand\ -- the icons. The app falls back to a bundled icon without them, but
# the tray and the title bar are the difference between "an app" and "a stray
# exe", and they are a few KB.
$brand = Join-Path $repo "brand"
if (Test-Path $brand) {
    Get-ChildItem -Path $brand -File | ForEach-Object {
        Add-Item-Copy $_.FullName (Join-Path "brand" $_.Name)
    }
}

# win\modules\ is deliberately NOT copied: build_exe.py --add-data bundles the
# modules INSIDE the exe and module_host.bundled_root() reads them from
# sys._MEIPASS when frozen. A copy beside the exe would be a second, staler
# set of the same files.

# ---------------------------------------------------------------------------
# 3. Print the manifest: size and SHA-256 prefix for every file that shipped.
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "--- EsotericOS-portable manifest ---"
$total = 0
foreach ($f in ($manifest | Sort-Object)) {
    $item = Get-Item $f
    $hash = (Get-FileHash -Path $f -Algorithm SHA256).Hash.Substring(0, 12).ToLower()
    $rel = $item.FullName.Substring($Out.Length).TrimStart("\")
    $total += $item.Length
    "{0,-28} {1,12:N0}  {2}" -f $rel, $item.Length, $hash
}
Write-Host ""
"{0} files, {1:N1} MiB" -f $manifest.Count, ($total / 1MB)

# What was deliberately left out, said out loud. A silent exclusion list is
# indistinguishable from a list that did not run.
Write-Host ""
Write-Host "--- excluded by design (nothing machine-specific ships) ---"
foreach ($name in ($excluded | Sort-Object)) {
    $src = Join-Path $repo $name
    if (Test-Path $src) { "  present here, NOT shipped: $name" }
}
foreach ($d in $excludedDirs) {
    $src = Join-Path $repo $d
    if (Test-Path $src) { "  present here, NOT shipped: $d\" }
}

# ---------------------------------------------------------------------------
# 4. Optional zip.
# ---------------------------------------------------------------------------
if ($Zip) {
    $zipPath = "$Out.zip"
    if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
    Compress-Archive -Path (Join-Path $Out "*") -DestinationPath $zipPath
    $zi = Get-Item $zipPath
    $zh = (Get-FileHash -Path $zipPath -Algorithm SHA256).Hash.Substring(0, 12).ToLower()
    Write-Host ""
    "zip: {0}  {1:N1} MiB  {2}" -f $zi.FullName, ($zi.Length / 1MB), $zh
}

Write-Host ""
Write-Host "Copy the folder to the other PC and run EsotericOS.exe."
Write-Host "It generates its own node key on first run. Run bake-in.ps1 as"
Write-Host "administrator there once: it starts the app at sign-in and adds the"
Write-Host "Windows Firewall rule for the PROGRAM (the LAN service port is"
Write-Host "assigned by the OS and is different every launch, so a port rule"
Write-Host "would be stale by the next restart)."
