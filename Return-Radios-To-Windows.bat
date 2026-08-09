@echo off
rem Extreme-failure give-back: returns every Bluetooth radio to Windows.
rem Works even if OpenSpan itself is broken or gone. Safe to run any time.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0win\return_radios.ps1" %*
pause
