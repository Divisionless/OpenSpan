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

---

## ADDENDUM 2026-08-16 ~23:00 (Architect) -- the measured story corrects two claims

Evidence: Windows Event Log (System, EventID 1074), Cairo's own logs
(`C:\Users\Douglas Knoll\AppData\Local\Cairo Desktop\Logs\08-16-2026*.log`),
Task Scheduler info for "EsotericOS Shell (elevated)", live token reads via
`app\tools\proc-integrity.ps1` (new, read-only), and git history in both repos.

**Timeline as measured:**

1. **17:09** `assert-control.ps1` sets HKCU **and HKLM** Winlogon Shell to the
   frozen fork. (Defect: that removed the machine safety net. Fixed tonight --
   the script now forces HKLM back to `explorer.exe` and says why.)
2. **17:17:56** restart (initiated by CairoDesktop.exe) -> **17:18:49** sign-in
   with the fork as the Winlogon-started shell. **No log of this session
   survives** -- Cairo keeps one same-day backup slot and the 17:34 session
   overwrote it. What broke here was never recorded.
3. **17:34:34** the scheduled task "EsotericOS Shell (elevated)" (RunLevel
   Highest, installed via `tools\install-elevated-shell.ps1` as the fix
   attempt) starts CairoDesktop; Cairo logs "Application started" 17:34:28(*),
   `Running as shell: True`, from `shell\stable\`. This **elevated** instance
   is the one whose failures are on record: `runas` verb -> **"Class not
   registered"** for `Claude_pzs8sxrjxfjjc!Claude` (17:35:04) and
   `WindowsTerminal` (17:35:38, 17:36:30), plus a blank-named StartupRunner
   failure (17:34:35).
4. **17:51:15** manual `shutdown.exe` restart; **17:53** back to ALONGSIDE
   (explorer 17:53:15 Medium, CairoDesktop 17:53:30 Medium via Run key).

(*) log clock vs task clock differ by ~6s; same event.

**Correction 1 -- "the interactive logon token is full-admin" is contradicted
by measurement.** Tonight, explorer.exe started by Winlogon runs **Medium,
not-elevated** (`proc-integrity.ps1`, 22:52). Winlogon hands the shell the
FILTERED token on this box (`FilterAdministratorToken=1`). The High-integrity
CairoDesktop that was "measured" was in all likelihood the 17:34 task-launched
instance (RunLevel Highest is exactly "start High"), not the Winlogon-started
one from 17:18.

**Correction 2 -- the recorded failures are an ELEVATION problem in the other
direction.** "Class not registered" on `shell:appsFolder` activation is the
signature of AppX/packaged-app activation refusing an **elevated** caller (and
of Explorer's activation COM being absent). The elevated-shell fix attempt
therefore reproduced a launch failure rather than fixing one. OneDrive's
"can't start elevated" fits the same elevated parent.

**What remains genuinely unknown:** why the 17:18 Medium(-presumed) fork-as-
shell sign-in was broken. No log survives. Candidates: AppX activation without
Explorer's COM surface, StartupRunner failures, or something else entirely.
**Next armed sign-in must capture:** `proc-integrity.ps1` output within a
minute of desktop, and a copy of Cairo's live log before any restart rotates
it. The watchdog (`shell\tools\shell-watchdog.ps1`, installed 22:36) now
backstops that experiment.

## RESOLUTION 2026-08-16 23:16 -- the third attempt held

Plain unelevated arrangement (HKCU Shell -> `stable\CairoDesktop.exe` directly,
HKLM = explorer.exe net intact, elevated bootstrap inert). Restart 23:09, sign-in
23:15. Measured by first-light (`app\first-light\20260816-231605`): explorer NOT
running; the fork Winlogon-started at **Medium, not-elevated**, `Running as
shell: True`; EsotericOS.exe up **High/elevated** -- silent UAC
(`ConsentPromptBehaviorAdmin=0`) brokered its RUNASADMIN flag through the Medium
shell; BT stood down; OpenSpan-Codex running; watchdog "shell alive -- no
action". Doug: everything seemed to go really well.

The working Medium counter-example disproves the original theory outright, and
the afternoon's failures now attribute cleanly to the ELEVATED task-launched
instance (AppX refuses elevated callers). Whatever additionally went wrong at
the unrecorded 17:18 sign-in did not recur and is unknowable -- closed as
overtaken by evidence.

Open residue: StartupRunner logs `Failed to start program: ` (blank name) at
23:15:24, same as the 17:34 session, while everything that matters started
anyway. Tracked as a follow-up, not a blocker.

**Leftover state flagged:** the task "EsotericOS Shell (elevated)" is still
registered (Ready) while the elevated arrangement is NOT active -- HKCU Shell
is absent. Orphaned half of a removed arrangement; Doug to rule on keeping it
for the next experiment or unregistering (`install-elevated-shell.ps1 -Undo`
also resets Shell -- prefer plain `Unregister-ScheduledTask` if the goal is
only cleanup).
