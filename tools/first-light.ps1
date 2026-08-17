<#
.SYNOPSIS
  Evidence capture for an armed shell sign-in: record what the shell actually
  is, at what integrity, before anything can rotate or restart it away.

.DESCRIPTION
  The 2026-08-16 takeover attempt taught this the hard way: Cairo keeps ONE
  same-day log backup, so the session that mattered was overwritten by the
  troubleshooting session that followed, and the original failure was never
  recorded. This script is the camera that is already rolling.

  Runs as a logon scheduled task ("EsotericOS First Light"), 60s after
  sign-in. Read-only except for its own output directory. It:
    1. snapshots process list (shell candidates) with INTEGRITY + elevation
       via tools\proc-integrity.ps1
    2. reads both Winlogon Shell values and the Run key
    3. copies TODAY's Cairo logs out of the rotation's reach
    4. appends a dated report to D:\_EsotericOS\app\first-light\<stamp>\

  Install/remove (the task survives until removed; each sign-in gets a folder):
    .\first-light.ps1 -Install
    .\first-light.ps1 -Uninstall
    .\first-light.ps1 -Check
    .\first-light.ps1 -Run -DelaySeconds 0     capture right now
#>
param([switch]$Install, [switch]$Uninstall, [switch]$Check, [switch]$Run,
      [int]$DelaySeconds = 60)

$ErrorActionPreference = 'Continue'   # capture must not die on one bad read
$TaskName = 'EsotericOS First Light'
$AppRoot  = Split-Path -Parent $PSScriptRoot                 # D:\_EsotericOS\app
$OutRoot  = Join-Path $AppRoot 'first-light'
$CairoLogs = Join-Path $env:LOCALAPPDATA 'Cairo Desktop\Logs'

if ($Run) {
    if ($DelaySeconds -gt 0) { Start-Sleep -Seconds $DelaySeconds }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $out = Join-Path $OutRoot $stamp
    New-Item -ItemType Directory -Force -Path $out | Out-Null
    $report = Join-Path $out 'report.txt'

    "EsotericOS first light  $stamp" | Set-Content $report -Encoding utf8
    "" | Add-Content $report

    "== integrity (proc-integrity.ps1) ==" | Add-Content $report
    & (Join-Path $PSScriptRoot 'proc-integrity.ps1') -Names explorer,CairoDesktop,EsotericOS,wscript 2>&1 |
        Add-Content $report

    "" | Add-Content $report
    "== registry ==" | Add-Content $report
    $cu = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Winlogon' -Name Shell -ErrorAction SilentlyContinue).Shell
    $lm = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon' -Name Shell -ErrorAction SilentlyContinue).Shell
    ("HKCU Winlogon Shell : " + $(if ($null -eq $cu) { '(absent)' } else { $cu })) | Add-Content $report
    ("HKLM Winlogon Shell : " + $(if ($null -eq $lm) { '(absent)' } else { $lm })) | Add-Content $report
    $runProps = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -ErrorAction SilentlyContinue
    if ($runProps) {
        $runProps.PSObject.Properties |
            Where-Object { $_.Name -notmatch '^PS' } |
            ForEach-Object { ("Run\{0} = {1}" -f $_.Name, $_.Value) } |
            Add-Content $report
    }

    "" | Add-Content $report
    "== cairo logs copied ==" | Add-Content $report
    if (Test-Path $CairoLogs) {
        $today = Get-Date -Format 'MM-dd-yyyy'
        $files = Get-ChildItem $CairoLogs -Filter "$today*.log" -ErrorAction SilentlyContinue
        foreach ($f in $files) {
            Copy-Item $f.FullName (Join-Path $out $f.Name) -Force
            ("copied " + $f.Name + "  (" + $f.Length + " bytes, written " + $f.LastWriteTime.ToString('HH:mm:ss') + ")") | Add-Content $report
        }
        if (-not $files) { "(no Cairo log for today)" | Add-Content $report }
    } else { "(Cairo log dir absent)" | Add-Content $report }

    "" | Add-Content $report
    "== boot.log tail ==" | Add-Content $report
    $bl = Join-Path $AppRoot 'boot.log'
    if (Test-Path $bl) { Get-Content $bl -Tail 5 | Add-Content $report }

    Get-Content $report
    exit 0
}

if ($Install) {
    $ps  = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arg = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Run -DelaySeconds {1}' -f $PSCommandPath, $DelaySeconds
    $user      = '{0}\{1}' -f $env:USERDOMAIN, $env:USERNAME
    $action    = New-ScheduledTaskAction -Execute $ps -Argument $arg
    $trigger   = New-ScheduledTaskTrigger -AtLogOn -User $user
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew -StartWhenAvailable
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force `
        -Description 'Records shell identity, integrity, registry and Cairo logs 60s after sign-in. Read-only except its own output folder.' | Out-Null
    "installed task '$TaskName' (delay ${DelaySeconds}s) for $user"
    $Check = $true
}

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false; "removed task '$TaskName'"
    } else { 'task not present' }
    $Check = $true
}

$t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
'task     : ' + $(if ($t) { $t.State } else { 'NOT INSTALLED' })
'output   : ' + $OutRoot
if (Test-Path $OutRoot) {
    Get-ChildItem $OutRoot -Directory | Sort-Object Name -Descending | Select-Object -First 3 |
        ForEach-Object { '  ' + $_.Name }
}
