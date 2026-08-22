# SPDX-License-Identifier: AGPL-3.0-or-later
<#
.SYNOPSIS
  Keep the EsotericOS LAN firewall exception bound to the installed program.

.DESCRIPTION
  The LAN node listens on an OS-assigned TCP port which changes every launch,
  and also uses mDNS. A fixed-port exception cannot cover that contract. This
  installer therefore owns two Private-profile PROGRAM rules for the exact
  executable: inbound from LocalSubnet and outbound.

  Re-running the installer is the update path. If a new build has a new file
  name, the managed rules are replaced with that exact path before obsolete
  per-build rules are removed. This prevents Windows Security Alert from
  treating every acceptance build as an unrelated application.
#>
[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$Undo,
    [string]$ExePath = ''
)

$ErrorActionPreference = 'Stop'
$InboundName = 'EsotericOS LAN inbound'
$OutboundName = 'EsotericOS LAN outbound'
$InboundId = 'EsotericOS-LAN-In'
$OutboundId = 'EsotericOS-LAN-Out'
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $ExePath) { $ExePath = Join-Path $RepoRoot 'EsotericOS.exe' }
$ExePath = [IO.Path]::GetFullPath($ExePath)

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]$identity
    if (-not $principal.IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this installer from an elevated PowerShell.'
    }
}

function Get-RuleById {
    param([string]$Id)
    @(Get-NetFirewallRule -Name $Id -ErrorAction SilentlyContinue)
}

function Test-RuleContract {
    param(
        [string]$Id,
        [string]$Direction,
        [string]$RemoteAddress
    )
    $rules = @(Get-RuleById $Id)
    if ($rules.Count -ne 1) { return $false }
    $rule = $rules[0]
    $program = @($rule | Get-NetFirewallApplicationFilter)
    $ports = @($rule | Get-NetFirewallPortFilter)
    $addresses = @($rule | Get-NetFirewallAddressFilter)
    if ($program.Count -ne 1 -or $ports.Count -ne 1 -or
            $addresses.Count -ne 1) { return $false }
    $actualProgram = [string]$program[0].Program
    try { $actualProgram = [IO.Path]::GetFullPath($actualProgram) }
    catch { return $false }
    $remote = @($addresses[0].RemoteAddress)
    return (
        $actualProgram -eq $ExePath -and
        [string]$rule.Direction -eq $Direction -and
        [string]$rule.Action -eq 'Allow' -and
        [string]$rule.Enabled -eq 'True' -and
        [string]$rule.Profile -eq 'Private' -and
        [string]$ports[0].Protocol -eq 'Any' -and
        $remote.Count -eq 1 -and
        [string]$remote[0] -eq $RemoteAddress)
}

function Test-InstalledContract {
    (Test-RuleContract -Id $InboundId -Direction 'Inbound' `
        -RemoteAddress 'LocalSubnet') -and
    (Test-RuleContract -Id $OutboundId -Direction 'Outbound' `
        -RemoteAddress 'Any')
}

function Remove-ManagedRules {
    foreach ($id in @($InboundId, $OutboundId)) {
        Get-RuleById $id | Remove-NetFirewallRule
    }
}

function Remove-ObsoleteRules {
    $removed = 0
    foreach ($rule in @(Get-NetFirewallRule -ErrorAction SilentlyContinue)) {
        if ($rule.Name -in @($InboundId, $OutboundId)) { continue }
        $display = [string]$rule.DisplayName
        $legacyName = $display -in @('EsotericOS', 'OpenSpan clipboard')
        $promptRule = $false
        # Name first: asking Windows for the application filter of every
        # firewall rule takes minutes on a normal installation. Only an
        # EsotericOS-named prompt rule needs that comparatively expensive
        # second test.
        if ($display -match '^esotericos($|[-.])') {
            $program = [string](@(
                $rule | Get-NetFirewallApplicationFilter)[0].Program)
            $leaf = try { [IO.Path]::GetFileName($program) } catch { '' }
            $promptRule = $leaf -like 'EsotericOS*.exe'
        }
        if (-not ($legacyName -or $promptRule)) { continue }
        $rule | Remove-NetFirewallRule
        $removed++
    }
    return $removed
}

function Show-State {
    if (Test-InstalledContract) {
        "firewall : exact Private-profile program rules for $ExePath"
    } else {
        "firewall : missing or stale for $ExePath"
    }
}

if ($Check) {
    Show-State
    if (-not (Test-InstalledContract)) { exit 1 }
    exit 0
}

Assert-Administrator

if ($Undo) {
    Remove-ManagedRules
    $removed = Remove-ObsoleteRules
    "removed EsotericOS LAN firewall rules ($removed obsolete rule(s))"
    exit 0
}

if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
    throw "Executable not found: $ExePath"
}

if (-not (Test-InstalledContract)) {
    Remove-ManagedRules
    try {
        New-NetFirewallRule -Name $InboundId -DisplayName $InboundName `
            -Group 'EsotericOS' -Direction Inbound -Action Allow -Enabled True `
            -Profile Private -Program $ExePath -Protocol Any `
            -RemoteAddress LocalSubnet `
            -Description 'Allows paired EsotericOS nodes on the local private network.' `
            | Out-Null
        New-NetFirewallRule -Name $OutboundId -DisplayName $OutboundName `
            -Group 'EsotericOS' -Direction Outbound -Action Allow -Enabled True `
            -Profile Private -Program $ExePath -Protocol Any -RemoteAddress Any `
            -Description 'Allows EsotericOS LAN discovery and paired-node traffic.' `
            | Out-Null
    } catch {
        Remove-ManagedRules
        throw
    }
}

if (-not (Test-InstalledContract)) {
    throw 'Firewall rules were created but failed exact contract verification.'
}
$removed = Remove-ObsoleteRules
"installed EsotericOS LAN program rules for $ExePath (Private/LocalSubnet; removed $removed obsolete rule(s))"
Show-State
