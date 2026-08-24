# THE MAP — EsotericOS

> **What this is for.** When Doug asks *"can we implement X"*, the answer should be a lookup,
> not an investigation. Find X in **THE SUBJECTS**, walk **THE STACK**, and the highest layer
> that already answers is where the work goes.
>
> **Status: v1 skeleton, drafted 2026-08-23** against board rev 27 (`docs/plan/plan.json`,
> 153 items) and the live tree. Counts cited here were looked up, not remembered. The `▸`
> lines are Doug's to answer.

> ### Nothing is finished.
> Do not ask which subjects are done — ask what is **live**, what is **dormant**, and what is
> **blocked**. A dormant thing is a thing waiting for a dependency, and the map's job is to
> name the dependency.

---

## THE STACK — what a behaviour question walks through

Resolution order for the app (from `docs/forge/router.json`): highest wins; a layer is
invisible if one above it already answered.

| # | Layer | File | Note |
|---|---|---|---|
| L5 | Runtime state | `status.json` | What IS, written every paint tick; never persisted |
| L4 | Module settings | `module_settings.json` | Written by the settings UI or modules |
| L3 | User preferences | `openspan_settings.json` | Hotkeys, tiling, audio balance |
| L2 | Machine config | `openspan_config.json` | VM name, radio IDs, display layout |
| L1 | Code defaults | inline in `win/` sources | Lowest priority |

Above the app sits the machine itself: the **shell** (Explorer today; EsotericOS Shell when
Doug swaps it), **elevation** (the GUI is High-integrity by launch invariant), and the
**VM** (Debian 12, owns the Bluetooth radios). A change should be placed at the highest layer
that can actually enforce it — a config edit before a code change, a code change before a
shell change, a shell change before a Windows-contract change.

## THE TREES — who owns what

| Tree | What | Mode |
|---|---|---|
| `D:\_EsotericOS\app` | the bridge: Python/Tk control app + Debian VM (BLE HID + audio + clipboard). Git, branch `multidevice`. Board lives at `docs\plan\plan.json` | read/write |
| `D:\_EsotericOS\shell` | EsotericOS Shell — Cairo Desktop fork, C#/WPF, GPL-3.0-or-later. Private; `no_push`. `shell\stable\` is never touched by a build | read/write, guarded |
| `D:\_EsotericOS\managedshell` | ManagedShell fork (shell plumbing library) | read/write |
| `D:\_EsotericOS\legacy-csharp` | previous C# program | reference only |
| `D:\_EsotericOS\preservation` | binaries ledger, cold backups | read; `cold-backups` forbidden |
| `E:\esoteric-path-core\forge` | the Forge (Keeper's) | read-only; consult, never edit |

## THE SUBJECTS

State is from board rev 27. `[tag]` is the board item tag; find it in `plan.json` for deps,
estimates and history.

### Input bridge — keyboard/mouse to iPad and Mac
**Live.** BLE HID via the VM (`guest/openspan_ble.py`, ports 9955 iPad / 9956 Mac), edge
crossing via Win32 hooks (`win/openspan_portal.py`), keymap remaps, Esc×3 panic bail.
Position model in `POSITION_MODEL.md`. Known limit: relative motion drifts; corner-park
re-sync is planned, dormant.

### Audio — PC to Bluetooth earbuds
**Live.** VB-Cable → WASAPI loopback → UDP 4010 → PipeWire → A2DP (`win/win_audio_send.py`,
`guest/udp_to_sink.py`). Volume + L/R balance in app.

### Radios — custody and isolation
**Live, under watch.** Three radios bound to VBoxUSB (Doug's ruling 16 Aug); launch audit
watching the wedge rate `[custody-watch v3.131 doing]`. iPad HID lifecycle must never
disconnect audio `[shared-radio-isolation v3.147 doing]`. Deep reference: `RADIO-CUSTODY.md`,
`TECHNICAL_NOTES.md`. ▸ the wedge-rate verdict is Doug's observation to close.

### Clipboard
**Live.** Two-way Ctrl+C/Ctrl+V via Apple Shortcuts + token-guarded LAN relay
(`CLIPBOARD_SETUP.md`, `CLIPBOARD_DESIGN.md`). Limit: touch-made iPad copies don't auto-sync.

### Desk canvas — physical arrangement
**Live, active work.** EDID-scaled screens, snap, Identify, independent DESKTOP vs PRIMARY
marks `[desktop-monitor-role v3.145 doing]`, GUI+bar+icons+dock travelling as one Desktop
role `[unified-desktop-role v3.149 doing]`.

### Control app GUI
**Live, active work.** Single scrolling control page `[single-scroll-page v3.148 doing]`,
dark scrollbars `[dark-scrollbars v3.151 doing]`. Surface mode vs window mode probe
(`win/surface_mode.py`) decides closeability once at startup.

### Windows Control Center
**Released 2026-08-23, not started.** `[control-center-v2 v3.153 todo]` — catalog over four
discovery sources, Medium-integrity Shell broker (no explorer.exe route), searchable GUI
block. **Spec: `docs/CONTROL-CENTER.md` (frozen).** Named blocker it answers: elevated GUI
cannot launch Settings (0x87B20C15). Distinct from the deferred v1.68 Control Center flyout.

### Elevation and security
**Live.** Admin-only launch invariant `[admin-only-app v3.146 doing]`: non-elevated bootstrap
relaunches elevated; `EsotericOS App (elevated)` scheduled task at RunLevel Highest.
Installer-owned firewall identity `[durable-firewall-install v3.150 doing]`. Programs
run-as-admin `[programs-run-as-admin v3.152 doing]`. Credential file
`D:\_SERVER\.secrets\api-keys.txt` is never read by anything here.

### Shell swap — EsotericOS Shell
**Doing, gated.** Milestone `v3.M9`. De-Cairo identity migration
`[de-cairo-identity v3.136 doing]`. Forge compatibility under the new shell is a blocking
row `[forge-compat]` — nothing swaps until verified. Postmortem:
`SHELL-TAKEOVER-POSTMORTEM.md`.

### Files — the browser
**Blocked — the board's only blocked item.** `[files-grid v3.132]` tabs + arbitrary split
grid. Vision notes in `docs/files-vision/`. ▸ what unblocks it is Doug's to name on the row.

### LAN nodes — one desk across PCs
**Live core, milestone doing** (`v3.M10`). 32-byte-key identity, DNS-SD/mDNS discovery,
HMAC-signed transport, ephemeral OS-assigned ports (`guest/lan_nodes.py`). Desk federation
(more than a placeholder screen per node) is dormant, waiting on the desk canvas work.

### VM and provisioning
**Live, reproducible.** `create-vm.ps1` → Debian 12 → `guest/provision.sh all`;
`cold-test.ps1` verifies on a fresh clone (`PROVISION_SPEC.md`). Multi-radio via
`configure-multiradio.ps1` (opt-in; assignments by controller MAC).

### Build and release
**Live.** 53 test suites (`win\test_*.py`), `build_exe.py` → single `OpenSpan.exe`,
`swap-build.ps1` hot-swaps the running exe with the VM up (`BUILD.md`). Licence
AGPL-3.0-or-later since 2026-08-16; dependency-licence audit on the board
`[licence-audit]`. Publishing is gated on Doug's explicit word.

---

*Maintenance: update the subject line when a board item changes state; this file names
owners and roads, the board carries the work. Corrections are edits here; chronology stays
in the board log.*
