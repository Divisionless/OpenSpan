# SPDX-License-Identifier: AGPL-3.0-or-later

<#
.SYNOPSIS
  Back up known-good, freeze a stable EsotericOS, and make Windows boot into it
  with EsotericOS in full control. Idempotent: running it again re-asserts the
  same outcome.

.WHAT IT DOES (nothing that touches the running app, VM, or radios)
  1. BACKS UP the current known-good: the boot registry keys (both Winlogon
     Shell values + the user Run key + the Fast Startup flag) and the live
     EsotericOS.exe, with hashes, into D:\_EsotericOS\backups\known-good-<stamp>.
  2. FREEZES the shell: copies the built fork into D:\_EsotericOS\shell\stable so
     the boot shell is a fixed known-good copy, isolated from future rebuilds.
  3. ASSERTS control for the NEXT sign-in:
       * HKCU Winlogon Shell -> the frozen fork (Explorer suppressed for this
         user; HKCU wins over HKLM and Windows Update / SFC never touch it).
       * HKLM Winlogon Shell -> explorer.exe, DELIBERATELY. The machine value
         is the safety net: any other account, or this account with the HKCU
         override removed, lands on a working Explorer desktop. The first
         version of this script set HKLM to the fork too (2026-08-16) -- that
         destroyed the net the moment it was needed. Never again.
       * the user Run key keeps launching EsotericOS.exe (displays, Bluetooth
         stand-down, bridge VM) -- the fork's StartupRunner runs it as shell.
       * Windows Fast Startup OFF, so every shutdown is a true cold boot -- the
         cold boot the VM and the passed-through radios need (no hybrid-resume
         wedge). This is the only firmware-adjacent setting that matters; it is
         a Windows power flag, NOT a BIOS change. NO BIOS CHANGE IS REQUIRED.
  4. VERIFIES the radios read-only and prints custody status (never changes a
     radio -- that stays your hands).
  5. Leaves the recovery hatch in place and prints it.

  It does NOT restart the machine. Restart when you are ready.

.RECOVERY if a sign-in ever comes up wrong
  Ctrl+Shift+Esc -> More details -> File -> Run new task -> explorer.exe
  then:  powershell -ExecutionPolicy Bypass -File D:\_EsotericOS\app\assert-control.ps1 -Release
  (-Release removes both shell overrides and returns Explorer.)
#>
param([switch]$Release)

$ErrorActionPreference = "Stop"

# ---- self-elevate (writing HKLM needs admin) --------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Elevating..."
    $args = @('-NoExit','-ExecutionPolicy','Bypass','-File', $PSCommandPath)
    if ($Release) { $args += '-Release' }
    Start-Process powershell -Verb RunAs -ArgumentList $args
    return
}

# ---- paths (all derived, nothing typed twice) -------------------------------
$appRoot   = Split-Path -Parent $PSCommandPath          # D:\_EsotericOS\app
$esRoot    = Split-Path -Parent $appRoot                # D:\_EsotericOS
$shellRepo = Join-Path $esRoot "shell"
$shellBuild= Join-Path $shellRepo "Cairo Desktop\Cairo Desktop\bin\x64\Release\net6.0-windows\win-x64"
$appExe    = Join-Path $appRoot "EsotericOS.exe"

# The freeze is VERSIONED: stable-<stamp>\, and HKCU Shell points at the new
# one. Never freeze onto a directory the running shell executes from -- the
# first design (a single stable\) robocopy-/MIR'd onto the LIVE shell's own
# locked files the moment the takeover succeeded. Old stable-* dirs are the
# rollback ladder; prune by hand when the newest has survived a sign-in.
$stamp   = (Get-Date).ToString("yyyyMMdd-HHmmss")
$shellStable = Join-Path $shellRepo ("stable-" + $stamp)
$stableShellExe = Join-Path $shellStable "EsotericOS.Shell.exe"

$runKey    = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$wlCU      = "HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Winlogon"
$wlLM      = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
$powerKey  = "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power"

function Line { Write-Host ("-" * 68) }
function Sha8($p) { if (Test-Path $p) { (Get-FileHash $p -Algorithm SHA256).Hash.Substring(0,8).ToLower() } else { "(absent)" } }

# ============================================================ RELEASE (undo) ==
if ($Release) {
    Line; Write-Host "RELEASE: returning control to Windows Explorer"
    if ((Get-ItemProperty -Path $wlCU -Name Shell -ErrorAction SilentlyContinue).Shell) {
        Remove-ItemProperty -Path $wlCU -Name Shell; Write-Host "  removed HKCU Winlogon Shell"
    }
    $lmShell = (Get-ItemProperty -Path $wlLM -Name Shell -ErrorAction SilentlyContinue).Shell
    if ($lmShell -and $lmShell -ne "explorer.exe") {
        Set-ItemProperty -Path $wlLM -Name Shell -Value "explorer.exe"; Write-Host "  HKLM Winlogon Shell -> explorer.exe"
    }
    Write-Host "Explorer returns at the next sign-in. The app + custody are untouched."
    return
}

# ================================================================ 1. BACKUP ==
$backup  = Join-Path $esRoot ("backups\known-good-" + $stamp)
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Line; Write-Host "1. BACKUP known-good -> $backup"

reg export "HKCU\Software\Microsoft\Windows NT\CurrentVersion\Winlogon" (Join-Path $backup "HKCU-Winlogon.reg") /y | Out-Null
reg export "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" (Join-Path $backup "HKLM-Winlogon.reg") /y | Out-Null
reg export "HKCU\Software\Microsoft\Windows\CurrentVersion\Run"        (Join-Path $backup "HKCU-Run.reg")      /y | Out-Null
reg export "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power" (Join-Path $backup "HKLM-Power.reg")  /y | Out-Null
if (Test-Path $appExe) { Copy-Item $appExe (Join-Path $backup "EsotericOS.exe") -Force }

$manifest = @()
$manifest += "EsotericOS known-good backup  " + $stamp
$manifest += "app  EsotericOS.exe     sha8 " + (Sha8 $appExe)
$manifest += "shell EsotericOS.Shell.exe  sha8 " + (Sha8 (Join-Path $shellBuild "EsotericOS.Shell.exe"))
$manifest += "alias CairoDesktop.exe      sha8 " + (Sha8 (Join-Path $shellBuild "CairoDesktop.exe"))
try { $manifest += "app git    " + (git -C $appRoot rev-parse --short HEAD 2>$null) } catch {}
try { $manifest += "shell git  " + (git -C $shellRepo rev-parse --short HEAD 2>$null) } catch {}
$manifest | Set-Content -Path (Join-Path $backup "MANIFEST.txt") -Encoding ASCII
Write-Host "  registry keys exported; EsotericOS.exe copied; MANIFEST.txt written"
$manifest | ForEach-Object { Write-Host ("  " + $_) }

# ========================================================= 2. FREEZE SHELL ==
Line; Write-Host "2. FREEZE the stable shell -> $shellStable"
if (-not (Test-Path (Join-Path $shellBuild "EsotericOS.Shell.exe"))) {
    Write-Host "  REFUSING: shell not built at $shellBuild"; return
}
robocopy $shellBuild $shellStable /MIR /NJH /NJS /NP /NDL /NFL | Out-Null
if ($LASTEXITCODE -ge 8) { Write-Host "  robocopy FAILED (code $LASTEXITCODE)"; return }
$global:LASTEXITCODE = 0
Write-Host ("  frozen: " + $stableShellExe + "  sha8 " + (Sha8 $stableShellExe))

# ======================================================= 3. ASSERT CONTROL ==
Line; Write-Host "3. ASSERT control for the next sign-in"

if (-not (Test-Path $wlCU)) { New-Item -Path $wlCU -Force | Out-Null }
Set-ItemProperty -Path $wlCU -Name Shell -Value ('"' + $stableShellExe + '"')
Write-Host "  HKCU Winlogon Shell -> the frozen fork (Explorer suppressed for this user)"

# HKLM stays explorer.exe ON PURPOSE -- it is the machine-wide safety net, and
# HKCU wins for this user anyway. Setting it to the fork (as the first version
# did) removes the one fallback that works when the fork cannot start.
$lmNow = (Get-ItemProperty -Path $wlLM -Name Shell -ErrorAction SilentlyContinue).Shell
if ($lmNow -ne "explorer.exe") {
    Set-ItemProperty -Path $wlLM -Name Shell -Value "explorer.exe"
    Write-Host "  HKLM Winlogon Shell restored -> explorer.exe (the safety net stands)"
} else {
    Write-Host "  HKLM Winlogon Shell = explorer.exe (safety net intact, untouched)"
}

if (Test-Path $appExe) {
    Set-ItemProperty -Path $runKey -Name "EsotericOS" -Value ('"' + $appExe + '"')
    Write-Host "  Run\EsotericOS -> EsotericOS.exe (StartupRunner launches it as shell)"
} else { Write-Host "  WARNING: EsotericOS.exe missing at $appExe" }

foreach ($dead in @("EsotericOS Shell","CairoShell","OpenSpan")) {
    if ((Get-ItemProperty -Path $runKey -Name $dead -ErrorAction SilentlyContinue).$dead) {
        Remove-ItemProperty -Path $runKey -Name $dead; Write-Host "  removed redundant Run\$dead"
    }
}

Set-ItemProperty -Path $powerKey -Name HiberbootEnabled -Value 0 -Type DWord
Write-Host "  Fast Startup OFF -> every shutdown is a true cold boot (radio/VM safe)"

# ========================================================= 4. VERIFY RADIOS ==
Line; Write-Host "4. VERIFY radios (read-only)"
$py = "C:\Python313\python.exe"; $custody = Join-Path $appRoot "win\radio_custody.py"
if ((Test-Path $py) -and (Test-Path $custody)) {
    $audit = & $py $custody audit 2>&1 | Out-String
    $notOwned = ($audit -split "`n") | Where-Object { $_ -match "verdict" -and $_ -notmatch "ESOTERICOS-CUSTODY" }
    if ($notOwned) {
        Write-Host "  NOT all radios are in EsotericOS custody:"
        $notOwned | ForEach-Object { Write-Host ("   " + $_.Trim()) }
        Write-Host "  Take custody yourself (app closed, VM off) with:"
        Write-Host "    $py $custody take <instance-id> --apply"
    } else {
        Write-Host "  all configured radios: ESOTERICOS-CUSTODY (bthusb never binds them)"
    }
} else { Write-Host "  (custody check skipped: python or radio_custody.py not found)" }

# ============================================================ 5. FIRMWARE ==
Line; Write-Host "5. FIRMWARE / BIOS"
Write-Host "  NO BIOS CHANGE IS REQUIRED for EsotericOS to take control."
try {
    $sb = Confirm-SecureBootUEFI 2>$null
    Write-Host ("  (info) UEFI Secure Boot: " + $sb + "  -- not relevant to the shell takeover")
} catch { Write-Host "  (info) legacy/BIOS boot or Secure Boot state unreadable -- not relevant" }

# ================================================================ REPORT ==
Line
Write-Host "DONE. On your next restart:"
Write-Host "  * Windows starts the EsotericOS shell (the frozen fork) -- no Explorer."
Write-Host "  * It launches EsotericOS.exe: your displays, Bluetooth stand-down, the"
Write-Host "    bridge VM, radios in custody, the portal."
Write-Host "  * Fast Startup is off (true cold boot)."
Write-Host ""
Write-Host "RECOVERY if a sign-in comes up wrong:"
Write-Host "  Ctrl+Shift+Esc -> File -> Run new task -> explorer.exe   then run:"
Write-Host "  powershell -ExecutionPolicy Bypass -File $PSCommandPath -Release"
Write-Host ""
Write-Host "Backup of the prior known-good is at: $backup"
Line
