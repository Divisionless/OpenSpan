# Installs the OpenSpanBoot startup task (runs the boot orchestrator at
# every startup as SYSTEM). Requires admin; the app invokes this elevated
# once, the first time you switch into Station mode.
schtasks /Create /TN "OpenSpanBoot" /TR "powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File D:\OpenSpan\OpenSpan-boot.ps1" /SC ONSTART /RU SYSTEM /RL HIGHEST /F
