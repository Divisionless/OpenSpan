# Swap a staged build into place.
#
# This exists because build_exe.py used to PRINT the swap as a bash one-liner
# joined with `&&`, which is a parser error in PowerShell 5.1 -- the shell
# actually in use here. A command that cannot be run is not an instruction,
# and it had been handed over twice.
#
# Everything is derived: the root is where this script sits, the names come
# from the files that are actually there. Nothing is written out twice.
#
#   .\swap-build.ps1                     swap and relaunch (refuses if running)
#   .\swap-build.ps1 -NoLaunch           swap only
#   .\swap-build.ps1 -CloseRunning       close the running app first (see below)
#   .\swap-build.ps1 -Elevated           relaunch as administrator
#   .\swap-build.ps1 -Name Foo           a build staged under another name
#
# -CloseRunning is a HOT SWAP, and it is deliberate about what it does NOT do:
# it does not go through the app's own full stop, because that powers off the
# bridge VM and the iPad then needs re-pairing (the app's own X dialog says
# so). It kills the app's process tree -- GUI, portal, audio -- and leaves the
# VM running; a fresh instance attaches to a VM that is already up (openspan.py
# starts the VM only `if not vm_running()`), so no re-pair.
#
# Two things the app's graceful stop would have done that a kill cannot:
#   * Spaces keeps its hidden-window handles in memory only. Kill it with
#     windows hidden and they STAY hidden with nothing left to show them. So:
#     the person at the desk turns Spaces off first (System > Window management)
#     -- this script cannot see hidden windows and will not guess.
#   * screen zoom can hold a desktop-scope magnification that outlives the
#     process. This script resets it to 1x itself, unconditionally; a no-op
#     when not zoomed.

param(
    [string]$Name = "EsotericOS",
    [switch]$NoLaunch,
    [switch]$CloseRunning,
    [switch]$Elevated
)

$ErrorActionPreference = "Stop"
$ROOT     = $PSScriptRoot
$live     = Join-Path $ROOT "$Name.exe"
$staged   = Join-Path $ROOT "$Name-next.exe"
$rollback = Join-Path $ROOT "$Name.exe.prev"

if (-not (Test-Path $staged)) {
    Write-Output "Nothing staged: $staged does not exist. Build first."
    exit 1
}

# IDENTIFY A RUNNING PROCESS BY ITS PATH, NEVER ITS NAME. On 2026-08-15
# `Get-Process EsotericOS` reported "not running" three times while
# EsotericOS-next.exe was driving the desk, because that is an exact-name
# match. Acting on that answer is how a build overwrote the benchmark.
function Running-Here {
    @(Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and (
            $_.Path -eq $live -or $_.Path -eq $staged) })
}

$running = Running-Here
if ($running.Count -gt 0) {
    if (-not $CloseRunning) {
        Write-Output "Still running, so nothing was touched:"
        $running | Select-Object -Unique Path | ForEach-Object {
            Write-Output ("  " + $_.Path) }
        Write-Output "Close it (or pass -CloseRunning), then run this again."
        exit 1
    }
    Write-Output "closing the running app (VM stays up):"
    foreach ($p in ($running | Sort-Object StartTime)) {
        Write-Output ("  pid {0}  {1}" -f $p.Id, $p.Path)
        # /T takes the whole tree: the GUI plus its --portal and --audio roles.
        # NO `2>&1` HERE. Under $ErrorActionPreference = "Stop", redirecting a
        # native command's stderr into the pipeline is a TERMINATING error in
        # PowerShell 5.1 -- and taskkill WILL write to stderr on the second
        # and third pass, because /T on the GUI already took its --portal and
        # --audio children with it. The first version of this line aborted the
        # script mid-swap: app dead, nothing preserved, nothing swapped, no
        # relaunch. Proven by the reviewer on this exact shell.
        try {
            $null = & taskkill /PID $p.Id /T /F
        } catch {
            # already gone (or not ours to kill); the wait below is the judge
        }
    }
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $deadline -and (Running-Here).Count -gt 0) {
        Start-Sleep -Milliseconds 300
    }
    if ((Running-Here).Count -gt 0) {
        Write-Output "REFUSING to swap: the app did not exit."
        exit 1
    }
    # Magnification can outlive the process; put it back to 1x regardless.
    # The interpreter is RESOLVED, not written out: the one hardcoded path in
    # this whole change was here, and a missing interpreter would have been a
    # terminating error AFTER the kill. Failure to unzoom is reported, never
    # fatal -- the swap is the job.
    $unzoom = Join-Path $ROOT "win\unzoom.py"
    if (Test-Path $unzoom) {
        $py = $null
        foreach ($candidate in @("C:\Python313\python.exe", "python.exe", "python")) {
            $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
            if ($cmd) { $py = $cmd.Source; break }
        }
        if ($py) {
            try {
                & $py $unzoom | ForEach-Object { Write-Output ("  " + $_) }
            } catch {
                Write-Output "  unzoom: could not run ($_) - if the desktop is magnified, Alt+scroll it back"
            }
        } else {
            Write-Output "  unzoom: no python found - if the desktop is magnified, Alt+scroll it back"
        }
    }
}

# Keep what is being replaced. A swap that cannot preserve refuses, rather
# than proceeding and hoping -- the outgoing build is someone's rollback.
if (Test-Path $live) {
    try {
        Copy-Item -LiteralPath $live -Destination $rollback -Force
        $h = (Get-FileHash $rollback -Algorithm SHA256).Hash.Substring(0, 16).ToLower()
        Write-Output "kept the outgoing build as $Name.exe.prev  (sha $h...)"
    } catch {
        Write-Output "REFUSING to swap: could not preserve $live ($_)"
        exit 1
    }
    Remove-Item -LiteralPath $live -Force
}

Move-Item -LiteralPath $staged -Destination $live -Force
$new = (Get-FileHash $live -Algorithm SHA256).Hash.Substring(0, 16).ToLower()
Write-Output "swapped in $Name.exe  (sha $new...)"

if (-not $NoLaunch) {
    if ($Elevated) {
        # UAC is off on this machine, so RunAs starts elevated without a
        # prompt; the app then skips its "not running as administrator" gate.
        Start-Process -FilePath $live -WorkingDirectory $ROOT -Verb RunAs | Out-Null
        Write-Output "launched (administrator)."
    } else {
        Start-Process -FilePath $live -WorkingDirectory $ROOT | Out-Null
        Write-Output "launched."
    }
}
