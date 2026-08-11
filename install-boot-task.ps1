# Installs the OpenSpanBoot startup task (runs the boot orchestrator at
# every startup as SYSTEM). Requires admin; the app invokes this elevated
# once, the first time you switch into Station mode.
#
# The path is taken from where THIS script sits, not written out again. The
# tree moved once already (D:\OpenSpan -> D:\_EsotericOS\app, 2026-08-11) and
# a literal here would have re-installed the task pointing at the old root.
$boot = Join-Path $PSScriptRoot 'OpenSpan-boot.ps1'
if (-not (Test-Path $boot)) { throw "boot orchestrator not found beside this script: $boot" }
schtasks /Create /TN "OpenSpanBoot" /TR "powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File $boot" /SC ONSTART /RU SYSTEM /RL HIGHEST /F
