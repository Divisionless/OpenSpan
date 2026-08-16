<#
.SYNOPSIS
  Bake EsotericOS into the desktop: start it at sign-in, on the desktop, every time.

.DESCRIPTION
  Writes HKCU\Software\Microsoft\Windows\CurrentVersion\Run : EsotericOS = "<this folder>\EsotericOS.exe".
  Per-user, no admin needed to write. Both Explorer and EsotericOS Shell run the Run key at
  sign-in, so this holds before and after the shell swap. UAC is off on this machine, so the
  process comes up elevated as the app requires (UIPI: hooks die under elevated windows otherwise).

  -Check : report only.   -Undo : remove the value.
#>
param([switch]$Check, [switch]$Undo)
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$exe = Join-Path $PSScriptRoot 'EsotericOS.exe'
$cur = (Get-ItemProperty -Path $key -Name EsotericOS -ErrorAction SilentlyContinue).EsotericOS
"Run\EsotericOS : " + $(if ($null -eq $cur) { '(absent)' } else { $cur })
if ($Check) { exit 0 }
if ($Undo) {
    if ($null -ne $cur) { Remove-ItemProperty -Path $key -Name EsotericOS; "removed" } else { "nothing to remove" }
    exit 0
}
if (-not (Test-Path $exe)) { "no EsotericOS.exe beside this script: $exe"; exit 1 }
$want = '"' + $exe + '"'
if ($cur -ne $want) {
    Set-ItemProperty -Path $key -Name EsotericOS -Value $want
    "Run\EsotericOS -> $want"
} else { "already baked in" }
