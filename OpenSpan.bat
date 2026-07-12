@echo off
rem Prefer the packaged single-file exe if it's been built; otherwise fall
rem back to the Python entry point via the unflagged interpreter copy.
if exist "%~dp0OpenSpan.exe" (
  start "" "%~dp0OpenSpan.exe"
) else (
  start "" "C:\Python313\openspanw.exe" "%~dp0win\openspan.py"
)
