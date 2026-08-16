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
#   .\swap-build.ps1              swap and relaunch
#   .\swap-build.ps1 -NoLaunch    swap only
#   .\swap-build.ps1 -Name Foo    a build staged under another name

param(
    [string]$Name = "EsotericOS",
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$ROOT    = $PSScriptRoot
$live    = Join-Path $ROOT "$Name.exe"
$staged  = Join-Path $ROOT "$Name-next.exe"
$rollback= Join-Path $ROOT "$Name.exe.prev"

if (-not (Test-Path $staged)) {
    Write-Output "Nothing staged: $staged does not exist. Build first."
    exit 1
}

# IDENTIFY A RUNNING PROCESS BY ITS PATH, NEVER ITS NAME. On 2026-08-15
# `Get-Process EsotericOS` reported "not running" three times while
# EsotericOS-next.exe was driving the desk, because that is an exact-name
# match. Acting on that answer is how a build overwrote the benchmark.
$running = @(Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and (
        $_.Path -eq $live -or $_.Path -eq $staged) })
if ($running.Count -gt 0) {
    Write-Output "Still running, so nothing was touched:"
    $running | Select-Object -Unique Path | ForEach-Object {
        Write-Output ("  " + $_.Path) }
    Write-Output "Close it, then run this again."
    exit 1
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
    Start-Process -FilePath $live -WorkingDirectory $ROOT | Out-Null
    Write-Output "launched."
}
