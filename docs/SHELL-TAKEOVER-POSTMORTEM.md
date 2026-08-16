2026-08-16 evening -- shell takeover backed off to ALONGSIDE, root cause found.

Doug restarted into the fork-as-shell (HKCU+HKLM Winlogon Shell). It came up
BROKEN: apps would not open, OneDrive errored "can't start elevated", the Claude
app "cairo doesn't have authority". Measured (win/../scratchpad diag): CairoDesktop
ran at HIGH integrity as the shell (manifest is asInvoker, so the interactive
logon token is full-admin -- Doug's env runs elevated by design). A High-integrity
shell cannot broker app launches: RUNASADMIN apps (his Claude CLI, the EsotericOS
app, presumably the Forge) fail to elevate through it, and OneDrive refuses an
elevated parent. Also Win+Shift+S is an Explorer-provided hotkey and dies with
Explorer.

Decision: revert to ALONGSIDE mode -- Explorer is the shell/service layer (medium
integrity, brokers UAC elevation, owns Win+Shift+S and the tray), EsotericOS +
Cairo run on top for the experience. This is the config that worked earlier today
and gives Doug Forge + Claude + screenshot + elevated launches. Full Explorer
suppression is deferred until the fork handles screenshot, elevation brokering,
and tray itself (tracked). Registry: HKCU Shell cleared, HKLM=explorer.exe, Cairo
+ app back on the Run key. assert-control.ps1 -Release did the shell revert.
Radios released (0 captured) before the restart.
