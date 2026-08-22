# SPDX-License-Identifier: AGPL-3.0-or-later
<#
.SYNOPSIS
  Start the EsotericOS control app elevated at every interactive sign-in.

.DESCRIPTION
  A normal HKCU Run entry starts with the shell's filtered token and cannot
  itself promise an elevated GUI. This installer creates one per-user logon
  task with RunLevel Highest, verifies its exact action and principal, and
  only then removes the legacy Run value. The task starts after a short delay
  so the EsotericOS Shell can establish the desktop first.

  Installing or removing the task requires an elevated PowerShell. Nothing
  currently running is stopped or launched; changes take effect at the next
  sign-in.
#>
[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$Undo,
    [string]$ExePath = '',
    [ValidateRange(0, 300)]
    [int]$DelaySeconds = 30
)

$ErrorActionPreference = 'Stop'
$TaskName = 'EsotericOS App (elevated)'
$RunKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$RunName = 'EsotericOS'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UserId = '{0}\{1}' -f $env:USERDOMAIN, $env:USERNAME
if (-not $ExePath) { $ExePath = Join-Path $RepoRoot 'EsotericOS.exe' }
$ExePath = [IO.Path]::GetFullPath($ExePath)

function Get-AppTask {
    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

function Get-RunValue {
    (Get-ItemProperty -LiteralPath $RunKey -Name $RunName `
        -ErrorAction SilentlyContinue).$RunName
}

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]$identity
    if (-not $principal.IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this installer from an elevated PowerShell.'
    }
}

function Assert-TaskContract {
    param([Parameter(Mandatory)]$Task)
    $actions = @($Task.Actions)
    $triggers = @($Task.Triggers)
    if ($Task.Principal.RunLevel -ne 'Highest') {
        throw "Task '$TaskName' is not RunLevel Highest."
    }
    if ($Task.Principal.UserId -ne $UserId) {
        throw "Task '$TaskName' belongs to '$($Task.Principal.UserId)', not '$UserId'."
    }
    if ($actions.Count -ne 1 -or
            [IO.Path]::GetFullPath([string]$actions[0].Execute) -ne $ExePath) {
        throw "Task '$TaskName' does not execute '$ExePath'."
    }
    if ($triggers.Count -ne 1 -or
            $triggers[0].CimClass.CimClassName -ne 'MSFT_TaskLogonTrigger') {
        throw "Task '$TaskName' does not have exactly one logon trigger."
    }
}

function Show-State {
    $task = Get-AppTask
    $run = Get-RunValue
    if ($task) {
        $action = @($task.Actions)[0]
        $trigger = @($task.Triggers)[0]
        "task      : $TaskName ($($task.State))"
        "run level : $($task.Principal.RunLevel)"
        "user      : $($task.Principal.UserId)"
        "action    : $($action.Execute)"
        "trigger   : $($trigger.CimClass.CimClassName), delay $($trigger.Delay)"
    } else {
        "task      : $TaskName (absent)"
    }
    'Run value : ' + $(if ($null -eq $run) { '(absent)' } else { $run })
}

if ($Check) {
    Show-State
    $task = Get-AppTask
    if (-not $task) { exit 1 }
    Assert-TaskContract -Task $task
    if ($null -ne (Get-RunValue)) {
        throw "Legacy Run value '$RunName' is still present."
    }
    exit 0
}

Assert-Administrator

if ($Undo) {
    $task = Get-AppTask
    if ($task) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        "removed task '$TaskName'"
    } else {
        "task '$TaskName' was already absent"
    }
    Show-State
    exit 0
}

if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
    throw "Executable not found: $ExePath"
}

$action = New-ScheduledTaskAction -Execute $ExePath `
    -WorkingDirectory (Split-Path -Parent $ExePath)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $UserId
$trigger.Delay = 'PT{0}S' -f $DelaySeconds
$principal = New-ScheduledTaskPrincipal -UserId $UserId `
    -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew -Priority 4 -DontStopOnIdleEnd `
    -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action `
    -Trigger $trigger -Principal $principal -Settings $settings -Force `
    -Description 'Starts the EsotericOS control GUI with the full administrator token at interactive sign-in.' | Out-Null

$installed = Get-AppTask
Assert-TaskContract -Task $installed

# The verified task is now the sole automatic launch owner. Removing this only
# after verification makes a registration failure leave the old startup route
# intact instead of leaving the app absent at next sign-in.
if ($null -ne (Get-RunValue)) {
    Remove-ItemProperty -LiteralPath $RunKey -Name $RunName
    "removed legacy Run value '$RunName'"
}

"installed task '$TaskName' (Highest, interactive logon, ${DelaySeconds}s delay)"
Show-State
