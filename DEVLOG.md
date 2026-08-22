# OpenSpan — Devlog

## 2026-08-21 — Desktop is not Windows primary

The Desk arrangement now models two independent roles on each local display.
Windows owns **PRIMARY**, shown as a white labelled edge at the top of its
rectangle. EsotericOS owns **DESKTOP**, shown as a violet labelled edge at the
bottom. A screen may wear either role or both. Right-clicking a local screen
offers **Use as EsotericOS Desktop**; it never changes Windows display settings
or the Windows primary flag.

The distinction is functional, not decorative. The built-in EsotericOS surface
now sizes and docks against the selected Desktop monitor's real `rcWork` rather
than `SPI_GETWORKAREA`, which only describes primary. The choice lives in
`openspan_settings.json`, outside arrangement profiles. It is persisted through
the existing stable monitor identity model with the GDI name and last virtual
position as compatibility/twin-panel tiebreakers. If that panel is detached,
the effective Desktop falls back to current primary without overwriting the
choice; reconnecting or a `DISPLAYn` rename restores the physical panel.

Focused arrangement, context-menu, profile, and on-desktop suites pass. A
read-only live probe resolved all three attached panels to separate durable
preferences and separate work areas. The complete 59-file run reached 55 pass;
one randomized LAN signature case passed on immediate rerun, while three
unrelated live-tree checks remain red: the PC-block and portal-invariant suites
disagree with the arrangement currently on disk, and repository hygiene names
the pre-existing `assert-control.ps1` output paths. None is in this feature's
write boundary.

The acceptance build is
`D:\_EsotericOS\app\EsotericOS-desktop-role.exe` (73,474,308 bytes,
SHA-256 `38ed0489f72b3d9febdee9bbcc390b5a6003dd05db6e14fd26cc990d63ff9206`).
PyInstaller completed successfully and the archive contains `openspan`,
`on_desktop`, and `monitor_identity`. For restart acceptance, the existing
`HKCU\...\Run\EsotericOS` value now points to that exact staged executable;
the prior Run and user Winlogon keys are exported under
`D:\_EsotericOS\backups\desktop-role-20260821-214717`. The running app and
shell, shell pointers, VM, portal, and radios were not touched. Files
implementation remains paused.

---

## 2026-08-21 — The shell receives its own public identity

De-Cairo II replaces the fork's public executable and manifest identity with
`EsotericOS.Shell.exe` and `Divisionless.EsotericOS.Shell`. Internal
`CairoDesktop.*` namespaces and serialized setting keys remain as compatibility
ABI for this release; a byte-identical `CairoDesktop.exe` apphost alias protects
existing boot entries for one accepted release.

The new startup bridge acquires both the canonical and legacy mutexes and
copies legacy settings, logs, extensions, themes, and stacks into
`%LOCALAPPDATA%\EsotericOS\Shell` and `%APPDATA%\EsotericOS\Shell`. Migration
is copy-only, per-file atomic, idempotent, and target-wins: it never modifies or
deletes the legacy data. Autorun, watchdog, First Light, surface-mode detection,
restart, and shell bootstrap tooling recognize the transition while writing
only the canonical identity. The existing one-time read-only
`CairoAppConfig.xml` applications migration remains deliberately available.

Tracked AppGrabber source and resources, the obsolete launch verifier, the
upstream NSIS scripts, and Cairo WinGet publication are gone. All 19 language
dictionaries remain, with dead AppGrabber, Welcome, and updater resources
removed and live product text changed to EsotericOS Shell. The repository is
now consistently AGPL-3.0-or-later while preserving Cairo and ManagedShell
attribution in NOTICE.

Independent verification passed: net6.0-windows x64 builds with the five
existing warnings and no errors; net480 x64 builds with no warnings or errors;
the deterministic no-UI harness passes 4,722 assertions; both framework
outputs contain byte-identical canonical and compatibility apphosts; pack URI,
localization, runtime dependency, script syntax, licence-header, and diff
audits pass. The ignored `.verify` tree and ignored AppGrabber `bin`/`obj`
residue are not referenced or shipped, but the execution policy rejected their
validated targeted recursive deletion. The live shell, existing stable trees,
registry, scheduled tasks, and processes were not changed.

Shell commit `b03d004` was first frozen without arming to
`stable-20260821-143851`; canonical and compatibility executables both hash to
SHA-256 `71f47a2cde6359811d02a2a3ba589cb9580b16d2b84bde09826f5145038fa779`.
Doug then explicitly ordered it armed. The prior boot state was exported to
`D:\_EsotericOS\backups\known-good-20260821-145513`, and HKCU Winlogon now
points to that candidate's `EsotericOS.Shell.exe`. HKLM remains `explorer.exe`,
Fast Startup remains off, app autorun plus First Light and the shell watchdog
are ready, and the current August 19 shell process remains untouched. The next
system restart is the live-acceptance boundary.

The Files follow-up is now framed as an architectural extraction before more
UI work: an EsotericOS-owned identity/navigation/operation-ledger core, a
Windows adapter for NTFS/USN, IFileOperation, Restart Manager, and watchers,
and WPF panes consuming those boundaries rather than expanding the existing
window-owned monolith. Doug paused Files implementation until after this shell
restart acceptance; no Files source changed in this turn.

---

## 2026-08-19 — Programs leaves Cairo AppGrabber

Doug rejected v3.143 because it repaired a handler while leaving Cairo's
AppGrabber in authority. v3.144 replaces that runtime architecture with the
standalone `EsotericOS.Shell.Applications` assembly. It owns immutable launch
identity, live Start Menu and AppX discovery, ShellExecuteEx and
IApplicationActivationManager activation, search, retained groups, and Quick
Launch persistence. Existing Cairo categories and pins are read once for
migration; the new store is `%APPDATA%\EsotericOS\applications.json`, and the
live catalog is rediscovered instead of frozen into that file.

The executable, MenuBar, Taskbar, settings, open-file path, task buttons, and
task thumbnails now have no AppGrabber or old AppLauncher dependency. Mouse,
Enter, detached context-menu, Quick Launch, task pin, and direct-path routes
all cross the EsotericOS-owned typed boundary. Adversarial review corrected
three release defects before freezing: transient discovery was initially being
persisted, `.lnk` arguments could have been applied twice, and the grouped UI
had lost its visible TabControl template.

The deterministic no-UI harness passes 607 assertions. Full net6.0-windows x64
build passes with zero errors and the five existing warnings; the new assembly,
MenuBar, and Taskbar each pass net480 with zero warnings and errors. Source and
built dependency audits both exclude `CairoDesktop.AppGrabber`. Shell
`5f51761`, executable SHA8 `31a15c8a`, is frozen and armed at
`stable-20260819-232647`. Live verification passed after the 2026-08-21
restart: First Light recorded the new shell at Medium integrity with Explorer
absent, the built dependency graph loaded the EsotericOS assembly without
AppGrabber, migration retained 20 entries, five Quick Launch pins, and seven
groups, and Doug opened Google Chrome successfully from Programs. The VM,
portal, audio, and all three captured radios recovered through the normal host
restart path.

---

## 2026-08-19 — Programs becomes one observable command

Google Chrome exposed a second launch failure after the v3.142 activation
work. The exact Start Menu shortcut launched directly, and the same
`ShellExecuteEx` flags succeeded through a headless probe, but clicking its
exact Programs entry created no process. That seals the failure boundary before
`AppLauncher`; the retired routed WPF event had no observation point, so the
record does not pretend to name a narrower cause.

v3.143 replaces that dispatch system. Category entries, search mouse, search
Enter, context-menu Open, and Quick Launch now send the displayed
`ApplicationInfo` object through one service-owned typed command. Detached
context menus bind both command and parameter explicitly through their
placement target. Every attempt logs name, path, target, dispatch, and the
accepted/failed result while preserving v3.142's `IApplicationActivationManager`
AppX door and `ShellExecuteEx` file/shortcut door.

The no-UI contract harness passes all six routes; MenuBar and Taskbar compile
cleanly for net480 and net6.0-windows; the full x64 desktop build succeeds at
the existing warning baseline. Shell `062a317`, executable SHA8 `6df60468`, is
frozen to `stable-20260819-195930` and armed for the next sign-in. The running
PID 6220 remains untouched on `stable-20260817-175603`. Chrome by mouse and
Enter, plus packaged Claude, remain the live acceptance checks.

---

## 2026-08-17 small hours — first morning under our own shell

The first full working session with the fork as the Windows shell, and it held
through real weather: a WiFi outage, two restarts, and a Bluetooth regression —
none of them ours, and for the first time we could *prove* it fast.

**Win+Shift+S came home.** Explorer owned the OS binding and died with it; the
chord now lives in `ShellHotKeyService` beside the fork's other reclaimed Win
chords, launching `ms-screenclip:` with a `SnippingTool.exe` fallback
(shell `be58e8c`). Registered at first light: 00:48:38.

**The headphone teardown was us — and the fix restored a law, not a guess.**
Doug's ears (`Pairing · Connected · Pairing · Pairing`) plus the guest journal
convicted `btready.sh`: its unconditional `systemctl restart openspan-wireplumber`
fired 2.1s after the earbuds' A2DP stream landed, tore it down, and desynced
the release handshake. Deeper: the app's READY was derived from *ssh answering*,
not from the boot helper finishing — two components that had never heard of
each other. Both ends fixed (`d173c20`, `68db740`): completion marker + gated
READY (210s-bounded, exits via trap), restarts conditional on no live A2DP
transport, and the ✓ now re-verifies the *transport* 7s later with a capped
retry. The Jul-6 dual-use law — proven ground, wrongly accused mid-triage,
reaffirmed from this very file — is now commented at the top of the script it
convicts. Dual-use radio stands.

**The 01:19 network loss was not us, and we can say so with evidence.** Full
audit (five agents, ~460k tokens, board v3.140): upstream AP outage —
both laptops dropped, reconnects failed against a silent AP for 100s, return
on a cached lease. EsotericOS exonerated in code (zero adapter-touching paths)
and in logs (VM NAT-only, clean USB the whole window). Chronic bystanders
filed: Killer helper crashes every boot; VBoxUSBMon grumbles at shutdown while
the VM holds radios; portal.log is 54MB, unrotated, and timestampless — the
one log that couldn't be windowed, now a housekeeping item.

**One surface, staged.** Doug's doctrine ("that IS EsotericOS — it and the
shell should breathe as one surface") is implemented shell-aware: under our
shell the app builds no minimize, no X, and refuses WM_CLOSE; under Explorer —
the debugging visit — it stays a mortal window (`0d6c5d4`). Deployed by
on-disk rename-swap beside the running process, per Doug's ruling that the
test rides a normal restart with no mid-session variables.

**Crystallized 02:18**: `known-good-20260817-021850` — app `635d9062` @
`ecd48f0`, shell `42ee11b3` @ `be58e8c`. The next restart is one test with
three answers in it: the ear test, the surface, and a boot with every fix
aboard.

**And then the machine learned to photograph itself.** The 02:13 restart
passed everything — ears included, journal-proven: the wireplumber bounce
finished before READY invited the earbuds in, and they held. But Win+Shift+S
exposed the last Microsoft dependency: `ms-screenclip:` throws `0x87b20c15`
in an Explorer-less session — packaged snipping cannot be reliably invoked
on this desktop, ever. Doug's ruling: "get rid of our dependency on this old
snipping crap." Fifty minutes later, **EsotericOS Capture** (shell `b02fc50`
+ `1f06264`, ~275k Opus, legacy capture module mined as reference and
improved on — per-monitor overlays where legacy's spanning window had no
honest DPI): Win+Shift+S draws a boundary on a frozen frame, Win+Alt+Shift+S
takes the screen under the pointer, both to clipboard + PNG, Microsoft's
path deleted from the build. And the chords live in
**`D:\_EsotericOS\config\bindings.json`** — a new EsotericOS-wide bindings
home, all eight shell chords in one flat versionable map, outside Cairo's
doomed settings store. Christened by Doug on the first press of each, and
the proof artifact is perfect: a screenshot of EsotericOS's own Desk
surface, taken by EsotericOS, on the shell that is EsotericOS.

**Also this night:** the 01:19 WiFi loss on two laptops was audited to a
verdict — upstream AP outage, EsotericOS exonerated in code and logs
(v3.140, ~460k tokens of evidence). Surface mode is live: under our shell
the app builds no X and refuses WM_CLOSE. **Crystallized 02:46**:
`known-good-20260817-024628` — app `635d9062`, shell `4facba00`, verified
live by Doug's own hands before the seal.

---

## 2026-08-16 night — the takeover postmortem corrected by measurement, and the board learns to defend itself

The Architect seat came up in the Forge (the bespoke agent IDE — this entry is
written from it) and spent the night on evidence rather than construction. What
it found rewrote part of the afternoon's story.

**The postmortem was half right.** The afternoon's conclusion — "fork-as-shell
ran High integrity because the interactive logon token is full-admin" — is
contradicted by direct measurement: Winlogon starts explorer.exe at **Medium,
not-elevated** on this box (`FilterAdministratorToken=1`; measured with the new
read-only `tools/proc-integrity.ps1`). The High-integrity CairoDesktop whose
failures are actually on record was the **17:34 instance started by the
"EsotericOS Shell (elevated)" scheduled task** (RunLevel Highest — that is what
"start High" means), i.e. the fix attempt, not the original Winlogon-started
shell from the 17:18 sign-in. And its recorded failures — "Class not
registered" activating the Claude app and Windows Terminal via
`shell:appsFolder` — are AppX activation refusing an **elevated** caller. The
elevation fix reproduced a launch failure; it did not fix one. What broke the
original 17:18 Medium sign-in was never recorded: Cairo keeps one same-day log
backup and the 17:34 session overwrote it. Full corrected timeline, sources,
and the open question: `docs/SHELL-TAKEOVER-POSTMORTEM.md` (addendum).

**Two safety defects fixed.**
* `assert-control.ps1` set HKCU **and HKLM** Winlogon Shell to the fork —
  destroying the machine-wide Explorer net exactly when it would be needed. It
  now asserts HKCU only and forces HKLM back to `explorer.exe`, with the
  incident written into the script as the reason.
* There was no automated recovery from a failed shell sign-in. Now there is:
  `shell\tools\shell-watchdog.ps1` — logon scheduled task (Task Scheduler is
  shell-independent; an HKCU Run value would be launched by the fork's own
  StartupRunner and thus dead precisely when needed). 45s delay, 60s poll
  window; if neither CairoDesktop nor explorer appears it starts explorer.exe,
  logs, and raises a dialog. It never kills a process and never touches the
  registry — a recovery that undoes the evidence makes every diagnosis after
  it wrong. Installed and live-fired: LastTaskResult 0, "shell alive" logged.

**Leftover flagged, not touched:** the "EsotericOS Shell (elevated)" task is
still registered while the arrangement it served is inactive (HKCU Shell
absent). Doug rules on keep-for-next-experiment vs unregister.

**The board grew tooling.** `docs/plan/plan.json` is edited by script now, not
by hand: `tools/board_summary.py` (live state), `board_cost.py` (the live work
prices at 8.1M–15.8M est. tokens across 43 items), `board_set.py` and
`board_fix_drift.py` (dry-run by default, timestamped backup, atomic
temp+rename, round-trip guard; the embedded history snapshots are never
touched). Drift corrected: v2.M3 → done, v2.M2 → doing, v1.38's stale dep;
v3.107 (recovery kit) → done with the watchdog; v3.110 (the takeover) → 
**blocked** on the corrected root-cause picture. Next armed sign-in is a
measured experiment: `proc-integrity.ps1` within a minute of desktop, and
Cairo's log copied before rotation eats it.

**Seat charters landed in the repo** (`forge-architect/`, `forge-builder/`,
`forge-keeper/` CLAUDE.md) — the Forge injects them per turn; the Architect's
now carries the Next Action Item rule as law, because a rule said in
conversation does not survive.

**And it held.** Third attempt, 23:15 sign-in: the fork Winlogon-started at
Medium from `stable\`, no Explorer in the session, app up elevated through
silent UAC, VM running, watchdog quiet, Forge 5/5 (taskbar, tray, dialogs,
Win+Shift+S, Claude app). Full verdict in the postmortem's RESOLUTION.

**A second doctrine, same night — one surface.** Doug, on the app: "there is
never a point when i want to be operating this computer without that thing,
unless i am in windows regular for whatever reason -- that'd be debugging
special case... remove the minimize, X buttons. that IS EsotericOS -- it and
the shell, all of it should breathe as one surface." So the app stops being a
window: under the EsotericOS Shell it is surface — no minimize, no X, no
casual close, exits only by deliberate debug path or session end; under
Explorer (a debugging visit) it remains a mortal window. Board v3.138.

**The doctrine, set the same hour it held — Cairo is scaffold, not
foundation.** Doug, with the swap proven under him: "move away from Cairo —
it asks to update, it seems clunky/outdated, let's use what we want from it
and move on." And it *did* ask to update tonight, straight past the
`CheckForUpdates=0` key set at 23:06 — the field test that settles how
removal works here: registry silencing is a reminder; only excision refuses.
The divergence path on the board: v3.135 De-Cairo I (update machinery and
Cairo identity out of the build at source) → v3.109 (our skin) → v3.111
(taskbar → Dock, our desktop) → v3.112 (our modules in the shell). What we
keep is the fork's plumbing — ManagedShell interop, AppBar/WorkArea handling,
the shell-hosting bones. What we replace is everything with Cairo's name or
Cairo's face on it. The licence lineage already permits exactly this; that is
why the fork is GPL-3.0-or-later.

---

## 2026-08-16 evening — the boundary drawn at boot, and copyleft made law

Doug re-centred the whole endeavour. Verbatim direction, across several messages:
"i want EsotericOS to take over from the beginning"; "bring up the entire thing
on the desktop too, the displays, bluetooth, all of this is mission critical and
must load in as fast as possible"; "windows boots into [EsotericOS Stable] and
immediately we control bluetooth, suppress explorer, take over everything right
from the beginning ... in a way that is inviolable from windows -- we are drawing
our boundaries further and further, make sure they stick"; "Cairo is equal to
windows, a foreign architect that we are using to our own ends only"; and the
charter — "FRAMEWORK FIRST. Modularity, Extensibility, Anti-Capture Universalism."
And a law: "Maintain strict copyleft, i don't want any corporate pigs feasting on
this" / "STRICT COPYLEFT == SCRIPTURE STITCHED INTO SOUL."

**What was set (applies at his next sign-in; he restarts cold from the Forge on
his own schedule):**

* **The fork becomes the shell.** Per-user `HKCU\...\Winlogon\Shell` = the fork's
  `CairoDesktop.exe`. Windows starts it AS the shell and never starts Explorer for
  this user. Chosen per-user, not machine, on purpose: HKCU wins over HKLM AND
  Windows Update / SFC do not touch it, so it is the value that *sticks*; HKLM
  stays `explorer.exe` as the safety net. Verified the fork's `StartupRunner`
  reads the HKCU Run key, so **EsotericOS.exe still autostarts** under the new
  shell — displays, the Bluetooth stand-down, and the bridge VM all come up as
  they do today (boot.log confirmed the app did exactly that at the 16:47 boot).
* **Cairo's self-assertion stripped.** `ENABLEFIRSTRUN` removed from the Release
  build so the upstream "Welcome" tour service is never registered; WinSparkle's
  update check turned off (it offers to install *stock* Cairo over the fork). A
  foreign architecture does not get to reintroduce itself at our boot.
* **Recovery is intact:** Task Manager → Run `explorer.exe`; `restore-explorer-
  shell.ps1`; `autostart-shell.ps1 -Undo`. The one untested surface is the Forge
  (Electron) under Cairo-as-shell — the old v3.114 audit, now a post-hoc check at
  his next sign-in rather than a gate, because he ordered the swap ahead of it.

**Copyleft made law.** The app carried an MIT LICENSE ("OpenSpan contributors").
Relicensed to **AGPL-3.0-or-later** (verbatim text; NOTICE with copyright Douglas
Knoll / Divisionless and the reasoning). AGPL because EsotericOS runs over the
network — LAN nodes, clipboard relay, input portal — and section 13 closes the
SaaS door a plain GPL leaves open. First-party code only; all copyright the
author, his to relicense. The shell fork was already GPL-3.0-or-later over its
Apache-2.0 lineage. A dependency-licence audit (nothing GPL-2.0-only combined in;
every vendored component's terms recorded) is board row v3.134.

---

The honest build log: what it took, what broke, what I threw away. OpenSpan
was built in about a week (early–mid July 2026) in close collaboration with
Claude (Anthropic) as my engineering partner — I set direction and tested
everything on real hardware; Claude wrote code, diagnosed from logs and
source, and ran adversarial multi-agent reviews. The Bluetooth radio was
always mine to test — no AI can see whether the earbuds are blinking.

This log keeps the dead-ends in on purpose. The wrong turns are the story.

---

## The one hard constraint

One PC. **One Intel Bluetooth radio**, passed through into a headless Debian
VM. That radio does two jobs at once:

- **A2DP audio** → Bluetooth earbuds
- **BLE HID keyboard + mouse** → the iPad

Every hard problem in the project traces back to that single antenna
time-sharing two roles. "One antenna, two jobs" is the whole saga.

---

## Rough timeline

### Jul 25 — validated end-to-end; frameless restored (safely)
- **Validated over several days (Jul 23–25) of real-hardware use — every
  tested path passed.** iPad input, managed-Mac input, Bluetooth audio,
  controller isolation, and seamless iPad↔Mac edge handoff all worked together
  and survived restarts across the run, with no regression in the original
  single-radio iPad path.
- Restored the frameless window the crash-safe way: a one-shot Win32 style
  strip (drop `WS_CAPTION`) with **no ctypes window procedure** — so the
  `_ctypes.pyd` access-violation the old `WM_NCCALCSIZE`/`HTCAPTION` callback
  hit under heavy pointer traffic cannot recur. The header row is the title
  bar; the drag stays the callback-free `SetWindowPos` blit; native resize,
  minimize, snap, and the taskbar are all kept.

### Jul 24 — independent managed-Mac lane
- The three-radio bench passed: iPad input, Bluetooth audio, and controller
  isolation all worked after restart.
- Added a second BLE HID daemon on TCP `9956`, advertised as **OpenSpan Mac
  Control**, with its own controller assignment, bond, subscription state, and
  Pair/Connect/Disconnect/Unpair controls. The original iPad daemon and the
  single-radio compatibility path remain intact.
- The arrangement canvas now holds Windows monitors, the iPad, and individual
  managed-Mac displays. Display resolution, refresh rate, rotation, and
  physical/layout size are independent. The default Mac profile is three 4K
  displays with two rotated 90°; any display can be changed manually to 2K or
  another mode.
- Bluetooth-only limitation kept explicit: no companion software is installed
  on the Mac. OpenSpan knows the configured desk/display geometry and sends
  standard relative HID input; it cannot read the Mac's authoritative cursor
  position back over the HID link.
- Live verification found that older VM clones had no host forwarding rule for
  the second daemon. The app now self-heals TCP `9956` on a running or stopped
  VM, and newly created VMs include the rule from the start.

### ~Jul 5–6 — getting audio to survive
- Goal: route Windows audio to BT earbuds through the VM, and have it *stay*.
- **Bruise:** first cut ran the audio stack on a hand-rolled `dbus-daemon`.
  It accepted the boot-time WirePlumber connection, then refused every
  reconnect. Endless restart failures.
- **Fix:** rebuilt the standard, documented way — PipeWire/WirePlumber/BlueZ
  on a real persistent systemd *user* bus (`loginctl enable-linger root` →
  `/run/user/0/bus`). This was the crux. Do not hand-roll dbus.
- **Bruise:** audio dropped ~5s into any silence. Cheap TWS earbuds fully
  disconnect when the A2DP transport idle-suspends.
- **Fix:** two things hold it, and it took an audit of the live VM to get
  this right. The WirePlumber bluez config is edited to disable suspend
  (`suspend-timeout=0`, `pause-on-idle=false`, `with-logind=false`), *and*
  the UDP bridge feeds real-time silence during gaps so the transport never
  idles. I'd earlier written that the config didn't exist and the feed was
  the whole story — wrong on both counts. Verify against ground truth, don't
  trust your own notes.
- Contention gremlin: Debian's stock per-login PipeWire services spawned a
  competing WirePlumber on every SSH login that grabbed then dropped the
  endpoints — "it dropped the instant I typed." Masked them.

### ~Jul 6 — keyboard + audio on the same radio
- Proved BLE HID (iPad) and A2DP (earbuds) can run together on the one radio.
- The breakage was never the protocol — it was **operational coupling**.
  Keep them segregated: restarting audio must never touch the keyboard, and
  vice versa.

### ~Jul 9 — the mouse-lag rabbit hole
- Mouse felt laggy. Root cause was self-inflicted: BlueZ/kernel default LE
  interval is 30–50ms, and something was actively *slowing the iPad down* to
  it.
- **Bruise:** cranked the interval to 7.5ms. Mouse got great — and the audio
  garbled constantly. 7.5ms services the iPad ~133×/sec and starves A2DP of
  airtime. One antenna, two jobs.
- **Bruise:** added per-interval mouse coalescing to compensate. Reverted it
  — added risk, wasn't the cause.
- **Fix:** settled on 15–30ms (`Min=12`/`Max=24`). Snappy mouse, airtime left
  for clean audio. The balance point.

### ~Jul 10 — the connect script I kept breaking
- **Bruise (repeated):** rewrote `bt-connect.sh` three times (v2/v3/v4) with
  "honest state reporting," stop-scan-before-pair, retry loops. Every version
  *broke the connect* and produced false "the earbuds didn't respond"
  reports. The buds were fine.
- **Lesson:** BlueZ purges an unpaired discovered device ~1s after the scan
  stops. Pair *during* the live scan. Reverted to the basic version and left
  it alone. (Hard rule that came out of this: stop guessing, diagnose from
  data — every wrong guess cost a real hardware test cycle.)
- Launch gotcha: Windows blocked the unsigned Python interpreter ("an
  administrator has blocked this app"). Root cause wasn't what I first
  guessed (OneDrive/MOTW) — it was a `RUNASADMIN` AppCompat flag on
  `pythonw.exe` forcing elevation. Fix: a same-dir unflagged copy,
  `openspanw.exe`. First local git snapshot taken here.

### Jul 10–12 — the sprint (this is where most commits live)
- **Latency:** the audio was decent right after a restart and got worse over
  a session. Two culprits: a hidden 64KB stdin pipe to `pw-play` (~341ms of
  buffered audio nobody accounted for) and an open-loop silence injector that
  *ratcheted* the queue upward on every late packet and never reclaimed it.
  Capped the pipe, debounced the injector.
- **Two-senders garble:** audio garbled after a restart. Not the volume code
  (measured, innocent) — a race in the sender watchdog spawned *two* senders
  onto the one UDP port. Named mutex + launch lock.
- **Broadcast killed the music:** hitting Pair/Broadcast instantly dropped
  A2DP. Two real bugs: (1) a systemd drop-in had silently *reset* the
  keyboard unit's `ExecStartPre` list back to an unconditional radio
  power-cycle — `systemctl cat`, not just reading the script, is how you see
  the truth; (2) the bond-cleanup was **fail-open** — an empty `bluetoothctl
  info` (busy bluetoothd) matched neither guard, so it removed the *playing*
  earbuds' bond mid-song. Made it fail-closed.
- **Auto-reconnect:** the buds page the adapter during the ~90s VM boot, give
  up, and never retry — so I was the retry. Taught the app to reconnect them
  itself, structurally unable to scan or pair (an adversarial review proved
  the first version *could* reach the scan branch — fixed).
- **Two-way clipboard:** plain Ctrl+C/Ctrl+V now sync both machines (Apple
  Shortcuts + a token-guarded LAN relay + FKA key-combo triggers the PC sends
  itself). Found a day-one latent bug on the way: the ctypes clipboard code
  never declared its Win32 handle types, so 64-bit handles were being
  truncated — it had been working by allocation-address luck the whole time.
- **Compact mode + exe:** collapsible panel with volume + L/R balance;
  packaged the whole thing into one `OpenSpan.exe`.

### Jul 12 — publication prep
- Assessed readiness with a fresh-eyes review. Verdict: **not ready** — for
  two independent reasons.
- **The catch that mattered:** every commit was authored under my real name
  and the tree carried my home network's fingerprint (device MACs, LAN IP,
  monitor layout). A `git push` would have doxxed me permanently. Scrubbed
  the personal data, squashed history to one clean commit. (Casualty: the
  granular commit timeline — hence this hand-written log.)
- Pruned `guest/` from 158 files to 39 (it was a dumped working directory —
  ~120 throwaway experiment scripts). Rewrote the README, which had gone
  stale enough to tell people the *wrong* USB mode. Added SSH-key
  self-provisioning and a create-VM script.

### Jul 13 — after v1.0: the volume-curve bug
- Swapped to a second (identical) pair of earbuds mid-session; they came in
  **ear-splitting**. First reflex was a guess — "these buds are just louder
  hardware." Wrong — and saying it out loud, then flagging it for a human
  check, is what saved it.
- A five-agent adversarial review of the whole volume chain (four read-only
  investigators + an adjudicator) refuted the guess and found **two separate
  things**:
  - *The hot arrival:* a Bluetooth sink WirePlumber had no saved volume for
    came up at unity 100%. These sinks are A2DP absolute-volume
    (`HW_VOLUME_CTRL`), so 100% *is* the earbuds' hardware max — a never-seen
    pair starts at full. (I first mis-read it as one pair changing its MAC on
    reset; the device list showed two physically distinct pairs. Human ground
    truth beat the log inference.)
  - *The real bug:* the volume slider had no usable range — everything above
    ~25% was "too loud." The sender read the Windows master's *slider position*
    (`GetMasterVolumeLevelScalar`, a tapered 0–1) and used it as a raw
    amplitude. At 5% that applied −26.7 dB where the slider truly meant
    −46.2 dB (~20 dB too hot), and linear faders pile all their range into the
    bottom.
- **Fix:** read the actual level in dB (`GetMasterVolumeLevel`) and apply
  `10^(dB/20)`, so the earbuds track the same perceptual curve as the Windows
  slider. Tagged v1.0.1.

### Jul 14 — the copy that killed the mouse (and wasn't our bug)

The worst bug in the project, and the most instructive.

- **Symptom:** copy anything in Windows → the mouse silently stops crossing to
  the iPad. The iPad still shows connected. Restarting the portal fixes it,
  every single time.
- **I shipped three fixes. All three were wrong.** (1) `leave()` did a blocking
  socket send inside the hook proc — real hygiene problem, not this bug.
  (2) Re-install the hook when it goes deaf. (3) Run the hooks on a replaceable
  thread. Each one was aimed at the hook code, and each one failed.
- **So I stopped guessing and instrumented it.** A heartbeat, a callback
  counter, exception capture, per-call timing. The data was brutal and
  unambiguous: the hook procs **never blocked**, **never threw**,
  `SetWindowsHookEx` kept returning **valid handles** — and delivered **zero
  callbacks** while the cursor was provably moving. Six re-installs in a row:
  nothing. Brand-new threads: nothing. Only a **new process** ever restored it.
- **The answer was Windows, not us. UIPI:** a non-elevated process receives
  **no low-level input hooks while an elevated window has focus.** I was
  copying inside an admin terminal. The instant it had focus, the portal went
  deaf. "Restarting the portal fixed it" only because restarting **steals focus
  back**.
- **Fix:** run OpenSpan elevated. It now detects this, warns at launch, shows
  `⚠ NOT ADMIN` in the status bar, and the README states it as a requirement.
  **Every line of watchdog scaffolding written to chase it was deleted.**
- **Lesson:** it looked *exactly* like a bug in our code, so I kept fixing our
  code. The instrumentation is what ended it — not cleverness. When three fixes
  in a row miss, stop fixing and start measuring.

### Jul 14 — broadcasting is now opt-in (consent, not default)

Doug caught this: the iPad was reconnecting on its own, and the app never said
it was broadcasting. It wasn't a bug so much as a bad default — the daemon
registered the BLE advertisement **at boot and never unregistered it**, so the
machine advertised as a Bluetooth keyboard 24/7 and any bonded iPad would
silently reconnect.

Now: the daemon comes up **silent**. Only **Pair/Broadcast** turns advertising
on, it turns itself **off** the moment the iPad is in (and on a failed or
abandoned pair), and the status bar reports the **daemon's real state** —
`📡 BROADCASTING` or `📡 not broadcasting` — never a UI guess. A bonded iPad
now *cannot* reconnect without you asking. Headphone auto-reconnect is
untouched.

---

## Operating principles that shaped it

- **I test the radio; the AI doesn't.** No AI can see whether the buds are
  blinking, so it would draw confident wrong conclusions. Bluetooth
  verification was always mine. Every fix got baked into the app; I tested
  when I could.
- **No guessing.** Diagnose from logs, config, source, process lists — or say
  "I don't know yet, here's the one thing I'd measure." Every wrong guess is
  a real hardware test cycle.
- **Keep the dead-ends visible.** N bugs that look separate are often one
  wrong assumption wearing different hats. Laying failures side by side is
  what reveals the pattern.
- **Adversarial review.** Big changes went through multi-agent reviews (one
  round ran 22 agents) that tried to *refute* each finding before I trusted
  it. They caught real ones: a native tray crash, a retry loop that could
  fire five Bluetooth scans per click, a COM call that could freeze the UI.

---

## Fast-pair: the radio-contention lesson

The iPad was slow to *see* the keyboard when pairing. The tempting story is
"boost the advertising power." The real one: there is no separate low-power
advertising mode — the advertisement is registered once at boot and never
touched. What starves discovery is that the single radio time-shares BLE
advertising with A2DP audio, and our own silence-feed (the fix that stops the
earbuds dropping mid-song) keeps A2DP *transmitting even when nothing is
playing*. So muting the PC does nothing; the audio link has to actually drop.

Fix: pressing Pair now asks first ("briefly disconnect Bluetooth audio to pair
fast?"), then drops the earbud link to give the broadcast the whole radio,
auto-starts the input portal the moment the iPad bonds (no second click),
settles the button to a check, and reconnects the earbuds on its own. Freeing
the audio *is* full-speed advertising.

The pre-hardware adversarial review (8 agents — five failure lenses, each
finding put to a refuter) caught two bugs before they cost a radio session:
the audio-restore on the timeout/failure paths could silently no-op if a prior
session had hit the 3-fail reconnect pause (the fail counter wasn't reset like
the two sibling paths), and a stale `on` snapshot mislabeled the just-started
portal button "Start portal" for one tick — a click in that window would have
stopped the portal it had just started. Both fixed; a headless harness now
pins the whole flow (28 checks) so it can't silently regress.

---

## The tray crash: a bug only the packed exe could have

Sending the app to the system tray hard-crashed the packed exe — twice, back to
back — leaving the window gone but the audio and portal child processes still
running (orphaned). Nothing reproduced it from source. The Windows error log had
the answer, exact and unguessable: faulting module `_ctypes.pyd`, exception
`0xc000041d` ("an exception occurred during a user callback").

The chain: the tray uses a ctypes `WNDPROC` callback. If that callback raises,
ctypes tries to print the traceback to `sys.stderr` — but a `--noconsole`
PyInstaller build sets `sys.stderr = None`, and writing to `None` from inside a
Win32 callback faults the whole process. Source never hit it because source has
a real stderr.

Two fixes plus a guard against ever shipping it blind again: the frozen launcher
now points `stdout`/`stderr` at a real log file when they're `None`, so a
callback exception is logged instead of fatal; the `WNDPROC` is wrapped so no
exception can escape it at all; and a hidden `--traytest` role exercises the
exact tray path inside the packed binary and prints OK/FAIL, so the fix was
verified in the *frozen exe* — not just from source — before it went back.

Lesson banked: a windowed frozen build is a different runtime. Any C callback
needs a writable stderr and a body that cannot raise, and "passes from source"
is not "passes as an exe."

---

## The iPad as a normal Bluetooth device

The pairing UI had accreted its own vocabulary — "Pair / Broadcast", a separate
"portal" button, a "re-pair to reset the bond" escape hatch — each one a piece
of BLE plumbing promoted to a button. It worked, but it made you think about
advertising and GATT subscriptions instead of a keyboard.

The reframe: the PC is a Bluetooth *peripheral*, so model it like any other
Bluetooth device — **Pair, Connect, Disconnect, Unpair** — and hide the rest.
It maps cleanly onto the radio, because for a peripheral *advertising is the
connect/disconnect lever*. Connect = advertise so the bonded iPad reconnects;
Disconnect = drop the link and stop advertising, so nothing can re-attach and
the iPad's on-screen keyboard comes back and stays; Unpair = forget the bond.
The old broadcast/bounce/portal machinery is still there — it just lives behind
those four verbs now. Bond state is read live from BlueZ by a self-healing
periodic read, so the buttons reflect reality and even notice a "Forget This
Device" done on the iPad itself.

Getting the concurrency right took four adversarial-review passes: a re-entry
guard so no path can start two pair workers at once; a lock so pressing
Disconnect to *cancel* an in-flight pair can't interleave with the worker
committing the broadcast; and — the lesson banked — a "clever" self-latching
bond-state cache that a review caught stranding a real bond as "not paired"
when a startup read timed out. The fix was to stop being clever: a plain
periodic read that self-heals beats a latch that can get stuck.

## A window that doesn't tear

The frameless header drag shredded — dragging left black gaps and torn strips
until you let go. The cause wasn't lag. Driving the move through Tk's
`geometry()` invalidated the whole client area on every mouse-move, and the
flood of motion events starved `WM_PAINT`, so the heavy canvas never repainted
mid-drag. The fix is to move the window with a raw `SetWindowPos` size-free blit
instead: Windows relocates the existing pixels with no invalidation, so there is
nothing to repaint and nothing to starve. Smooth — and still safe from the
reentrancy rule, because it is a synchronous Win32 call from a normal callback,
with no modal move loop.

---

## Multi-radio without sacrificing the one-radio build

Multi-device support is an opt-in layer over the shipped single-radio path.
`radio_mode = single` remains the default and still calls the original
`bluetoothctl` scripts. In Multiple radios mode, BlueZ controllers are
enumerated with their stable MAC addresses and USB hardware names; the iPad
keyboard, scan target, and each discovered device can be assigned separately.
The UI stores assignments by controller MAC rather than `hciN`, because Linux
is free to renumber adapters after any reboot.

Every multi-radio action is controller-scoped through `openspan_bt.py`.
The HID daemon can move to the selected adapter via a systemd drop-in, while
the audio pin records `controller|device` (and still reads the old MAC-only
format). Pairing the iPad only disconnects audio when both are deliberately
assigned to the same controller. Audio on another controller is left live.

The first bench machine now has all three physical radios live together: the
internal Intel controller plus two TP-Link USB adapters. The two RTL8761BU
adapters require Debian's `firmware-realtek`. VirtualBox could mark both dongles captured at VM startup
without actually attaching them; re-enumerating their shared external USB hub
after the VM filters are listening makes both proxy devices appear. The app
now performs that narrow recovery only in multi-radio mode and only for a
shared external hub containing two filtered radios. Root hubs and the default
single-radio path are never cycled.

The saved HID choice remains a controller MAC. Every app startup re-resolves
that MAC to the current `hciN`, updates the daemon drop-in, and applies the
15-30 ms mouse interval to the resolved controller. This prevents Linux radio
renumbering from silently moving the iPad lane after a reboot. The internal
Intel bonds were preserved as the backup lane throughout the bench work.

The packed app also needed `PYINSTALLER_RESET_ENVIRONMENT=1` for its independent
audio, portal, and elevated-replacement roles. Without it, a child could share
the main one-file `_MEI` directory and display "Failed to remove temporary
directory" when it exited. The roles now unpack independently.

Hardware inventory, passthrough, controller isolation, HID-daemon assignment,
and service bring-up are confirmed. The remaining live hardware test is the
first fresh iPad bond on TP-Link 1 followed by simultaneous headphone pairing
on TP-Link 2; the preserved Intel lane remains available for the original
single-radio behavior.

## Display-editor crash and duplicate Apple bond hardening

Repeated display arrangement crashes were native access violations in
`_ctypes.pyd`, not damaged monitor geometry. The main window had replaced Tk's
native Windows procedure with a Python `ctypes` callback to implement a
frameless title bar. High-volume move/resize messages could reach that callback
after its safe lifetime and take down the main process without a Python
traceback. The app now keeps Tk's native window procedure and applies only the
safe DWM dark-titlebar painting. A live soak then exposed the same failure in
the remaining pure-ctypes tray WNDPROC, despite its short packaged self-test
passing. Runtime tray creation is therefore disabled: Minimize now uses the
native taskbar and keeps the bridge warm without another Python Windows
callback. This also eliminates duplicate OpenSpan notification icons. Frozen
audio and portal roles are stopped as full process trees, preventing one-file
child processes from surviving a UI crash or restart.

The main UI's compact volume reader was another `_ctypes` risk: it repeatedly
discarded and rebuilt Core Audio `comtypes` pointers. Core Audio now lives only
in the isolated audio role. The app's volume slider writes a small persisted
gain file that the sender applies alongside the normal Windows master volume,
so the control remains functional without COM pointers in the UI process.

The first Mac-lane test also exposed one iPad central bonded to both the iPad
and Mac controllers. Mac pairing now rejects and removes iPad/iPhone/iPod bonds
from the dedicated Mac controller, and Mac paired status ignores both mobile
devices and audio devices. The two HID lanes now publish different PnP product
IDs plus lane-specific model and serial characteristics, so Apple hosts can
cache them as distinct accessories. The original single-radio path and Intel
iPad bond remain unchanged.

## Independent device lanes and multi-edge travel

The first successful simultaneous iPad/Mac session revealed one remaining
legacy coupling: iPad Disconnect/Unpair stopped the single Windows input-router
process, which made the still-connected Mac look unpaired. Bluetooth had stayed
controller-scoped, but control disappeared. The router now remains alive and
maintains independent daemon/socket readiness per target. iPad and Mac unpair
commands are also identity-filtered as well as controller-scoped, so each verb
can remove only its intended class of host.

The desk layout now produces a directed adjacency graph across every shared
display edge, not just a list of target-to-PC entrances. One hook broker routes
both independent target channels (two competing low-level-hook processes would
steal or duplicate input). While captured, it tracks the cursor through the
configured physical/resolution geometry, follows Mac display-to-display edges,
hands directly between iPad and Mac when their rectangles touch, and returns to
the correct Windows monitor through any shared PC edge.

Dragging also solves horizontal and vertical snaps together. A device can now
stick to two neighbors in one release—for example, the iPad's right edge against
the PC and its top edge against Mac Display 2. All active PC↔target and
target↔target segments are drawn with the same yellow portal line.

---

## Status

Working & tested: BLE keyboard + mouse, edge crossing, keymap remaps,
Bluetooth audio (volume + balance), the iPad managed as a normal Bluetooth
device (Pair / Connect / Disconnect / Unpair), themed in-frame dialogs, a lean
one-window UI with a collapsible console + system tray, a tear-free frameless
window, headphone auto-reconnect, single-file exe. Clean repo, honest docs.

**Still in progress — the two-way clipboard.** The plumbing works (FKA chords →
Apple Shortcuts → a token-guarded LAN relay), but it is *not* finished: the
iPad Shortcut's token can drift out of sync with the relay's and paste then
returns a "bad token" error instead of your text, and the setup is not yet
documented well enough for anyone else to reproduce reliably. Treat clipboard
sync as experimental until that's sorted.

Reproducible VM: `create-vm.ps1` + `guest/provision.sh` turn a fresh Debian
into the working bridge — validated on a fresh clone (software) and confirmed
end-to-end on the radio + iPad. Tagged v1.0; v1.0.1 fixes the volume-slider curve.

Built by Douglas Perianu Knoll, with Claude.

## 2026-07-27 — Crossing onto a device lands where you crossed

**Reported:** "when i move over to the mac, it doesn't start at the edge, and
then it returns from that same false point to the PC, so i can't mouse over the
right section at all unless i drag the mouse all the way left and flick it."

**Cause.** Four mechanisms added over the previous two sessions, all of which
worked around the fact that a relative HID link cannot move a cursor *to* a
position — and all of which paid for it by discarding motion the wire had
already delivered:

| Mechanism | What it actually did |
|---|---|
| position resume | `enter()` restored the position saved on the last **exit**, not the edge just crossed. That point is a live exit trigger, so entry landed on a bounce. Entering mac-2 from the right restored a point saved on its bottom edge — the "false point". |
| `EXIT_PRESSURE` | 45 target points of lean per crossing, sent to the device, thrown away by the model. |
| `SWITCH_COOLDOWN` | up to 300 ms of motion, likewise sent and thrown away. |
| proportional arrival | stretched the overlap across the destination's whole edge — 3.4× on the iPad→Mac seam, not reversible, and it dropped 43% of Mac-to-Mac crossings into a band of the next screen with no link back. |

**The rule that replaces all four.** *The model may only change position by an
amount the wire also moved. When the model must be somewhere the wire has not
taken it, send the difference.* `_place()` asserts the crossed edge; `_warp()`
queues the HID reports that make the assertion true, converting desk units per
**display** by walking the path through the screens it actually crosses.

Also fixed: `_matching_link` had no fallback, so an edge range with no link was
a silent unbounded wall — mac-2's right edge, the boundary with the PC, was 47%
dead because the monitor beside it is shorter than the 32" panel. `_last_seen`
is per **device** now (a device has one pointer). `ARRIVE_MARGIN` is one
constant governing both halves of every edge. The compensated path credited the
model along the pre-rounding direction, and the sender re-summed per-report
deltas — both break the per-report premise the Apple inverse rests on.

**New:** `win/test_portal_invariants.py` — eight properties driven by the live
layout. Each fails on the previous build.

**Known limits, hardware only:**
- The warp assumes the desk arrangement matches macOS's own Displays
  arrangement. If they differ, cross-screen entries are wrong by that
  difference.
- `_APPLE_CURVE` is Apple's table at the **default** tracking-speed slider.
- `_last_seen` is true unless the Mac's own trackpad moves the pointer while we
  are away; it re-converges at any edge.

## 2026-07-27 — Pin on exit: stop remembering the pointer, start knowing it

Doug: *"when you move the mouse over to another screen, whatever screen it was
on, can you, as the last action, shove the mouse as far as it goes in that same
direction. This will always reset the relative position per window boundary."*

His idea, and it is the right primitive. A relative HID link cannot ask a device
where its pointer is — but the device's own window server **clamps** that
pointer at the edge of its display union. Driving it hard in one direction
therefore turns that coordinate from an accumulated belief into a measured fact,
using nothing but the target's own clamp. Done as the last act of leaving, in
the direction you left by, it is never watched: your attention is already on the
screen you moved to.

**What it replaced.** The previous build asserted the crossed edge and paid for
the jump with a warp computed from `_last_seen` — correct, but only once
`_last_seen` held something. It starts empty at every portal launch, so the
FIRST crossing to a device had nothing to correct from: the model asserted the
edge while the device's pointer stayed where it was. Since the portal now
restarts on every layout edit, almost every crossing during a layout session was
a first crossing. That is what Doug saw as "it is assuming the original mouse
position."

`_pin(target, side, along)` warps exactly to the outer boundary — computed over
the screens that actually span `along`, so an L-shaped arrangement gives the
boundary the pointer will really meet rather than a bounding box — then drives
`PIN_OVERSHOOT` past it so rounding cannot leave it a pixel short of the clamp.
It records the boundary itself, and reports whether it fired so the ordinary
record cannot overwrite a measured value with a believed one.

Bails (Esc ×3, Ctrl+Alt+Q/I, a dropped lane) have no direction and skip the pin
— nothing moved the pointer, so the previous record still holds.

**Cost on this desk**, in HID reports at a 15 ms connection interval:

| exit | reports | time |
|---|---|---|
| iPad, any side | 2 | ~30 ms |
| mac-3 → PC (bottom) | 9 | ~135 ms |
| mac-3 → PC (right) | 17 | ~255 ms |
| worst case (mac-1 right, full width) | 40 | ~600 ms |

**New invariants:** the shove always reaches the device's own clamp, and the
record is the boundary itself — measured, not remembered.

**Still true only if** the desk arrangement matches the target's own display
arrangement: the pin lands where that OS clamps, not where we think it should.

## 2026-07-27 — The union is an L, so one shove establishes nothing

Doug: *"i was on the ipad then i traverse up and it goes into the bottom of the
middle managed mac display, should not be possible... It doesn't make sense we
are missing something."*

He was right that something was missing, and it was not the routing. Replaying
that crossing headlessly from four prior states routes to Mac Display 3 every
time, and the warp lands the pointer there. The fault is one step earlier.

**The Mac's screens do not form a rectangle.** Two 32" portraits beside a
shorter, vertically-offset 32" landscape make the union an **L**. On an L, the
boundary you reach by shoving one way DEPENDS on the other axis:

```
shove left    -> boundary [-5986]           SAME everywhere -- safe to shove blind
shove right   -> boundary [-2848, -59]      DEPENDS on the other axis
shove top     -> boundary [-2179, -1569]    DEPENDS on the other axis
shove bottom  -> boundary [0, 610]          DEPENDS on the other axis
```

So the exit pin — one shove, in the direction of travel — did not establish a
position. It established *one of two*, and which one depended on the axis we
merely believed. Leaving the Mac rightward recorded x = −59 (the landscape's
right edge); if the believed Y was wrong at all, macOS had actually clamped the
pointer at −2848, the portraits' right edge. 2789 desk units of error. The next
warp in from the iPad spent that error going left and put the pointer on the
middle portrait — precisely what was reported.

And the believed Y *was* wrong, because the first crossing of a portal session
had nothing to pin from.

**`_resync()`**: shove first along whichever axis the geometry says has ONE
boundary everywhere — a fact regardless of what was believed — then, with that
axis known, shove perpendicular, where the boundary is now determinate. Both
directions are derived from the rectangles; a device whose screens *do* form a
rectangle finds the first axis immediately. Runs once per device per portal
session, the moment nothing is known, before any warp.

New invariant, and the decisive one: **a cold re-sync lands in the same place no
matter where it started** — simulated from every corner and centre of every
screen, with the device's own clamping modelled. On this desk it converges to
(−5986, −2179), the top-left of Mac Display 1, from all sixteen starting points.

Cost: iPad 2 reports (~30 ms); Mac 69 reports (~1.0 s), once per session.

## 2026-07-27 — Entirely position-agnostic: nothing is believed across a boundary

Doug: *"make it entirely agnostic - users must be able to arbitrarily
rearrange."*

The previous build still trusted a belief in three places, and said so when
asked. All three are gone.

1. **The exit pin shoved *from* where it thought it was**, then overshot by
   400 px or 25% of the screen. It only reached the clamp if the belief was
   close enough.
2. **It chose *which* boundary to record using the believed other axis** — and
   on any non-rectangular arrangement that boundary is not unique.
3. **A re-sync ran once per device per session.** A dropped lane now clears the
   record, so the next entry re-establishes it.

`_pin` is deleted. **Every** exit — to the PC or to another device — runs the
full re-sync. Nothing is carried across a boundary as a belief.

**Making it work for any arrangement.** A shove maps every position the pointer
could be in onto one boundary; do it enough times and the set collapses to a
single point. How many times, and in which directions, depends entirely on the
shape:

| arrangement | plan |
|---|---|
| one screen, a row, an L, a T, a Z, a staircase | one diagonal, or two straight |
| a plus | needs a diagonal — axis-aligned shoves map its symmetry onto itself forever |
| two islands that never touch | **impossible**, and it says so rather than guessing |

So the sequence is **searched** from the rectangles, never assumed. Diagonals
are included because each axis clamps independently, so a diagonal slides along
a boundary instead of stopping on it — that is what breaks a symmetry. The
search takes the **cheapest** plan, not the shortest: one diagonal across this
desk costs 94 reports where two straight shoves cost 69, because a diagonal must
be long enough to travel *around* things. Planning runs at portal startup, never
in the mouse hook (Windows unhooks a hook that overruns, without a word).

Two bugs the new `test_resync_shapes.py` caught, both of which would have shipped:

- The planner walked a 45° diagonal while `_shove` sized each axis to its own
  extent — a shallower line that clamps somewhere else entirely.
- A diagonal shove sized to the straight-line extent falls short: while one axis
  is clamped, the motion commanded on it is **discarded**, so crossing a wide
  screen sideways burns the vertical budget without moving vertically. Width
  plus height bounds it.

**Cost on this desk:** iPad 2 reports (~30 ms), Mac 69 (~1.0 s), on every exit.

**Remaining honest limits:** the desk arrangement must match the target's own
display arrangement — a re-sync lands where *that* OS clamps, and we record the
desk coordinate we believe that is. And `_APPLE_CURVE` assumes the default
macOS tracking-speed slider.

## 2026-07-28 — Corners are places you use, and a re-sync puts the pointer back

Doug: *"its really jumpy... This time it seemed like the ipad jumped to the far
left screen from below but it was in the corner maybe. Honestly I think corner
movement is problematic. Don't allow movement across corners at all -- either
way sometimes i need to interact with corners."*

**The far-left jump was mine.** Re-syncing on every exit left the pointer parked
at the plan's landing corner — the top-left of Mac Display 1. Crossing back then
began there and flew across two screens to the arrival point. Correct, and
horrible to watch.

`_resync` now takes a `restore` point: establish the corner as a fact, then walk
the pointer back to where the user actually left it. The corner is measured and
the walk back is a known distance, so the result is still a measurement — and
the pointer is where they expect to find it, both when crossing back and when
using the device directly. All of it still happens on the way *out*.

**No crossing fires within `CORNER_ZONE` (one inch) of either end of any edge**,
on either surface, in either direction — including the Windows side, where the
corners are Start, Show Desktop, and every window's close box. Two reasons, and
the second is the one that bit:

- corners hold things people reach for, and a crossing there takes the pointer
  away mid-reach;
- a diagonal into a corner satisfies **two** edges at once, so which surface you
  land on comes down to whichever overshoot happened to be larger on that
  report. That is not a rule anyone can predict, which is exactly why it felt
  arbitrary.

Arrivals are clamped into the same band, so the way back is open the instant you
land. Crossing at the very end of an overlap therefore lands a corner-zone
inside the neighbour — the rule working, not drift.

New invariant: **driving hard into a corner never crosses anywhere** — every
corner of every screen, three approach distances, thirty reports of sustained
pressure.

**On the jumpiness.** The iPad's `pointer_accel` is 1.0, which reaches 3.4x
effective gain on a fast flick — and iPadOS applies its own acceleration on top.
A 120 px mouse move becomes 412 iPad px before iPadOS sees it, 38% of that
screen in a single report. Lowering *sensitivity* would slow careful movement
too; the acceleration is what jumps. `pointer_accel` 0 on the iPad is the
targeted change.

## 2026-07-28 — Re-sync when the lane comes up, not when someone crosses

Doug, after the corner work: *"crossing this boundary lands me in the top left
of the ipad screen."*

Measured on the live layout, and he was describing the re-sync landing exactly:
the iPad's plan ends at (−2333, 0), which **is** the iPad's top-left corner.

The corner fix had already made every *warm* crossing land correctly — replaying
all three PC entrances shows the pointer arriving within 10 desk units of the
model. What remained was the **cold** crossing: the first one of a session had
to run the re-sync itself, so it started at a corner of the arrangement and then
carried the pointer all the way back — 106–110 reports, about a second and a
half of it sailing across two screens while he watched.

The answer is not to make the re-sync cheaper, it is to stop doing it at the
worst possible moment. **A lane coming up is the right moment:** nobody is
looking at that device, and there is time. `_park_at_door()` re-syncs then and
leaves the pointer at the entrance it is most likely to be met at, so no
crossing ever pays for a re-sync. New invariant asserts exactly that, with a
32-report budget; the parking cost is unbounded and nobody waits for it.

**The other report was geometry, not a fault.** "Moving across puts me into the
managed mac display shown in the bottom right corner" — DISPLAY1 only touches
the bottom 833 desk units of Mac Display 3's right edge (that screen is 1569
tall), so a crossing from it lands low by construction. Verified that arrivals
track the crossing point along every boundary:

```
Managed Mac via x=4    cross at -1080 -> (-99, -833)
                       cross at  -540 -> (-99, -416)
                       cross at     0 -> (-99, -100)
```

## 2026-07-28 — The sender was cancelling every re-sync, and a boundary you could leave by but not return through

Two faults, found together after the display resolutions were corrected.

### 1. sender() summed a re-sync into nothing

A re-sync is three deliberate movements — shove hard left, shove hard up, walk
back — and its whole purpose is that the device **clamps between them**.
`sender()` summed a tick's motion into one net vector before putting it on the
wire. Those three cancel algebraically: the pointer never reached an edge, never
clamped, and the position the model then recorded as measured fact had never
been established at all.

Every re-sync on a device that was *not* running target-acceleration
compensation had therefore been a no-op since the day it was written — the iPad,
in other words. The Mac escaped only because its compensated reports already
took a separate path that kept them individually.

Fixed by never merging two deliberate movements: an exact report (a shove, a
warp) is emitted on its own; only consecutive live hook samples — one continuous
movement inside one 8 ms tick — are coalesced, and never across an exact report.
Splitting a large delta into ≤127 chunks is still fine; it is only *merging*
that destroys the movement.

Measured after: the iPad's re-sync emits **31 intact reports** and the pointer
lands **1 desk unit** from the model. The Mac, 110 reports, 6 units.

Found by three independent lenses of a 29-agent investigation, each arriving at
it separately.

### 2. A boundary you could leave by but not return through

Doug: *"crossing this boundary still acts strange."* The log said it outright:

```
resync mac: ... back to (-59,-800)          <- exited here
<<< mac mode OFF -- resynced to (-59,-800)
>>> Managed Mac mode ON ... at (-99,-352)   <- had to re-enter far below
```

The corner rule was being applied to each **screen's own edge**, but the two
sides of a boundary have different edges. Mac Display 3's right edge runs
y ∈ [−1569, 0]; the PC monitor beside it covers only [−833, 0]:

```
EXIT band  (trimmed from the SCREEN's edge): [-833, -100]
ENTRY band (trimmed from the SPAN)         : [-733, -100]
              -> [-833, -733] was exit-only
```

Now both sides trim the **shared overlap**, which is the one thing they agree
on, and arrivals are clamped into that same band — so the way back is open at
the exact point you land. A consequence worth knowing: where a neighbour spans
only part of an edge, the middle of that edge can legitimately be a wall.

### Testing

`sender()` split into `_flush_queue()` so a test can drive the **real** batching.
Every previous test walked the *queue*, which is why none of them could see
fault 1. New invariant: **a re-sync survives the sender and really does clamp.**
The "no silent wall" invariant now leans from inside each link's live band
rather than from a screen's centre.

Twelve invariants, eight suites.

## 2026-07-28 — A third device: the lane was perfect, Windows just had no route to it

Doug added a Managed Laptop. *"adding process went really smoothly until i tried
to pair it."*

The guest was flawless: `openspanble@device-1` active, hci1
(a real dongle address, redacted), advertising name set, `:9957` listening, GATT registered.
From Windows, `127.0.0.1:9957` simply timed out — the VM's NAT table had rules
for 9955, 9956, ssh and audio, and nothing for the new device.

`ensure_device_forwards()` was only ever called at startup and during
provisioning, so a device added afterwards had no rule. The pair worker then
looked like it was working: its readiness check runs `ss -ltn` **on the guest**,
which passed, and only the next step — talking to the port from Windows — could
fail. A healthy daemon behind an unreachable port.

Fixed by ensuring the forwards from inside the pair worker, before anything
needs the port.

**And a second fault the save exposed:** the display editor minted new screen ids
as `mac-N` whatever device was open — the last hardcoded remnant of the
two-device model, living in the one dialog used for every device. The laptop's
second screen therefore came out as `mac-2`, the id the Managed Mac's own second
screen already had. Ids are now derived from the device being edited, the
fallback in `validate_mac_displays` takes the device too, `dedupe_display_ids()`
heals a config that already carries a clash, and the dialog's title and heading
name the actual device instead of saying "Managed Mac" at everyone.

## 2026-07-28 — Transitions go through the middle: no re-sync on the way out

Doug: *"all transitions seem to go through corners, let's try to go through
middles, not corners. Another thing is i can see that the ipad frequently brings
down the notifications or the select open app menu, something about the frenetic
nature of the mouse going in and out of it."*

Both are the same cause. A re-sync drives the pointer into an edge and parks it
at a corner — that is how it establishes truth, and it is unavoidable when the
position is genuinely unknown. But it was running on **every exit**, so every
crossing dragged the pointer along edges. On iPadOS an edge **is** a gesture:
the top pulls Notification Centre, the bottom the Dock and app switcher, the
side Slide Over. Going in and out repeatedly opened them constantly.

It was also unnecessary. Between re-syncs the model cannot drift — no motion is
discarded and every jump is paid for on the wire. So the position is established
**once**, when the lane comes up, and re-established only when it becomes
genuinely unknowable: a dropped lane. In between, a transition travels between
two interior points.

Measured on the live layout:

| | once per session | every crossing after |
|---|---|---|
| iPad | 3 reports | **1** |
| Managed Mac (via DISPLAY4) | 110 reports | **8** |
| Managed Mac (via DISPLAY1) | 110 reports | **12** |

Was 106–110 reports on **every** crossing.

**Known remaining tension**, not yet acted on: a crossing has to *reach* an edge,
and the motion that carries the model past it is already on the wire before
routing sees it. On an iPad that overshoot is exactly the gesture. Clipping the
transmitted motion at the edge would fix it, and needs `_route_motion` to report
how much it consumed before the crossing so `_mouse_proc` sends only that.

## 2026-07-28 — Pin the axis you left by. No corners.

Doug: *"we shouldn't need to ground truth -- in my mind what we do is when we
leave a monitor to go to another device we send a hard +3000px push in that
exact same direction as final action. We don't need to ground to corners if we
do that, and we preserve relative mouse location every single time."*

Right, and better than what was there. Leaving an edge only needs ONE thing to
become true: the axis you crossed. Push hard that way as the last act and the
device clamps the pointer on that edge, making the coordinate a measurement —
while the other axis is never touched, so position ALONG the edge is preserved
exactly, for free. No corner is visited and nothing is dragged around the
outside of the arrangement.

The corner walk survives for the one case that actually needs it: when nothing
at all is known, once, when a lane comes up. From then on the one-axis pin is
sufficient, because the along axis cannot drift — no motion is discarded and
every jump is paid for.

| leaving | reports | records | along axis |
|---|---|---|---|
| iPad | 2 (~30 ms) | its edge, same height | preserved |
| Managed Mac | 19–36 (~285–540 ms) | its edge, same height | preserved |

**And the dead corner was too big.** `CORNER_ZONE` was one inch — modest on a
32" panel, but a third of the iPad's 6-inch edge, which is why iPad → PC
bottom-left had stopped working. Now half an inch, capped at 15% of any span:
worst dead share across the whole desk is 16%, was 33%.

**Known trade-off:** the hard push necessarily goes past the edge, and on iPadOS
past an edge is a gesture. Leaving the iPad rightward pushes ~940 px beyond its
edge. If Slide Over still appears, the fix is to clip what we TRANSMIT at the
edge on the crossing report, which needs `_route_motion` to report how much it
consumed.

## 2026-07-28 — Momentum to cross; and a click that could vanish

Doug: *"i don't EVER want a GENTLE mouse movement to cross a boundary. I float
near the ipad boundary, it gets shoved to the right or left, i should initiate
all shoves with appropriate momentum myself, if i am being gentle near an edge
that is because i am trying to delicately select something along it."*

A crossing now also requires the hand to be **moving** — ≥1200 raw mouse counts
per second over the last 100 ms. Reaching an edge slowly just stops there.

Refusing does not desync anything: at a device's OUTER edge the target's own
window server clamps its pointer in the same place the model clamps. That is
only true when LEAVING a device, so the gate applies there and never to a seam
between two screens of the same device, where the target's pointer really does
flow across — refusing there would put the model somewhere the pointer is not.
Entering from the PC is gated too: sliding a window to the far edge of a monitor
must not fling control onto another machine.

### The doubling: it is structurally impossible in our stack

A 25-agent investigation. The decisive argument is not statistical:

> A HID keyboard report is **state, not an event**. macOS derives key presses by
> DIFFING consecutive reports. Two identical reports diff to zero new edges — so
> a duplicate introduced anywhere (TCP, D-Bus, GLib, BlueZ, the link layer, the
> macOS HID stack) is a **no-op by construction**. The only way to produce two
> Tabs is a genuine present → absent → present on the wire.

Every emitter of a keyboard report was then enumerated. On the Mac lane the only
drop-and-re-add path is the clipboard chord, and `clipboard` is `false` for the
Mac. The BLE side was ruled out separately: one notify per command, no
retransmit, no replay on re-subscribe. Thirteen theories were killed, including
every one that would have doubled ALL input.

Three real faults fell out of it anyway:

1. **A click could vanish.** `batch["button"]` was a bare bool and the button
   state was read again at SEND time, so a click whose press and release fell
   inside one 8 ms tick was emitted once carrying the post-release state — the
   press never went out. Transitions now carry their state at QUEUE time and
   every one is sent. *A click that does not register is exactly what makes a
   person click a second time.*
2. **`Ctrl+Alt+V` was not gated on capture.** With nothing captured the queued
   target is `None`, the sender falls back to the default port, and it typed the
   Windows clipboard into whichever device owns it.
3. **Nothing counted what reached a device.** "It doubled the Tab" and "the Tab
   never arrived" look identical from the far end. Every keyboard report is now
   logged with its modifiers and usages.

New tests count rising EDGES rather than reports — the only way to see a click
that was never sent.

---

## 29 July — arrangements, and the last of the pop-outs

### Two desks, one of which is the Mac at 2K

Doug: *"I sometimes change my managed mac's landscape screen to 2k resolution.
So I need to be able to duplicate the current arrangement and then resize/arrange
all screens and devices attached to it."*

A resolution is not a cosmetic field here. The physical size a screen is drawn
at, the desk units it spans, the scale from HID counts to target points, and the
crossing band on each of its edges are all derived from it — so switching that
panel between 4K and 2K means re-entering the desk, twice a day, from memory.

Arrangements are named copies of the desk, in `ROOT\profiles\*.json`. What one
carries is the line that matters:

| in the arrangement | belongs to the machine |
|---|---|
| screens, positions, sizes, resolutions, rotations | `radio` — a physical dongle |
| which devices exist, their names and input settings | `port` — a lane on the guest |

**Radios and ports never travel.** Bonds live on the guest *per radio*, so an
arrangement that remembered which dongle drove the Mac would, after a dongle
moved, point that lane at a radio holding no bond for it — a device that pairs,
goes green, and does nothing. Loading takes those two fields from whatever is
running now, matched by device id.

Three things fell out of building it that were not obvious:

1. **`normalize_config` returns a fresh dict from a field whitelist**, so the
   arrangement's own name did not survive being loaded. The app forgot which
   arrangement it was showing the moment it showed it.
2. **The canvas keeps references, not just data.** `ipad` and `selected` point at
   specific display dicts. A switch that rebuilt the config and left those
   behind would keep a live handle into the desk that is no longer on screen —
   valid-looking through every redraw. Installing a config is now one method,
   `MultiArrangeCanvas.adopt()`, and it is the only place those are rebuilt.
   Connection state is deliberately kept across the swap: a different picture of
   the desk does not disconnect anything.
3. **A selected arrangement is written through on every save.** Otherwise there
   would be a saved copy and a live copy drifting apart, and switching away —
   the entire point — would silently discard everything done since. There is no
   unsaved state to lose because there is no unsaved state. `Save as` names an
   unnamed desk; it is not a commit step.

### The dialogs stopped being windows

Doug, on the display editor appearing on a screen he was not looking at:
*"i don't want this program to generate popouts — if i click into something i
want it right there."*

Four `tk.Toplevel` dialogs remained. Rather than rewrite each, `FrameModal` is a
`tk.Frame` that answers the window-manager calls a dialog makes — `title`,
`transient`, `geometry`, `minsize`, `grab_set`, `protocol`, `destroy` — and
lives inside the app window over a scrim. Each dialog changed one line.

Two of its methods do real work, and both are failures that would have been
silent rather than loud:

- **`bind` installs on the window, not on the frame.** A Toplevel sees events
  from its children; an intermediate frame does not. Left alone,
  `win.bind("<Return>", ok)` would stop firing the moment focus entered the
  dialog's own entry box — which is the only place focus ever is. The bindings
  come back off at close, restoring whatever was there before.
- **`grab_set` remembers the previous grab and hands it back.** A confirm opened
  over the display editor would otherwise leave nothing grabbing, and the editor
  underneath would quietly stop being modal.

`geometry("900x420")` now sizes the card, clamped to the window; `"+x+y"` is
discarded, because a card in the middle of a window has no screen coordinate.
The two native `messagebox` calls left in the legacy `--setup` path went too, and
the unused `messagebox`/`simpledialog` imports with them.

`test_frame_modal.py` runs headless against a withdrawn root and ends by
**parsing** all four modules for `Toplevel`/`messagebox`/`filedialog`/
`simpledialog` — parsing rather than grepping, because `dark_confirm`'s docstring
names the native dialog it replaced and a text search cannot tell that from a
call.

### What the pre-swap review found

The build was finished and verified before it was allowed to replace the running
app, and a 13-agent adversarial review ran against the same commit in parallel.
Six defects survived refutation; two were refuted. Worth recording because one of
them had been live for far longer than this change.

**A config's top-level settings were being erased on every load.**
`normalize_config` builds its result from a whitelist — `version`, `monitors`,
`devices`, plus the derived `portals`/`links` — and both side-button crossing
settings live at the top level. So every launch dropped them and the next save
wrote the config back without them, while the checkboxes went on reading that
same config and showing whatever was left. `openspan_config.json` had neither key
in it. **Press-to-jump, the thing Doug called flawless, was switching itself off
between sessions** and nothing said so.

Arrangements would have made it worse (a switch is a load), but they did not
cause it. The fix carries unrecognised top-level keys across by DIFFERENCE, not
by name — listing the keys works exactly until the next setting is added at the
top level and nobody remembers the line exists.

**An arrangement's name and its filename could drift apart.** `_profile_path`
sanitised the name; everything else compared the raw one. "Mac 4K (day)" became
`Mac 4K _day_.json`, after which the write-through's guard (is this name in
`list_profiles()`, which returns stems?) never matched again — every edit lost at
the next switch — Delete did nothing, and "Desk 2.0" and "Desk 2 0" silently
overwrote each other. Now sanitised once, in `profile_name()`, and the sanitised
form *is* the name from that point on: box, config and file all hold one string.

**One click in the dimmed area destroyed a half-filled display table.** The
scrim covers the whole window and was bound to close. The Toplevels it replaced
had no click-outside gesture at all, and three of the four dialogs hold typed
values — so this was a regression the conversion introduced, with no undo and no
warning. The scrim now swallows the click.

**The display editor lost its buttons at six screens.** The card was pinned at
the requested 420px and the button bar was packed last, after a body with
`expand=True`, so the bar was never laid out — and with no window left to drag
bigger, the editor was a dead end. Two changes: the bar is packed first and
anchored to the bottom, so the rows are what get squeezed; and `geometry()` is
now a floor rather than a size, with `_fit()` measuring the built card and
capping it to the window. Measured at 1–14 screens (the editor caps at 8 rows),
Save and Cancel are always inside the card.

**`dark_confirm` over a modal left nothing grabbing.** The commit message claimed
otherwise; it was only true for FrameModal-over-FrameModal, and the display
editor's confirms go through the older overlay. Now true for both.

**Deleting an arrangement relabelled the one in use.** `_delete_profile` cleared
the name box unconditionally.

Refuted, and worth writing down so they are not re-raised: `ensure_device_forwards`
on the Tk thread (real call, but every leg of the asserted freeze fails against
the code), and `save_profile` stripping `links` (the mechanism is real —
`normalize_config` reads a missing `links` as "pre-adjacency" and re-runs a
one-time snap — but it does not move anything on this desk). `links` is kept in
the snapshot anyway: a latent trap that costs one line to close.

## 29 July, later — the gate its own restart switched off

Doug: *"my second profile is not respecting my selection to not allow device
crossing without pressing the mouse button"*, and, when I narrowed it wrongly to
the second symptom, *"it doesn't change the diagnosis, it is another symptom, it
was absolutely crossing on mouse proximity without touching the button as well."*

He was right on both counts. Two independent faults, and his `portal.log` holds
the first one in five consecutive lines:

```
[portal] stayed put at the right edge -- hold a mouse side button to cross
...
[portal] ready — 3 portal(s) loaded.
[portal] no side button has ever arrived from this mouse -- falling back to a
         deliberate push so nothing gets stuck
[portal] >>> Managed Mac mode ON via x=4 -> Mac Display 3
```

The gate working, a restart, the gate gone, a crossing he did not ask for.

**`_side_seen` was per-process.** The lockout guard asked "has a side button
arrived *in this process*", and the portal restarts on every config change —
which now includes every arrangement switch. So each switch put the gate back to
"this mouse appears to have no side buttons" and let ordinary movement through,
while the checkbox went on saying it would not. Nothing was wrong with the
profiles: both files and the live config carry
`cross_requires_side_button: true`.

The fix is to stop learning something the operating system already knows.
`GetSystemMetrics(SM_CMOUSEBUTTONS)` returns **5** for his mouse, and it returns
it the instant the process starts. A guard that has to be taught cannot survive
a restart; one that asks cannot forget. The old "never seen one" path remains
for a mouse that genuinely has two or three buttons, because refusing every
crossing there would strand the pointer on a target.

*The escape-hatch test passed throughout, on a five-button mouse, because it
asserted `_side_seen = False` and inherited the rest from the machine it ran on.
It now states the mouse it is describing.*

### And the keyboard was still not dumb as a rock

The second symptom — *"when i was typing it was switching to the ipad - i think
when i hit the letter i"* — is a different fault with the same shape as one he
reported hours earlier.

Three combinations were being swallowed with a bare `ctrl and alt` test:

| | was | through the Mac's alt→cmd remap |
|---|---|---|
| `Ctrl+Alt+I` | enter/leave a device | **Cmd+Option+I** — Web Inspector, and the letter i |
| `Ctrl+Alt+Q` | legacy bail | Cmd+Option+Q |
| `Ctrl+Alt+V` | type the PC clipboard | **Cmd+Ctrl+V** — the paste he reported broken, twice |

A combination the portal swallows is one the target never sees. `Ctrl+Alt+V` had
been given an exemption for `keyboard_verbatim` devices earlier in the day, which
only narrowed the bug — the hotkey was the problem, not which devices it applied
to.

All three now fire **only while nothing is captured**. Esc ×3 remains the bail
and is the one chord that is always ours; it is printed on every entry line in
the log for exactly this reason. Paste-from-the-PC moved to `Ctrl+Alt+Shift+V`,
which already meant precisely that for the iPad — a device with the helper
shortcuts runs them, anything else has the clipboard typed into it.

Doug's own words are the rule this should have been written to in the first
place: *"the keyboard should be dumb as a rock and simply do what i do."*

## 29 July — a radio the VM had lost, and no way to see it

Doug: *"I had to unplug my external bluetooth devices - how can i get openspan to
recognize them again and get back to functioning state"*.

The answer turned out to be two `VBoxManage` commands and knowing to run them,
which is not an answer.

**What actually happens.** A Bluetooth dongle reaches the guest by USB
passthrough. VirtualBox auto-captures a filtered device **at the moment it
arrives** — so when a dongle is replugged and Windows' own driver binds it first,
it sits on the host marked `Busy` and the VM never gets it. The dongle is
plugged in, its filter matches, the VM is running, and the guest cannot see it at
all. His machine, when he asked:

| radio | filter | host state | in the VM |
|---|---|---|---|
| Intel internal — iPad | `IntelBT` | Captured | yes |
| TP-Link UB500 — Mac | `TPLinkBT-Port1` | **Busy** | **no** |
| TP-Link — laptop | `TPLinkBT-Port2` | **Busy** | **no** |

**Why the app could not say so.** It had never looked at the host's USB list —
`grep` for `usbattach`, `usbhost`, `usbfilter` across the whole tree returned
nothing. So a device whose radio was gone and a device that simply would not
connect produced exactly the same thing on screen: a lane that never went green.
That is the actual defect. The dongle being claimed by Windows is VirtualBox's
behaviour and is not going to change.

**What decides it.** A radio is *lost* when a device on the host matches one of
the VM's own active USB filters and the VM is not holding it. The filters are the
right definition because they are the machine's own statement of what belongs to
it — better than a list of vendor ids compiled into this file, which the user
never edits and which would be wrong the day a different dongle is bought.

Four pure functions parse it (`parse_usb_host`, `parse_usb_filters`,
`parse_usb_attached`, `radio_report`), two thin ones call VirtualBox
(`read_radio_state`, `reclaim_radios`). The Bluetooth tab reports the count and
offers one button that names how many it will take back.

Three things the fixtures pin down, all of them real on this desk:

- **Both TP-Link dongles are `2357:0604`.** Two devices, one vendor:product pair
  — anything keyed on the pair rather than the UUID would see one dongle.
- **A Blue Yeti microphone is also `Busy`.** Half the devices on a desk are
  Busy; only the ones a filter claims are ours.
- **The Intel adapter reports no `Product:` line at all**, so it is named from
  `Manufacturer`. A parser that assumed the field exists would drop the one
  radio that was working.

`test_radio_usb.py` runs against the real output captured from this machine in
exactly the broken state, with the dongle addresses removed. It contacts no VM
and attaches nothing.

**Deliberately not done here:** nothing in this scans, pairs, or connects. It
puts the USB device back where the guest can see it, and then says to press
Connect. Bonds live on the guest under the adapter's own MAC and a dongle carries
its MAC with it, so the same dongle returns to the same bonds — no re-pair.

### "I clicked reclaim, don't think anything happened"

It had happened. That was the problem.

`usbattach` returned zero, so the app said "attached" — and VirtualBox had taken
both dongles off Windows and never handed either to the guest. The evidence, from
three places that all agreed:

- `list usbhost` said **Captured** — VirtualBox had them
- `showvminfo` listed only the Intel as attached — the VM did not
- in the guest, `lsusb` showed one adapter and `dmesg` showed **no USB event
  since boot** — nothing had arrived

So the dongles belonged to nobody: taken from Windows, never delivered. A worse
state than the `Busy` one they started in, produced by the button meant to fix
it. Every further attach returned:

> `USB device 'TP-Link UB500 Adapter' ... is busy with a previous request.`

That is a VirtualBox host-side device object with an unfinished request against
it — the residue of a dongle unplugged while the VM held it. No number of
retries clears it; the device object has to be re-created, which means a physical
replug or a VM restart. The replug recovered two of the three.

**The defect was believing the exit code.** An exit code says whether the
*request* was accepted. The transfer is asynchronous and can fail after it. So
success is now defined as *the VM holding the device*: attach, wait, then check
the VM's own attached list, and treat "accepted but never landed" as the failure
it is. `"busy with a previous request"` is matched by name and reported with the
one thing that actually clears it — and is not retried, because retrying provably
cannot work.

**And the outcome was going somewhere he was not looking.** It went to the
Bluetooth panel's log box, while the line he was reading still said "1 of 3". A
success message next to a stale count is indistinguishable from nothing
happening, which is exactly how he described it. The status line now carries the
outcome, and a wedged radio's explanation is not overwritten by the bare count it
already explains.

One more thing this turned up, unresolved and worth an eye: the adapter at
`AC:A7:…` — which `openspan_config.json` assigns to the **Managed Mac** — carries
the guest-side alias `OpenSpan Managed Laptop`. The app resolves controllers by
MAC so nothing misroutes today, but the alias and the assignment disagree, and one
of them is stale.

### The root cause, and what a stranger can do about it

Doug: *"i need this resolved in a normal human's circumstance of not having you to
look into and orchestrate the file … i am considering what will happen when i send
this to my friends for testing."*

Fair. Everything up to here needed someone reading `VBox.log`.

**A dongle's USB serial number IS its Bluetooth address.** Both of his report
`ACA7F1299FCB` and `3C6AD23CD44E` — the two radio addresses in his config with
the colons removed. That single fact carries most of the solution, because it can
be read from the HOST, with the VM down and the guest unreachable, which is
precisely when identification matters. The app no longer says *"TP-Link Bluetooth
USB Adapter"*, a phrase useless to anyone holding two identical dongles. It says
**"Managed Laptop's dongle"**.

It is treated as a convention, not a law: twelve hex digits or the mapping is
skipped and the product string is used, so a dongle that does not follow it still
works, just unnamed.

**And it explains the failure.** Both TP-Link filters matched `2357:0604` and
nothing else, so two identical dongles arriving together raced two identical
filters — which is why replugging both recovered one every time and left the
other captured-away-from-Windows-but-never-delivered. `VBoxManage usbfilter
modify --serialnumber` pins a filter to exactly one device, **accepts it on a
running VM**, and there is then nothing left to race.

Note this is an *assignment*, not a match. Asking each filter "which device do
you match" has two answers and no way to choose — the same ambiguity VirtualBox
is losing to. Filters are grouped by what they match on, and within a group the
free dongles are paired off against the unpinned filters, deterministically by
serial.

`Repair radios` is now one button doing the whole ladder, cheapest first:

1. **Pin any ambiguous filter.** Free, no restart, and it is the actual cause.
2. **Attach what the VM has lost**, then verify the VM took it.
3. **Name whatever is still missing by the machine it serves**, with the one
   physical action left — and say to do them *one at a time*, because two
   arriving together is what wedged it. A captured-but-never-delivered dongle
   cannot be rescued by any command: `usbdetach` refuses it as *"not attached to
   this machine"*. At that point hands are the only tool, and saying so plainly
   is the whole job.

**Still unexplained, and honestly so.** One of his two dongles wedges every
single time while the other recovers every time. `VBox.log` shows a TP-Link
attaching only ever to port 2 of RootHub#1, and for the second dongle **no attach
attempt is logged at all** — captured, then silence, no error. That is not the
filter race (the filters are pinned now) and not something the app can fix. The
two are different models; the failing one is not the UB500. Next step if it
persists: a VM restart, which rebuilds the USB state from scratch — at the cost
of an iPad re-pair, which the app now says out loud rather than discovering
afterwards.

## 29 July, clean boot — what the baseline actually proved

Doug rebooted the host and asked for the whole startup sequence to be watched
from t=0. That single observation overturned the theory the previous three
commits were built on.

```
 0s  VM=poweroff  attached=0  Intel@14=Busy TP-Link@3=Busy TP-Link@4=Busy
11s  VUSB: Attached '[proxy 8087:0aaa]' to port 1 on RootHub#1 (FullSpeed)
19s  VUSB: Attached '[proxy 2357:0604]' to port 2 on RootHub#1 (FullSpeed)
19s  VUSB: Attached '[proxy 2357:0604]' to port 3 on RootHub#1 (FullSpeed)
31s  guest = hci0 hci1 hci2
```

**There was never a filter race.** Two identical dongles, two identical
`2357:0604` filters, nothing pinned — all three radios captured *and* delivered
in 32 seconds. The serial pinning built to fix that race was a fix for a
non-problem, and it caused a real outage: a filter pinned to a serial VirtualBox
often cannot read stops matching altogether. `radio_filter_plan` and
`pin_radio_filters` are deleted.

What is true is far simpler, and every recovery instruction now follows from it:

> **A clean host works. A host whose VirtualBox USB layer has been wedged stays
> wedged until it is rebooted.** Nothing else clears it — `usbdetach` refuses a
> device the VM does not hold, restarting the VM spreads the fault to radios that
> were working (it took out the internal Intel that had been fine all day), and
> killing `VBoxSVC` releases the *capture* without restoring *delivery*.

### Three defects the baseline exposed

**A captured device is listed twice.** `list usbhost` reports it as itself and
again as VirtualBox's proxy stub — different UUIDs, same vendor, product and
port. Counting both made a perfectly healthy machine read *"3 of 5 radios are
attached, 2 are missing"*: a false alarm produced by the feature built to explain
a real one. Merged on `port`, which is the only field always present.

And the pair had to be *merged* rather than filtered, because each half knows
something the other does not:

- the stub's address carries the serial (`…pid_cafe#aca7f1299fcb#…`) even when
  the device reports no `SerialNumber` field at all
- the original carries the UUID `usbattach` accepts

**`USBAttachActive` reports the PROXY's UUID.** So "does the VM hold this?" asked
against the real device's UUID answers *no* for a device the VM is holding
perfectly well. Both UUIDs are kept and the question is asked against the set.
This was the entire remainder of the false "2 missing".

**Every guest command was decoded with the ANSI codepage.** `ssh_guest` used
`text=True`, and `systemctl status` prints `●`/`○`. The UTF-8 bytes raised
`UnicodeDecodeError` inside subprocess' reader *thread*, where the surrounding
`try` cannot see it — the traceback went to a console nobody watches and the
output came back **empty**. Every status check built on that was silently blind.
Now `encoding="utf-8", errors="replace"`, here and in `vbox()`.

### The banner tells you why

`"Booting the bridge… (~90s)"` is the worst thing this app can say when something
is wrong, and it is what it said for as long as it was left running. `why_not_ready()`
walks the chain in dependency order — VM runs, guest answers, radios exist, BlueZ
registers them, `openspan-btready` finishes, daemons listen — and the first unmet
condition *is* the message. Computed on a worker, throttled to 12s.

Worth knowing: tonight's stall was **legitimate**. `openspan-btready.service` has
`TimeoutStartSec=200` and everything queues behind it; at the 90-second mark it
was 67 seconds in and working. The app was right to wait and wrong to be silent.

### Shutdown now leaves nothing

`_full_stop` fired `poweroff` and closed 400 ms later, claiming "nothing lingers".
The power-off is asynchronous, so the app was routinely gone before the VM was;
and `VBoxSVC`/`VBoxSDS` kept running — which is what Windows names when it says an
app is preventing a restart, and answers Doug's question about it exactly.
`stop_virtualbox_backend()` waits for a real `poweroff`, then ends `VBoxSVC`
(a COM server VirtualBox relaunches on demand). `VBoxSDS` is a Windows service and
is left alone.

## 29 July — the pointer chain had no acceleration in it at all

Doug: *"i put motion select a pointer speed to the middle on windows -- and
turned on enhance pointer precision, this feels better actually. does it make
sense or is it placebo"*

It makes sense, and finding it corrected a wrong prior of mine. I had flagged
"Enhance pointer precision" as a risk on the reasonable-sounding grounds that it
would stack with the target's acceleration. It cannot, and here is why:

- our `pointer_accel` is **0.0** on both compensated devices
- `compensate_target_accel` is **on**, so the target's own curve is inverted out
- EPP was off, so Windows contributed none

The entire pipeline was **strictly linear** — a fixed pixels-per-count ratio from
hand to screen, at every speed. That is exactly what a pointer should not be: low
gain is wanted for precision and high gain for travel, and a linear pointer forces
one compromise to serve both. Turning EPP on did not add a second curve. It added
the only one.

Two further mechanisms, both real:

**Speed 10 halves the quantum.** At 10 the multiplier is unity, so one mouse count
becomes one pixel. Above 10 Windows multiplies *before* rounding to integer
pixels, so at 13 the smallest expressible movement was ~2px. The hook reads
*pixel* deltas, so the source grid really was twice as coarse as necessary.
Slower slider, finer control.

**EPP carries sub-pixel remainders, which matters more here than usual.** With it
off and a >1 multiplier, slow motion arrives clumped (`0,0,2,0,0,2`); with it on,
evenly (`1,1,1,1`). The Apple inversion is a function of per-report MAGNITUDE and
is steep at small magnitudes, so a jittery magnitude stream becomes a jittery
count stream which the Mac then re-expands. Evenly spaced in, evenly spaced out.

### The calibration assumption this exposed

His tuned combination is `sensitivity 0.747` on the Mac, macOS tracking at **notch
6 of 10**, macOS acceleration ON.

macOS acceleration being ON is *required* — with it off the inversion would be
pre-distorting for a curve nobody applies. That part is right.

But `_APPLE_CURVE` is Apple's table **at the default slider position**, and notch
6 of 10 may not be it. If it is not, every compensated report is wrong by whatever
that slider does — silently. Two things follow:

1. **It cannot break the position model.** Every crossing re-syncs by shoving to
   the display-union edge and letting the target's window server clamp, which
   makes the coordinate a fact. Accumulated inversion error is wiped at each
   boundary. Curve error degrades *within-screen* precision and nothing else —
   which is measurement-by-clamp doing exactly the job it was designed for.
2. **A scalar can only match a shape at one speed.** `sensitivity` is absorbing
   the mismatch, and a single multiplier can cancel a scale difference but not a
   curve difference. That predicts a specific symptom: correct at the speed it was
   tuned at, drifting slightly at others — most noticeable mid-range, least at the
   top, where Doug reports flicks are sloppy by intent anyway.

Settling it needs one number off the Mac: `defaults read -g com.apple.mouse.scaling`,
compared against the default for that device. Until then this is a known,
bounded unknown rather than a mystery.

### Settled: the Mac is at the calibration point

`defaults read -g com.apple.mouse.scaling` → **1**, and notch 6 turned out to be
where the slider started. So the Mac sits exactly at the default the shipped curve
was reconstructed at. The inversion is inverting the correct transform, and
`sensitivity 0.747` is a preference, not a scalar compensating for a mismatched
curve shape.

The prediction therefore flips: he should *not* be able to feel mid-range drift on
that machine. If he ever does, the cause is elsewhere and this is not the place to
look.

`defaults read` is worth recording as the right tool for a managed machine, for the
reason that is easy to get backwards: it does not open the plist. It asks
`cfprefsd` over XPC for a value in the user's **own** domain, so it needs no sudo,
no admin, and — the part that usually bites Terminal under MDM — no Full Disk
Access. Reading the file directly has strictly more surface than the command that
looks heavier.

**Left open, and it is the more interesting half:** `Managed Laptop` also carries
`compensate_target_accel: true`. If that machine is not macOS, the app is
pre-distorting every report for a curve it never applies — which would be a
concrete cause of "not quite as good" rather than an unavoidable one. Its
`sensitivity` is also still 1.0, i.e. never tuned, where the Mac is at 0.747.
Both are one question away and neither should be guessed at.

### Both Macs are at the calibration point; resolution is not a feel knob

The Managed Laptop is a Mac too, with duplicated settings, and its
`com.apple.mouse.scaling` also reads **1** at the default slider notch. So both
compensated devices sit exactly where `_APPLE_CURVE` was reconstructed, and
`compensate_target_accel: true` is correct on both. That closes the calibration
question for this desk entirely.

Worth recording because it was on the suspect list and should come off it:
**display resolution cannot affect how the pointer feels.** `res_w/res_h` appear in
one expression — `scale_x = gain * display["w"] / res_w` — and that is the MODEL,
converting wire motion into where the virtual cursor believes it is. It never
touches what goes on the wire. A mis-entered resolution costs crossing accuracy,
not smoothness. `pointer_gain` is model-side only for the same reason.

So the entire feel difference between the two Macs is one number: `sensitivity`,
0.747 on one and the untuned 1.0 on the other. Identical curve, identical
compensation, identical everything else — which makes it a clean prediction rather
than a suggestion. If matching the number does not match the feel, something
outside this table is involved and that is worth knowing.

### The sensitivity slider gets notches

The slider was a continuous `ttk.Scale` over 0.1–3.0. Its readout formatted at two
decimal places while `apply_and_close` stored at three, so it showed `0.75` and
wrote `0.747`. The number on screen was not the number in the file, and a value
arrived at by feel could not be read back off the dialog — which is exactly the
number the whole calibration question above turns on.

Eighteen notches now, deliberately non-uniform: `0.25, 0.5`, then 0.05 steps from
`0.55` to `1.0`, then `1.25, 1.5, 1.75, 2.0, 2.5, 3.0`. Everything usable lives in
that middle band, so that is where the resolution goes; above it the difference
between 2.5 and 2.75 is not a decision anyone makes.

Two calls worth recording because either could have gone the other way:

**The ceiling stayed at 3.0, not 2.0.** Capping at 2.0 would have been tidier and
covers every value on this desk, but it makes the top of the old range
unrepresentable — a config already carrying 2.5 would have no notch to land on.

**Notches snap on DRAG ONLY, never on load.** `0.747` on the Mac and `0.686` on the
iPad are real tunings. Snapping at load would mean opening the dialog to check
something else and pressing Apply quietly moved both. So the widget runs in notch-
INDEX space — the handle physically cannot rest between notches — while the value
variable is written only when the handle moves. Opening the dialog positions the
handle at the nearest notch and leaves the stored value untouched; the readout
shows three places when it has to (`0.747`), two when it does not (`0.75`).

That split is why there are two functions rather than one: `nearest_notch_index`
only positions, `snap_sensitivity` only changes. A test that checked snapping alone
would pass on an implementation that silently rewrote both devices on first open,
so `test_sensitivity_notches.py` checks the load path first and the drag path
second — 34 checks, suite now 352.

**Consequence to be aware of:** `0.747` is no longer typeable from the slider; the
nearest notch is `0.75`. Matching the Managed Laptop to the Mac by hand means
either accepting `0.75` on both or editing `openspan_config.json` directly.

### Applied: every device on the 0.75 notch

All three devices are now `sensitivity: 0.75` — `Managed Mac` from 0.747,
`Managed Laptop` from its never-tuned 1.0, `iPad` from 0.686.

Doug's rule, and it is the better one: **no stored value that the settings
cannot reproduce.** A number reachable only by hand-editing JSON is a number
nobody can explain later, and both of the odd values here came from the
old slider's two-places-shown / three-places-stored split rather than from
anyone choosing them.

That makes the Managed Laptop prediction from the previous entry cleanly
testable at last: identical curve, identical compensation, identical
sensitivity. If the two Macs still feel different, the cause is outside this
table — which was the point of closing the gap.

The iPad moved 0.686 → 0.75 (+9%), the largest single change here, so that is
the one to watch for feel.

### W1 — the window was two monitors tall because of two `expand=True` flags

A five-pass read-only survey plus four adversarially-attacked design proposals
converged on one mechanical cause. `arr_wrap` and the arrangement canvas inside
it were the ONLY expanding chain in the left column, so 100% of the window's
surplus height landed in a canvas whose aspect-fit drawing is width-bound and
cannot grow into it. The Bluetooth Treeview had the same disease on the right.
Measured on the machine: the live window was **1921 x 2120** at 96 DPI (1.00x),
spanning two monitors.

Both flags come off together — capping only the canvas moves the void from
PANEL-coloured to CARD-coloured and delivers nothing.

`MultiArrangeCanvas._fit_height()` now requests exactly the height the drawing
occupies at the current width. **No 0.94 factor**: `_scale` already applies that
inset to the drawing, and taking it again on the container would shrink the
picture 6% while the change claims to leave it pixel-identical. That mistake was
in the plan and was caught before it shipped.

It is hooked to `<Configure>`, `adopt()`, `_release()` and `save()` — the world
aspect changes on a DRAG and on an arrangement switch, and **neither fires
`<Configure>`**, so a Configure-only hook goes stale on the app's primary
gesture. It is deliberately NOT hooked to `redraw()`: `_drag` calls that every
motion tick, and changing height mid-drag changes `oy` in `_scale`, so `c2w`
maps the cursor to a different world point and the rectangle jumps under it.

**The correctness fix hiding inside the cosmetic one.** Nothing in this file ever
set a window height — `geometry()` declared one at import and `_set_win_width`
parses it back out and puts it straight back. Meanwhile `minsize(940, 680)`
permitted a window far shorter than the left column needs, at which size
"System control" and "Bluetooth radio" simply do not get packed. There is no
scrolling anywhere by design, so there was no scrollbar, no clipped edge and no
way to learn those panels existed. `App.__init__` now measures the built content
and derives both the opening height and the minimum from it, and
`test_layout_budget.py` asserts `minsize >= content` so the mode cannot return.

One instruction in the plan was wrong and was overruled during implementation:
BtPanel's Treeview keeps `expand=True`. With `side="left"`, `expand` governs the
leftover WIDTH beside the scrollbar — setting it False opens a 216px hole. The
height cap is delivered entirely by `body`, which no longer expands.

`TButton padding=8` → `(10, 3)`: measured 39px → 29px per button, 10px back per
stacked row, and this column stacks 13 of them.

Suite 352 → 399 checks, all green.

### W2 — the screen owns its settings

Right-click any screen on the arrangement canvas. Resolution, refresh, rotation
and diagonal now live on the object they describe instead of behind three global
buttons, two of which acted on the wrong object entirely: `canvas.rotate()` only
ever touched `targets[0]["displays"][0]`, and "Configure Mac displays…" passed no
`device_id`, so it resolved to the first device regardless of which one you meant.
Both are deleted, not relocated.

Nothing had to be invented to do it. `_hit_key` already resolved a pointer to a
display, `_detail_lines` already formatted res/Hz/rotation/diagonal for the hover
card, and `BtPanel._popup` was already a working, reentrancy-safe menu on the same
window. The feature was one binding and one handler wide.

**Windows monitors are read-only, and that is the honest design.** Doug: *"we just
accept the state of windows, maybe just like a refresh now button tho. I think the
only thing that we'd want to change on the windows manually is the monitor size."*
Windows owns position, resolution, refresh and the primary flag — the app can
query all of them, and letting a user hand-edit them only authors a lie the app
then draws. Windows does NOT reliably know physical size; that is why "Screen
sizes…" existed at all. So the local menu shows res and Hz as disabled info, makes
only the diagonal editable, and adds **Refresh now**, which MERGES by monitor name
rather than replacing — a refresh that reset `diagonal_in` would destroy the one
field only he can supply.

**The app had been asserting a number it did not know.** `_normalize_monitor`
hard-defaulted `refresh_hz = 60` and nothing ever wrote it. Reading the real values
through `EnumDisplaySettingsW`/`DEVMODEW`:

    \.\DISPLAY5  ->  60 Hz
    \.\DISPLAY1  -> 144 Hz      <- the app has been calling this 60
    \.\DISPLAY4  ->  60 Hz

Not a structural nit: DISPLAY1 is a 144 Hz panel and every surface in the app said
otherwise. `refresh_hz` is not in `portal_signature`, so the correction restarts
nothing.

**What the adversarial pass caught after the suite was green.** The local
"Diagonal…" entry was labelled `(the one Windows cannot tell us)` while its
managed-display twin said `(restarts input ~8s)` — same handler, and a local
diagonal edit writes `layout_w/h`, which IS in `portal_signature`. So the one
entry with no cost warning was the one that killed input across three live lanes.
It shipped green because the cost-label test inspected only the target menu.

Also fixed from that pass: `merge_live_monitors` dropped `layout_w/h` for monitors
with no diagonal, silently resetting a 900x506 rectangle to 1920x1080 while
reporting "nothing changed"; `adopt()` left `_hover_item` stale and could TypeError
inside a Tk callback; the reload message fired even when the signature was
identical; and each right-click stranded two cascade Menu widgets forever.

**`test_pair_flow.py` had no `sys.exit`.** 59 checks that could never fail the
suite — so "all green" had never covered that file. Fixed; all 59 genuinely pass.

Suite 399 -> 505 checks across 12 files.

**Known, deliberately deferred:** `MacDisplayEditor` takes free-text `res_w`/`res_h`
with no per-kind validation, so a desktop resolution can still be typed onto an
iPad display through "Edit all screens…" — the exact hazard `_resolution_presets`
prevents on the right-click path. Pre-existing; belongs to a later wave.

### Pressed feedback — ttk's "active" is hover, not pressed

Doug, 2 August: *"when i click a button i need visual indication it has been
clicked on the button itself. it needs to react in some way, even in a pending
state while the action runs."*

One misreading of ttk ran through every button map in the file. **`active` is the
HOVER state.** Every map here specified only `("active", …)`, so pressing a button
changed nothing about the button. The only way to learn a click had registered was
to watch for a side effect somewhere else in the window — and 26 actions in this
app run on a worker thread and take seconds, so "hasn't happened yet" and "the
click missed" were indistinguishable. Exactly two buttons in the app (`vm_btn`)
had ad-hoc feedback, out of roughly fifty.

Two things are load-bearing and neither is obvious:

**Order.** ttk takes the FIRST matching state, and a held button is `pressed` AND
`active` at the same time. Listing `active` first means the press never shows.
`disabled` must precede both, or a disabled button can render as pressed.

**There are two TButton maps and the later one wins.** `_theme_widgets` sets one,
then replaces it a few lines down with the version carrying the disabled colours.
A `pressed` entry added only to the first is dead code — and it would test green
against `ttk.Style` if the test happened to inspect the other one. Both carry it
now, and `test_button_feedback.py` asserts EVERY map rather than the first found.

The pending/busy half of the request is deliberately NOT here. `_apply_device_rows`
re-enables the four per-device verbs on a 3-second poll tick, so a busy state set
on those would be stomped within 3s. It has to be co-designed with the wave that
rewrites that method, which is W3.

+27 checks.

### The screen outranks the content

W1 set `minsize` equal to the measured content so the packer could never again
drop the bottom panels in silence. Correct — until the desk changed under it.

Doug rebooted and came back on three 1080p panels where the primary had been
1440p. The same 1263px of content that fitted with 115px to spare was now 223px
taller than the display, and because `minsize` equalled the content, the window
**could not be shrunk to fit at all**. System control and Bluetooth radio were
unreachable by any means: too low to see, and the window refused to get shorter.

A window you cannot fit on your screen is worse than one whose overflow is
reported. So the rule gains an exception, in this order:

1. `minsize` follows the content — the starvation guard, unchanged.
2. Unless the physical screen is shorter, in which case the SCREEN wins.
3. And when the screen wins, the app says so, loudly, in the console.

`clipped` is that signal. It is the app's cue that the CONTENT has to get
shorter — W3 through W5 — not that the warning should be muted.

`work_area_height()` reads `SPI_GETWORKAREA` rather than `winfo_screenheight()`:
the raw screen height counts pixels the taskbar already owns, so sizing to it
puts the last panel under the clock. On this desk that is 1040, not 1080.

Measured live: `window_height_plan(1263) -> (1040, 1040, over=False, clipped=True)`.

Suite 532 -> 540.

### W3 — one row per device, a real pending state, and the suppressed register

Three changes in one wave because all three land in `_build_device_row` and
`_apply_device_rows`. Splitting them would have meant rewriting that code three
times.

**The cards.** Each device spent 66px on nine buttons to expose at most two live
actions, and five of those nine — Radio / Input / Rename / Displays / Remove —
appeared nowhere in `_apply_device_rows`: all fifteen across three cards were
permanently enabled whether or not the device even had a radio. They are pure
per-object property editors, so they moved to a right-click on the card, the
same shape the Bluetooth tree and (since W2) the arrangement canvas already use.
One ~33px row per card. **-99px.**

The four verbs stayed as visible, gated buttons. Collapsing them into one
relabelling button was refuted: the predicates yield THREE live verbs in the
paired-idle state, so there is no single correct verb, and a relabelling button
re-aims under the cursor every 3s with Unpair in the rotation.

**The pending state.** Doug: *"even in a pending state while the action runs."*
26 actions here run on a worker thread and take seconds while the button says
nothing. The trap: `_apply_device_rows` re-enables the verbs every 3s from
`_dev_state`, so a busy state painted on top is stomped within three seconds. It
had to become part of the state the poll tick READS. `inflight`/`broadcasting`
were not sufficient on their own — Disconnect and Unpair *clear* inflight (they
double as pair-cancel), and Unpair's `forget-hid` is a 25-second ssh, precisely
the wait being complained about. So `verb` was added as a presentation-only
field and the enable-gate left bit-for-bit unchanged.

**The suppressed register.** Doug: *"currently there is no visual difference
between a stopped portal and a paired but unconnected device."* He was exactly
right, and it was literal:

    colour, text = WARN, "portal off"
    colour, text = WARN, "paired"

The same amber for both, at two sites. The principle now implemented: **a stopped
portal is a global CAUSE; an unconnected device is a local STATE.** One alarm, at
the cause. When the portal is down every device colour drops to a suppressed
register — green and amber that read as *paused*, not as *dead grey* — and
full-strength amber survives in exactly one place: the Start portal button, which
becomes a `Warn.TButton`. Nothing else in the window may wear an alarm colour
while the portal explains everything.

**What the adversarial pass caught after the suite was green.** The largest
element in the window was untouched. `_apply_device_rows` passed
`set_target_state(device_id, live and portal_on, paired)` — collapsing `portal_on`
INTO `live` before the canvas could see it — and `IPAD_IDLE_LINE` is byte-for-byte
`WARN`. So the arrangement canvas still painted both states identically and now
CONTRADICTED the card two inches below. `_apply_poll` was collapsing the same way
a second time.

The fix was to stop having two truth tables rather than to widen both: the canvas
state token is now looked up FROM the colour `device_state_colour` returns, so a
new state is a `KeyError` at the moment it is added rather than a silent
divergence, and the suppressed canvas fills are derived from the same constants at
the ratios the existing fills already used.

Also fixed from that pass: a crashed pair worker left `inflight` set, so the button
read "Pairing…" for five minutes — a lying label where pre-W3 it merely disabled.
`clear_button_busy` re-enabled buttons it had never parked. Two verb-keyed dict
literals remained that would have raised `KeyError` inside the exception-swallowing
`_drain_ui` the moment a fifth verb was added. And `toggle_portal` blocked the UI
thread for ~8s in `_terminate_role_process` while reporting that it was doing so.

Suite 540 -> 714 checks across 14 files.

**Noted, not fixed:** `ArrangeCanvas` — the old single-iPad canvas, never
instantiated anywhere including tests — still carries its own `IPAD_IDLE_LINE`
painting with no portal awareness. The same divergent-second-table shape that
produced the fatal above. W5 deletes it. `_portal_changed` also still calls
`_terminate_role_process` on the UI thread.

### W4 — the app stops reporting a two-device world it left behind

`mac_st = None` was a hardcoded literal, never reassigned, threaded through
`_apply_poll` and read at four sites. So

    f"Mac {'● up' if mac_st is not None else '○ down'}"

could never be true. System control has been reporting the Managed Mac as DOWN
while it was connected, structurally, for as long as that code existed — and the
`mac` dot was permanently grey for the same reason. Meanwhile `_dev_status`
already held per-device daemon state for every device: the app outgrew the
two-device (iPad + Mac) model and the status rendering never followed.

Everything that read it now comes from `device_status_rollup()`, N devices wide.
The verifier proved the fix by execution rather than structure, driving the real
`_apply_poll` on the live 3-device shape: `sys_status` reports
`device daemons ● 2/3 answering`, and the Mac reads connected on every surface.

**Reachability moved onto the device.** `up = status is not None` was gate-only,
so a card printed "not paired" whether the device was genuinely unpaired or its
daemon was unreachable — two very different problems under one label, and the
only surface that distinguished them was a global line about a singleton that no
longer exists.

**Three fatals the adversarial pass caught after the suite was green:**

1. Deleting `c_ready` removed the app's ONLY readiness banner from the default
   view. Its supposed twin `ready_lbl` lived inside `consf`, and `consf.pack`
   appears exactly once in the file — inside `_toggle_console`'s else-branch,
   with `_console_open = False` at startup. It was never packed. The deletion
   also saved 0px, because the audio panel is in the right column, which does
   not bind height. One banner now, in the right column, always visible.
2. W4 killed the Mac half of the two-device model and left the iPad half, which
   is worse than dead — it was FALSE. `_ind["ipad"]` rendered a hardcoded device
   NAME over `any(paired for ALL devices)`, so on a three-device desk it
   contradicted the card three inches below it on the same tick. It now speaks
   for the first device under that device's own name and its own facts. The
   aggregate already has a home: the `devices N/M` token, which says so in words.
3. The one-fact-one-surface test was a duplicate-LITERAL detector: each marker
   was the exact f-string of the call it located, so it could only fail on a
   copy-paste, and three of its six facts already had three or four writers.

`_drain_ui` still swallows — a widget really can die under a queued closure at
shutdown — but the first faults now print a traceback. The cap bounds a burst
WITHIN one drain (`_emit` routes back through `ui()`, so an unbounded report of a
fault in the logger loops inside that same `while`), and a clean drain re-arms
it. Without the re-arm the counter was a lifetime cap and three benign faults
would permanently restore the silence it exists to break.

`isinstance(status, dict)`, not `is not None`: a daemon status is whatever
`json.loads` made of the socket bytes, and JSON's top level is legally a scalar.
One stray `5` short-circuits past `status and` straight into `5.get(...)`, at the
very top of `_apply_poll`, inside the closure `_drain_ui` swallows.

Suite 714 -> 795 checks across 16 files.

**Known red, and NOT from this wave.** `test_portal_invariants.py` fails two
checks against the live config after the desk was rearranged. Bisected: identical
failures on the W3 tree with none of W4's changes.

    mac/mac-2 pointer lands 1373 desk units from the model after a 2129-unit jump
    mac: nothing reached the wire

The new arrangement has a DIRECT crossing onto Mac Display 2 — the 90°-rotated
3840x2160 panel, 1569 x 2789 desk units. Every Mac crossing used to land on
mac-3. One HID report is worth ~190 target px on a compensated device, so a
~2100-unit jump onto that portrait panel is not fully paid in reports and the
model believes the pointer is about half a jump from where it is. Config-driven,
affects the shipped build too, and wants its own investigation rather than a
patch bundled here.

### The input-capture lease — coexisting with EsotericOS

EsotericOS is a macOS-interaction layer for Windows that hooks keyboard and mouse
for its own gestures. Two low-level hooks cannot see each other and Windows calls
them in reverse installation order, so whichever launched last wins — and Doug hit
it live: he crossed to his Mac and Alt+scroll zoomed *this* machine instead.

The contract, published by EsotericOS and implemented here:

    Local\EsotericOS.InputCaptureLease   -- a MUTEX, not an event

Held while OpenSpan owns input; released when it hands input back. A mutex rather
than an event because a mutex is **abandonment-detectable**: if the holder dies
without releasing, the next waiter gets `WAIT_ABANDONED` and reclaims it. An
abandoned event fails silently and permanently, which is the worst failure mode a
coexistence contract can have.

**The ship-blocker, and why the obvious implementation would have failed.**
Windows mutexes are THREAD-owned, and `ReleaseMutex` from a thread that did not
acquire returns FALSE while the mutex STAYS HELD. Capture here does not start and
end on one thread — `leave()` has seven call sites across at least three:

    2214  _kbd_proc         hook thread (the Esc x3 panic bail)
    1799  _route_motion     hook thread
    1871  _route_motion     hook thread
     921  _status_watcher   ITS OWN THREAD  <- a dropped lane, the killer case
     851  send              sender thread
     866  send              sender thread
    1046  _jump_nearest

So "acquire where capture starts, release where capture ends" would have left the
lease held by a healthy process that could no longer release it — same end state
as the abandoned event, by a different road, and *less* discoverable because
nothing died. The handle therefore lives as a LOCAL variable inside the lease
thread's `_run`, never an attribute, so no other thread can even name it;
`enter()`/`leave()` post requests to it.

That finding went back to EsotericOS and they promoted the dedicated-thread
pattern from a caveat to the default in their own spec.

**A failed release must not clear the held flag.** A mutex is re-entrant for its
owning thread, so setting `held = False` after a failed release lets the next
`enter()` take it a second time — after which one release can never let go.
`_give` stays held on failure, logs, and the next `leave()` retries. That is the
difference between self-healing and quietly permanently stuck, and it is invisible
in any single run.

**Clipboard.** The iPad relay carries passwords. Relay writes are now marked with
`Clipboard Viewer Ignore`, `ExcludeClipboardContentFromMonitorProcessing` and
`CanIncludeInClipboardHistory=0`, in one Open/Close pair with real GlobalAlloc
DWORDs. EsotericOS's history gate honours all three and fails closed, so relayed
clipboard traffic never enters it.

**Elevation is a stated consequence, not a bug.** EsotericOS runs `asInvoker` by
design and will not change — it hooks input, reads the clipboard and captures the
screen. OpenSpan must run elevated. So while an OpenSpan window has focus,
EsotericOS gestures do not fire, silently, because UIPI declines to deliver to a
non-elevated hook. On this machine UAC is disabled so every process carries the
elevated token and it does not arise; for other users the elevation gate's
"Ignore — normal mode for this run" is the documented escape.

**The three-link chain**, which came out of the exchange and belonged to neither
side. A unified cross-machine gesture needs: (1) not stolen locally — the lease;
(2) the modifier survives translation — the keymap; (3) the destination OS is
listening for the modifier that actually arrives. Link 3 has no owner and leaves
no trace: both products can behave perfectly and the user still sees nothing. Alt
reaches the Mac as Option (`modifier_remap: {}`, deliberate), and macOS zoom
listens for Control by default — so the gesture needs the Mac's own
Accessibility → Zoom modifier set to Option. No code on either side can fix it.

`docs/INTEROP.md` carries OpenSpan's half and cites EsotericOS's rather than
restating it, so the two documents cannot drift into contradiction.

Suite 795 -> 931 checks across 17 files.

### W7 — one pane at a time: the window is the tallest pane, not the sum

Doug: *"the app is still showing too much information at once i think — how can
we get this thing to be a reasonable size on 1080p? it demands too much —
consider InputDirector interface for ideas"*

Input Director's shape, and the reason it fits on a laptop: a narrow labelled
rail, ONE pane beside it, a small persistent header. The window's height is then
the tallest pane rather than the sum of every section. This window was the sum —
two columns, every panel packed at once, **1136 x 1054 measured on a 1040px work
area**. Under the rail, measured on the live config (3 devices, 3 monitors, 96
DPI):

| pane | pane px | window px | minsize px |
|---|---:|---:|---:|
| Desk | 552 | **725** | 725 |
| Devices | 157 | **520** (floored) | 520 |
| Bluetooth | 819 | **992** | 992 |
| System | 332 | **520** (floored) | 520 |
| Console | 385 | **558** | 558 |

The rail is 116px wide and 170px tall. The arrangement canvas is *better* off:
one column gives it 940px of width instead of 852, so `_fit_height` returns 493px
instead of 447 and the desk is drawn larger in a shorter window.

**The panes are built once and hidden with `pack_forget` — never destroyed,
never lazy.** Tk has no reparent operation, and two of them are service objects
as much as panels: `BtPanel._radios` gates the radio check on every device card,
`_connected_names` feeds the headphones line, `_poll` calls
`bt_panel.refresh(quiet=True)` on every fifth tick, and `MultiArrangeCanvas` owns
the desk config that six calls a tick read. A pane that existed only while you
were looking at it would take those with it. `test_panes.py` walks the tree on
every pane and proves both survive being hidden, then makes the tick's real
writes — including `set_ipad_state`, which reaches `redraw()` and `winfo_` — with
the pane hidden.

**The readiness banner had to leave the panes, and this is the part that would
have shipped broken.** `ready_lbl` lived in the Audio & status panel, which
becomes the Bluetooth pane — so under a rail it would vanish whenever any other
pane was up. That is the W4 fatal verbatim, one commit old: the banner was then
inside the console frame, which `_console_open = False` meant was constructed and
never mapped, so the default window could not say whether the bridge was up. It
now sits in the pinned header (`full`), assigned once, written by one caller.

**Height is re-derived on every switch, and minsize moves in both directions.**
`_rederive_height` re-runs the W1 budget against the pane on show: settle the
packer, re-fit the canvas if the desk is up, measure `full`, `pane_window_plan`,
apply. **minsize before geometry** — Tk clamps `geometry()` to whatever minsize
is in force, so lowering the window without lowering the floor first is a silent
no-op and the window stays stuck at the tall pane's height. The same order was
wrong at the end of `App.__init__` (the provisional `minsize(940, 680)` is taller
than a short pane) and was swapped there too.

**A floor, and it is not a re-introduced guess.** `FrameModal._fit` clamps its
card to `host.winfo_height() - 40`, and the tallest dialog in the file
(`MacDisplayEditor`) asks for 900x420. The Devices pane is the shortest in the
app *and* its own card menu is what opens that dialog — so without a floor,
selecting the smallest pane would cut the buttons off the biggest modal it can
raise. `PANE_MIN_WINDOW_H = 520`, never taller than the screen.

**The console stopped being a width problem.** It was a fixed 390px strip pinned
to the right edge; opening it widened the whole window 1120 → 1520, with a
`<Configure>` handler to re-apply that width whenever the window left a maximized
state because Tk ignores `geometry()` while zoomed. `_set_win_width`,
`App._on_configure`, `_was_zoomed`, `_console_open`, `_cons_anchor` and both
width literals are gone. The title-bar button selects the pane and a second press
returns to where you were.

**Persisted.** `last_pane` in `openspan_settings.json`, written only on a
deliberate switch. An unknown, missing or corrupt value falls back to `desk`
rather than raising — this runs inside `App.__init__`, before there is any
surface to report a fault on.

The rail labels every item in words, not a glyph alone: this app is opened rarely
enough that a bare icon is a memory test every time.

Left standing, and named rather than fixed: the Bluetooth pane at 992px is now
the app's height ceiling, and it is ~85% BtPanel. Its `self.out` Text (height=5,
72px) is a second console, and the app has a console pane now — but removing it
is a behaviour change nobody asked for.

Suite 931 -> 1032 checks across 18 files (`test_panes.py`, 101 checks). The one
standing red is the config-state check in `test_sensitivity_notches.py`, unchanged
and not a code fault.

### W7 — a nav rail, one pane at a time

Doug: *"the app is still showing too much information at once i think -- how can
we get this thing to be a reasonable size on 1080p? it demands too much --
consider InputDirector interface for ideas"*

Input Director's shape: a narrow left rail, one pane visible at a time, a small
persistent header. The consequence is the point — **window height becomes the
tallest pane rather than the sum of every section.** W5's ~1006px floor was still
summing; this does not.

    devices  520    system  520    console  558    desk  725    bluetooth  992

against a 1040px work area, from a fixed 1054. Last pane is remembered across
restarts; the pinned header is the token row, the status line and the readiness
banner, and nothing else.

**Panes are built once and hidden with pack_forget, never destroyed.** BtPanel is
a service object as much as a panel — `_radios` feeds the device-row radio gate,
`_connected_names` feeds the headphones line, and `_poll` calls
`bt_panel.refresh()` every fifth tick whether or not it is on screen. The canvas
is the same: it owns the config, and `set_target_state` runs every tick. Hiding
is free; destroying would break the poll, which is why the sweep refuted
"dissolve the Bluetooth column" while this wave is safe.

**The readiness banner moved to the header.** It lived in the audio panel, which
became the Bluetooth pane — under a rail it would have vanished on four panes out
of five. That is exactly the W4 fatal (no readiness surface in the default view,
because `ready_lbl` sat inside the never-packed console frame) and it would have
been reintroduced one commit after it was fixed.

**The header starves, and the casualty was the one that matters.** At the app's
940px minimum the honest token row wants ~932px against a 908px cavity, and Tk's
packer does not shrink overflow — it drops the last-packed child. That was
`admin`: per `is_elevated`'s docstring the ONLY surface in this app that explains
a silently dead mouse under UIPI. Fixed by ORDER, not by widening minsize —
`INDICATOR_ORDER` puts the non-negotiables first and the widest, most transient
token (`bcast`) last, and `broadcast_names()` collapses to a count past two
devices. Measured: admin PLACED at 940px, bcast the only casualty.

The row is deliberately NOT required to fit whole. Widening minsize to close a
24px gap would trade a real constraint — the window must fit a 1080p panel — for
a cosmetic one. What is asserted instead is the ordering invariant: overflow is
permitted, but only a yieldable token may be cut.

**The test that proved nothing.** The starvation check built its own probe row
from `INDICATOR_ORDER` and then asserted properties of `INDICATOR_ORDER` — a
closed loop. Reverting the shipped `for _k in INDICATOR_ORDER:` back to the old
literal tuple, with the constant and its rationale left untouched, made the whole
file report ALL PASS while the header dropped the lamp again. Found by mutation,
not by reading. There is now an AST check binding the loop to the constant.

Also fixed: a bare `except Exception: pass` re-introduced on the height path by
the same wave that deleted one from `_apply_poll`; another still wrapping the
readiness banner's own update inside `_apply_poll`; the console pane unable to
grow (the one pane where vertical growth is the entire point); `_prev_pane` going
stale when the console was reached from the rail; and rail hover painting the
same value as rail selection.

**`Ctrl+Alt+Q` released.** It was `return 1` and nothing else, guarded on
`not self.active` — so it fired only when there was nothing to bail out of and
fell through when capture was live, while the module docstring called it a backup
panic exit. EsotericOS moved its Quick Actions off that chord to avoid colliding
with us; holding it hostage to a stub was not reasonable. The test that asserted
the stub existed now asserts the stronger claim — that OpenSpan does not take the
chord at all — which cannot be satisfied by re-adding a better-guarded stub.

Suite 931 -> 1111 checks across 18 files.

### A second portal button, on the Desk

Doug: *"Duplicate start portal button linked to same backend and place floating
in field of Desk at bottom"*

W7 put the Start portal button in the System pane, so from the Desk pane — where
he spends most of his time — the control was two clicks away and the amber alarm
explaining why nothing is bridging was on a pane he could not see.

It floats over the canvas via `place()`, so it costs the pane **zero height**:
measured 517px with and without. The canvas is aspect-fit, so the strip below the
drawing is structurally empty — the button sits in it with 61px of clearance
above the lowest screen rectangle at this desk's geometry.

**"Linked to same backend" is the whole engineering problem.** A duplicated
control surface already shipped in this app and broke, because each surface kept
its own idea of state. So: one builder (`_portal_button`), one registry
(`_portal_btns`), one writer (`_render_portal_button`, which already called
itself "the ONE writer"), one command, and one busy helper (`_busy_portal`) that
parks the wait across every registered button and returns a single restore. The
guard is now `any(button_is_busy(b) for b in ...)` — a half-busy pair, one saying
"Stopping portal…" while the other says "Stop portal", IS the bug.

**Both tests protecting this were rubber stamps, and mutation proved it.**

The geometry section re-typed the button's placement into the test file and then
measured that. Changing the SHIPPED `rely=1.0` to `0.70` put the button over
three screen rectangles and turned +63px of clearance into −85px, and every check
still passed — including the one titled *"it clears the bottom-left hint line"*.
Same escape for `width=64` and for `anchor="sw"`. The kwargs are now lifted out
of `App.__init__` by AST, so the probe moves when the button moves.

The one-writer proof only recognised `config(text=...)` containing the literal
"Stop portal". A writer touching only the STYLE, or only the resting label,
walked past it — and style is half of what the renderer owns. Both mutants now
fail the suite.

That is three separate occasions in this codebase where a test passed while the
thing it existed to protect was broken. The pattern each time: the test asserted
a property of its own copy of the subject rather than binding to the shipped one.
Mutation is the only thing that has caught it — reading never has.

Suite 1111 -> 1157 checks across 18 files.

### Feel follows the device; verbs on the canvas menu

**Backlog 1.** `MACHINE_FIELDS = ("radio", "port")` were the only per-device
fields a profile did not carry, so switching arrangement reverted sensitivity,
acceleration, scroll direction, modifier remapping — everything about how a
machine feels. Observed live: three devices tuned to 0.75, one arrangement
switch, all three back to 0.686 / 0.747 / 1.0.

All 14 fields in a device record are now classified against one question: *if
the desk were rearranged and nothing else, would this value now be wrong?*
Twelve are device-scoped, `displays` is the arrangement, `id` is the join key.

`pointer_gain` was the one worth arguing. The DEVLOG records it as model-side,
which tempts a reading that it belongs to the desk. But model-side vs wire-side
decides which SYMPTOM a wrong value produces, not who owns it: the arrangement
is already represented by `display["w"]` in `gain * display["w"] / res_w`, so
the two are orthogonal. Gain calibrates that machine's window server. Device.

`load_profile` now DELETES a device field the live desk has no device for,
rather than leaving what the file held — otherwise a stale value leaks back
through an orphan record.

**Backlog 3.** The four verbs are on the canvas right-click menu, generated from
`DEVICE_VERB_SPEC` and gated by `DEVICE_VERB_GATES` — the same predicates the
cards use, one path. The count is not two: paired-idle offers THREE, because
`pair` is not gated on "not paired" and re-pairing is how a bad bond is
recovered. The device is named in the verb section, since the menu is per-display
and a Mac has three.

**The suite was writing his live files.** `test_pair_flow.py` builds a real App
and calls `canvas.save()` six times with no redirect, and `save()` writes
through to whichever arrangement is active. Running the suite under the new
strip rule stripped every device field out of `Mac 2k.json` — his ACTIVE profile
— while the running exe still expected them. Repaired from the live config, and
the test now redirects CONFIG / PROFILE_DIR / BT_PREFS to a scratch copy before
App is constructed. The suite run is now hash-verified not to touch either file.

Suite 1157 -> 1220 checks across 19 files, all green.

### A "⋯" on the card, because a right-click advertises itself to nobody

Doug: *"put a '...' floating next to the Pair button for all devices -- this
will make it obvious there are other options to be had"*

When the card collapsed to one row, five per-object editors (Rename, Radio,
Input, Displays, Remove) moved onto a right-click. That was the correct place
for them — they are not lane actions — but discoverability was the known cost,
recorded at the time and not paid. BtPanel solves the same problem in prose
("right-click a device for actions"); a one-row card has no space for a
sentence, so it gets a glyph.

It sits immediately left of Pair: `side="right"` allocates first-packed
rightmost and the verb loop runs the spec reversed, so packing the button LAST
lands it just inside Pair, where the eye arrives before reading the verbs.

Never disabled, unlike the verbs. There is no device state in which all five
editors are meaningless, and a greyed-out hint advertises nothing.

**One poster, not two.** `_device_card_menu` and the button both call
`_post_card_menu`; the button supplies its own screen coordinates rather than a
synthetic event. A second posting path would be a second place for the grab to
leak, and a `tk_popup` that raises while holding it leaves the window
mouse-dead.

The test that asserted `count("ttk.Button") == 1` in the builder was standing in
for "no per-object editor is a permanent button" — which is already asserted per
editor by name. Replaced with what it meant, rather than bumping the number.

Suite 1220 -> 1223 across 19 files.

### W8 — the menu's advertised waits are the wait, not one component of it

The verb menu labels each disruptive action with how long it takes. Three of the
four named a single call inside the work instead of the work:

    unpair    "a ~25s guest command"   forget-hid IS a 25s ssh -- but
                                       set_target_advertising (8s) and a
                                       disconnect (2s) run first. Wait: ~35s.
    pair      "up to ~1 min"           the ssh alone is 55s, and four
    connect   "up to ~1 min"           set_target_advertising calls at 8s
                                       apiece sit on the various exits. ~87s.
    disconnect "up to ~10s"            correct.

A number that describes part of the wait is worse than no number: it is precise,
and wrong in the direction that makes you think the app has hung.

`test_device_verbs.py` now re-derives every label from the code's own
`timeout=` literals and fails if a label is under the worst case its own path
allows — or more than double it. Writing that check immediately caught the
number in this very commit: I had counted two `set_target_advertising` calls on
the pair path and there are four, so my first correction said ~70s against an
87s path. The labels now read ~90s / ~90s / ~10s / ~35s.

The VM-start retry loop inside `_pair_device_attempt` — 45 × (5s probe + 2s
wait) ≈ 315s — is deliberately EXCLUDED, and the exclusion is in the test rather
than left as a silent omission: pair's gate requires `f["vm"]` and connect's
requires `f["up"]`, so neither verb is offered unless the VM is already
answering. Folding it in would advertise five minutes for a wait that cannot
happen.

The old assertion `"25s" in live_rows["unpair"]` was pinning the component
number, so the test certified the understatement. It now only asserts a wait is
advertised; section (g) checks the value.

Suite 1223 -> 1236 across 19 files.

### Iteration 3 — the day the radios taught us everything (2026-08-08/09)

Two blue screens opened the day; a tagged release closed it. The whole arc is
preserved in the recovery indexes (`D:\OpenSpan-preservation\2026-08-08*`), but
the engineering lessons live here.

The wedge that caused it all: VirtualBox arms a capture, tears down the
Windows device stack, and the re-add as a VBox proxy silently never runs. The
device becomes a PnP phantom — electrically present, unreachable by software,
"Captured" forever. The BSODs (0xCA) were churn against that state, not
steady-state passthrough: the stable week's own logs show 12/12 clean attaches
until the first mass-release storm at a cold-backup poweroff injured the tree
— timestamped eight seconds after the release, misread twice before the logs
settled it. dwErr=31 descriptor noise turned out to mean "Windows owns this
radio", not damage; a warm reboot just re-rolls which radios wedge; ONE
physical replug (a fresh arrival = a new device object) completes every
pending capture at once.

What landed as iteration-3 (tag on 8a3f38c, exe E03207FD…): the six-commit
integration (fail-closed USB ownership with a per-run attach blocklist,
stuck-input release keyed to the owning socket, templated lane provisioning
behind a refuse-legacy guard, per-device volume that rejects ambiguous sink
evidence), plus stable radio identity — lanes keyed by controller MAC,
resolved at every service start, proven the same night by an enumeration
reshuffle that would previously have crossed every lane — plus hardware-
identity radio labels (a controller list must never wear a lane's name), a
preflight that demands three spaced probe failures before its global remedy
(and probes with busctl, because a python interpreter's cold-start can eat a
6-second window all by itself), and an agent unit that dies by SIGKILL in
under 5s instead of parking systemd's job queue for 90 (the entire "45-second
lane timeout" mystery, twice).

Carried forward, eyes open: VM restarts still race the capture path (the
recovery cycle makes the app perform and verify its own handoffs); lanes
resolve their MAC only at start (event-driven re-resolution is filed with the
peer-identity work); audio dies without a watchdog when its radio hosts HID
churn; and the Cold-restart button is honest about the guest while lying
about the hardware — it cold-boots the kernel and mass-releases the radios,
which is precisely backwards, and its redesign is specified.

## 2026-08-16 — Radio custody: stop running the race, own the radio

**Reported.** The onboard Intel radio (`8087:0aaa`, port 14) was lost again.
Windows had the node registered but not enumerated — `present = False` — with
service `BTHUSB` and driver `Intel(R) Wireless Bluetooth(R)`; `VBoxManage list
usbhost` said `Unavailable`. The two TP-Link dongles (`2357:0604`) were
`Captured` and fine. Same shape as 2026-08-08/09.

**Cause, restated.** Every VM start, `VBoxUSBMon.sys` tears the live `bthusb`
device stack down and re-adds the device so PnP sees `USB\VID_80EE&PID_CAFE`,
which is the hardware id `VBoxUSB.inf` claims — that is how the proxy gets
bound. It is a race against a device Windows is actively using, and it is
re-run at **every** VM start. When it loses there is no proxy and no
enumeration, and on a built-in radio there is no replug.

**Design (Doug: "exclusively taking control of this from Windows until the
program is uninstalled").** Stop running the race. Bind `VBoxUSB.sys` as the
function driver of the radio's REAL device node, permanently — the Device
Manager Have Disk install done programmatically — so PnP loads the proxy at
boot and `bthusb` never touches the radio. Return = uninstall the node and
rescan, and the vendor driver comes back.

The obstacle is a class filter, and it is documented: a driver list built for a
device *"is composed of drivers that have the same class as the device instance
with which they are associated"*. The radio's class is Bluetooth; `VBoxUSB.inf`
is class USB. Probed here: `SPDIT_COMPATDRIVER` → 0 nodes (hardware ids differ,
expected); `SPDIT_CLASSDRIVER` on the device → 0 nodes even with
`DI_FLAGSEX_ALLOWEXCLUDEDDRVS`; the same call at SET level on a USB-class set →
1 node, `VirtualBox USB Driver` / Oracle / 7.2.12.24389. So the class is set
first with `SetupDiSetDeviceRegistryProperty(SPDRP_CLASSGUID)`, then the
device's own list is built, selected, and installed with `DiInstallDevice`.
Full sequence, citations and the unverified part in `docs\RADIO-CUSTODY.md`.

**The correction that cost the first version.** "Real node `present = False`
means phantom" is WRONG. A healthy dongle under runtime capture reports
`present = False` on its real node too, because that node was torn down and a
`USB\VID_80EE&PID_CAFE\<same suffix>` proxy stands in its place. On 2026-08-16
all three radios read `present = False` and two were working. What separates a
working capture from a wedge is VirtualBox's own host state — `Captured` versus
`Unavailable` — not the node. The verdict table now uses both, plus whether the
proxy exists at all.

**Also learned, from the proxy node.** The torn-down real node answers nothing,
but its proxy answers `SPDRP_REMOVAL_POLICY`: 1 (ExpectNoRemoval) for the
built-in Intel, 3 (surprise removal) for the dongles. That is a deterministic
way to tell "unplug it" from "restart Windows", instead of guessing from the
serial.

**Shipped.** `win\radio_custody.py` (audit / take / return, dry run by default,
pure decision functions separated from a fakeable `Win32Bindings`, `--json`);
the Bluetooth panel's custody line per radio and one **Take custody** button —
first click plans in the console, second click applies, both on a worker,
never a dialog; a launch audit that only reports, which doubles as the Windows
Update re-bind guard; `bake-in.ps1 -Custody` / `-Undo` printing the take and
return commands and never passing `--apply` itself.

**Refusals, because this is a driver binding.** Not elevated; `VBoxUSB.inf` or
`.cat` missing; the node absent; the node a phantom; and — the one this
codebase had to learn twice — **VirtualBox holding a live runtime capture on
that radio**. Rewriting the real node's driver under a live capture is the
layered ownership that wedged this stack before.

**The state changed under us, and that is worth recording — correctly.** Between
the first read of this session and the last, the Intel radio went from
`Unavailable` (proxy node present and OK, VirtualBox unable to open it) to
`Captured`, attached to the VM. It did NOT recover on its own: at 14:4x the
main session, on Doug's "do it", ran exactly one
`pnputil /restart-device "USB\VID_80EE&PID_CAFE\5&3B2D9A0D&0&14"` on the
**proxy** node — the ledger's rule that a restart works only while a node is not
phantom, applied to the node that IS present under a runtime capture. Port 14
was `Captured` 8 s later and the iPad lane's daemon answered within a minute.
Doug's earlier physical replug of a TP-Link had NOT un-stuck it this time. So:
the software replug of the proxy node is the sanctioned single kick for a
built-in radio, before any talk of restarting Windows. The wedge is
intermittent, and the audit reports what is true now, not what a brief said.

**Not verified, deliberately.** `SPDRP_CLASSGUID` appears on its MSDN page both
as settable ("*DeviceInfoData.ClassGuid* is set upon return to the new class")
and in the reserved list. Whether 22H2 honours the write is untested, because
testing it is a state change and this was code + dry-run only. `--apply` stops
with the class change named as the failing step if it is refused; the fallback
is the wizard's own route, `DIF_SELECTDEVICE` / `SetupDiSelectDevice`, which
sets the class as part of selection.

**The rule.** Prove the sequence on a TP-Link dongle FIRST, then the Intel. A
bad bind on a dongle costs a replug. A bad bind on the built-in radio costs a
Windows restart — and that restart must be taken with **no VM captures held**,
or it simply re-runs the boot-time race that caused all of this.

### 15:30 — the live test, in Doug's authorized window: the rule earned its keep

Test window 1–5 (Doug: "1-5"). Entry 1: swap to 7aab8cb7, VM warm, portal
restored itself. Entry 3, TP-Link first, on the Managed Laptop's dongle
(`3C6AD23CD44E`, port 3):

* App tree closed by path; `win\vm_off_gently.py` (new — the app's ordered
  handback + `acpipowerbutton`, from the command line) released all three
  radios one at a time and the guest powered off cleanly.
* **At VM-off the Intel wedged on the RELEASE side**: proxy Started, real node
  Stopped, VirtualBox `Unavailable` — the morning's shape again. Both TP-Links
  came back to `bthusb` normally. New data point: the race runs both ways.
* **Audit bug found and fixed**: VirtualBox cannot read a serial from a device
  Windows owns, so both dongles arrived serial-less and `match_instance_id`
  gave both rows the first twin. Tie now broken by hub port (`SPDRP_ADDRESS`
  == VirtualBox's `Port:`), and a node is never handed out twice.
* `take --apply`: **all six calls succeeded**, `DiInstallDevice ok=True
  needReboot=False`. So `SPDRP_CLASSGUID` IS settable on 22H2 — the "not
  verified" paragraph above is now verified. Real node → `VBoxUSB`, class USB,
  Started; audit read ESOTERICOS-CUSTODY. VirtualBox's host state for it read
  **`Busy`** — not its own device.
* App relaunched → VM start → **VirtualBox tore the custody node down and
  re-added `USB\Vid_80EE&Pid_CAFE\3C6AD23CD44E` anyway.** Real node
  Disconnected, proxy Started, attach address is the proxy's. The dongle works
  (VM holds it, daemon answered), but the design's "no teardown" claim is
  **refuted**: `VBoxUSBMon` captures by cycling the port and swapping the id at
  re-enumeration; it does not care what driver the real node had. What
  custody does deliver is narrower and true: `bthusb` never binds it again.
* Therefore **not applied to the Intel** (entry 4 abandoned as designed; no
  proven benefit, real recovery cost) and the reboot skipped. The Intel was
  recovered with the §6 proxy-node kick, VM running: `Captured` in 10 s,
  daemon up inside a minute — second time that recovery has held.
* Texts corrected the same hour: the custody line and the CUSTODY verdict no
  longer say the race is stopped; `plan_text` no longer prints "not run" on an
  apply; `docs\RADIO-CUSTODY.md` §8 has the full record.

### 16:00 — Doug overrules: "I need maximal control of all 3 radios"

I had abandoned the Intel take on my own reading ("no proven benefit"). Doug:
"You're not going to determine what i need and what i don't … I need maximal
control of all 3 radios. you think i purchased two extra because i want to let
the one on my MB rot?" — and, on the follow-up, "Don't overdesign something.
Simplicity is what drives this project from vibe-coded mess to viable product."
So: the simple bind, on all three, now; the USB/IP / host-owner ideas come off
the table.

Run: app down → `vm_off_gently.py` (3 released, clean poweroff) →
`take ACA7F1299FCB --apply` (Mac dongle, 6/6) → Intel had wedged on release
again (proxy Started, real Stopped, `Unavailable`) → **one
`pnputil /restart-device` on the proxy WITH THE VM OFF** → proxy Stopped, real
node Started under `oem98.inf`, VBox `Busy` → `take 5&3B2D9A0D&0&14 --apply`
(6/6, `needReboot=False`). Audit: **all three ESOTERICOS-CUSTODY**, and
`pnputil /enum-devices /class Bluetooth /connected` lists **no USB radio at
all** — Windows no longer has a Bluetooth adapter. App relaunched → VM took all
three on the first start (Intel via its proxy, no kick needed) → daemon up,
portal on.

What is now known: the bind persists across VM cycles (the Laptop dongle came
back to `VBoxUSB`, present, after its second cycle); the proxy kick works with
the VM off as well as on; `bthusb` is out of the picture on all three. What is
still to be shown: that the wedge rate actually drops with `VBoxUSB` holding
the node through VirtualBox's teardown — that is a matter of watching VM starts
over the coming days, and the audit reports it. Boot persistence is ordinary
registry binding; the restart that proves it is Doug's to take, with the VM off
and no captures held.

---

## 2026-08-16 - M10 begins: LAN nodes

Doug re-centred the project. Verbatim: EsotericOS "needs to be able to be placed
on my other laptop and then effortlessly expand the surface -- not through
bluetooth this time, but through the LAN Input Director style with increased
capability. The bluetooth keyboard/mouse solutions are for managed systems that
resist program installation."

That sentence demotes the entire Bluetooth stack from *the product* to *one lane
among several*, and it is right. BLE HID exists here because an iPad and a
managed Mac will not let you install anything. A Windows laptop Doug owns has no
such constraint, and paying the BLE tax on it -- relative-only pointer motion, a
15-30 ms connection interval shared with audio, a whole Debian VM -- buys
nothing. A LAN node is a device whose lane is TCP to a peer EsotericOS.

Two rows landed today: the foundation only.

### v3.120 - portable install

`install\make-portable.ps1` assembles `EsotericOS-portable\`: the exe,
`bake-in.ps1`, `swap-build.ps1`, `brand\`, `LICENSE`, `README-PORTABLE.md`. It
prints a manifest with sizes and SHA-256 prefixes, and `-Zip` produces the
archive beside it.

**It assembles from an allowlist, not by copying and pruning.** Copying the
folder and deleting the secrets afterwards is one forgotten pattern away from
shipping a private SSH key, and the failure is silent. Nothing lands in the
output that is not named. `win\test_portable.py` then does the thing that
actually proves it: it RUNS the script against this repo -- which really does
contain `id_openspan`, `openspan_config.json` and a live clipboard token -- and
searches the produced folder for every one of them by name, plus asserts the
repo held at least three of them so the pass cannot be vacuous.

**LAN-only mode.** The app assumed VirtualBox and the VM existed. On a second
laptop neither does, and what that looked like was "Booting the bridge... (~90s)"
forever, with a VBoxManage process spawned every three seconds to re-learn the
same thing. One predicate, `bridge_available()`, now answers it: VBoxManage found
(probed -- an override, VirtualBox's own installer variable, three Program Files
layouts, then PATH, each checked against the filesystem) **and** the configured VM
registered. Absent, the app runs as a LAN node: the banner reads "LAN node -- no
bridge here", the VM/Bluetooth/audio controls carry one secondary-text line --
*no bridge on this node - Bluetooth lanes need VirtualBox + the guest VM; LAN
lanes do not* -- the VM buttons are disabled rather than hidden, `status.json`
reports `vm: "none"`, and nothing polls a VM that is not there.

`"none"` is deliberately not `"down"`. Down means a bridge that is stopped. None
means there is no bridge to stop. A shell rendering "down" for a LAN node tells
its user something is broken when nothing is.

### v3.121 - discovery + pairing

`win\lan_nodes.py`. The whole row is organised around one instruction Doug gave
twice, the second time flatly: *"i am not interested in hardcoding ANYthing --
this should be a seamless experience on any device."*

The first draft had a beacon on UDP 9957 and a pairing listener on TCP 9958,
with a paragraph justifying the numbers. Both are gone.

**The service port is the OS's.** `bind(("", 0))`, read it back, advertise it.
Nothing connects to a number in the source. A second EsotericOS on one machine,
a machine already using our "favourite" port, a firewall pinholing one number --
none of them are cases to handle, because there is no number to collide with.
It changes every launch and that is the design, not a tolerance.

**Discovery is the OS's.** Windows 10 1809+ exports DNS-SD from `dnsapi.dll`;
we register `_esotericos._tcp.local` with the assigned port in SRV and the node
id / name / version in TXT, and browse the same type. That is the protocol
Bonjour speaks, so a Mac or an iPad can find a node later without inventing
anything. Where the API is missing, a minimal RFC 6762 responder on
224.0.0.251:5353 takes over -- a standard's number, not ours. The Console names
which path is live.

Two real bugs came out of driving the ctypes edge against the live API rather
than reasoning about it:

* `DnsServiceRegister` returned **9506**, which the first version treated as an
  error. 9506 is `DNS_REQUEST_PENDING` -- the asynchronous success path, like
  `ERROR_IO_PENDING`. The edge was reporting "DNS-SD not available" and falling
  back to mDNS on a machine where the API works perfectly.
* `deregister()` returned False. `_structs()` rebuilt the ctypes classes on
  every call, and ctypes compares types by identity, so assigning the pointer we
  registered with into a request typed against the *second* class raised
  TypeError. The advertisement outlived the process: peers went on seeing a
  machine that was no longer listening. Structs are built once and cached, and
  the de-registration now waits for its completion callback before freeing the
  instance rather than freeing memory the OS is still reading.

Both verified live on this box: register, browse, self-discovery at the correct
ephemeral port with the correct TXT, deregister -- all OK.

**Identity is 32 random bytes.** Not a hostname, not a MAC, not a disk id. A
derived id makes two clones one node, and changes when the user renames their
PC. The name is a *label*, defaulting to the machine name read at runtime and
editable after; renaming never re-pairs. Addresses are learned from whoever sent
the packet -- a node stating its own IP would be stating the one fact it is
least qualified to know, and would be wrong on every multi-homed machine.

**Pairing.** Each side sends a fresh 32-byte nonce over TCP. Both compute
`HMAC-SHA256(nonce_a||nonce_b, key = node ids sorted)[:3]` as six digits, sorted
by node id so initiator and responder -- who disagree about who dialled -- derive
the same number. Both screens show it; **both** desks must press "Same code"
inside 60 s. One side alone pairs nobody, and there is a test that presses one
button and asserts neither `peers.json` moved.

The long-term secret is domain-separated from the code. The code is three bytes
of a digest over those nonces and it is displayed on two screens and said out
loud across a room; if the secret were the rest of that digest, showing the code
would leak key material. Different message, same inputs.

**The firewall is a program rule.** Doug: no "run this netsh line". `bake-in.ps1`
-- run elevated once, which *is* the consent -- adds an in+out allow rule for
`EsotericOS.exe` on the private profile, idempotently, and prints what it added;
`-Undo` removes it. At runtime a blocked bind or an unreachable peer surfaces one
in-window action, "Allow EsotericOS through the firewall", which runs the same
rule on the click and never behind anyone's back. It has to be a program rule:
the service port is the OS's and differs every launch, so a port rule would be
stale by the next restart.

### The zero-display trap, and the decision

A paired node becomes a device (`kind: "node"`, `lane: "lan"`, `node_id` = the
peer's key). The question was whether to give it zero displays or a placeholder,
and the answer was not aesthetic. `normalize_config` contains `if not displays:
continue` -- a device with an empty display list is **silently deleted on the
next load**. A zero-display node would pair, appear to work, and quietly undo
itself with nothing said.

So it carries one placeholder rectangle named after the node, flagged
`placeholder: True` so v3.124 knows it may throw it away when the peer's real
screens arrive. Every existing invariant holds -- unique display ids, port
allocation, portal geometry, canvas hit-testing -- and not one existing test
moved. There is a test that asserts the trap is real, by loading a zero-display
node device and watching it vanish.

`normalize_config` builds devices from a whitelist, so `kind`, `lane` and
`node_id` had to be added to it or they would have been dropped on every load --
the same silent-discard shape the top-level settings hit two waves ago.

### What is not verified

**Two real machines.** There is one PC here. Everything below that is covered:
two `NodeService` objects in one process pair end to end over loopback through
the real TCP edge -- real nonces on a real socket, both codes derived and
compared, one-side-confirms proven insufficient, signed messages accepted and
tampered ones denied. The OS DNS-SD edge is verified live against `dnsapi.dll`.
What has not happened is a second Windows machine finding this one across a
switch, and no test in this suite can claim it.

Also unverified: the mDNS fallback on a Windows old enough to need it. Its wire
format is tested both directions, including a self-referential compression
pointer that would otherwise hang a receive thread, but the code path that
*selects* it has not run on a machine that requires it.

Suite: 58 files, all green.
