# Test window — the queue of live desk work

Anything that touches Doug's desk while he is using it — a swap, a relaunch, a
simulated click, a radio action, a shell change — waits here. It runs only inside
a window Doug authorizes, back to back, with the total estimate stated first.
Silent work (builds to staging, tests, code, docs, `-Check` audits) never waits.

Each entry: what happens on the desk · why · estimate · prerequisites · rollback.
When a window runs, outcomes are recorded against each entry, then the entry moves
to "Done" below.

## Queued

| # | What happens on the desk | Why | Est. | Prereqs | Rollback |
|---|---|---|---|---|---|
| 6 | Forge compatibility audit under the shell (rect before/after, taskbar/tray, toasts, file dialogs, showItemInFolder) — **parked until Doug says the Forge is ready** | v3.114 blocks the swap | 15 min | Forge modernization landed | none needed (read + relaunch shell) |
| 7 | Swap to a build that carries M10 rows 1–2 (LAN node service + pairing, `peers` in status.json, firewall program rule via `bake-in.ps1`) once it is staged and its tests are green | first LAN node on this box | 1 min | staged exe; `bake-in.ps1` run once elevated for the firewall rule (that run IS the consent) | `.prev` |

## Done

### Window of 2026-08-16 15:25–15:50 (Doug: "1-5"; estimate given ~40 min + reboot; actual ~25 min, no reboot)

| # | What happened | Outcome |
|---|---|---|
| 1 | `swap-build.ps1 -CloseRunning -Elevated` → **7aab8cb7** live, elevated | ✅ VM stayed warm; portal restored itself (portal memory); audio on; 1/3 devices; `.prev` = b6240700 |
| 2 | Files alpha B opened on the scratch `sample` folder for Doug to drive (no simulated input) | ✅ launched (`sample — EsotericOS Files`, 138 MB, responding); Doug's judgment pending |
| 5 | Shell theme/menus: no relaunch needed — Cairo applies both multi-mon toggles live (`EnableMenuBarMultiMon`, `EnableTaskbarMultiMon`); Doug flips them under EsotericOS menu → Settings | ✅ handed to Doug; running Cairo (07:13 build) already carries theme + menu + status; only the About-title commit is newer |
| 3 | TP-Link custody (Laptop dongle `3C6AD23CD44E`): app tree closed → `vm_off_gently.py` (3 released, clean poweroff) → `take --apply` (6/6 ok) → app relaunched | ⚠️ **bind worked, thesis refuted**: VirtualBox tore the custody node down at VM start and re-added its CAFE proxy anyway. Dongle in service. Side findings: Intel wedged on *release* (proxy Started / real Stopped / `Unavailable`); audit twin-dongle bug found + fixed. Full record: `RADIO-CUSTODY.md` §8, DEVLOG 15:30 |
| 4 | Intel custody + reboot | ⏹ **abandoned as designed** — no proven benefit after #3, real recovery cost. Intel recovered instead with the §6 proxy-node kick (`Captured` in 10 s, daemon up). Reboot skipped (also spared the M10 agent mid-flight and the Forge) |

Desk at close: app **7aab8cb7** on the desktop, elevated; VM up; VM holds all three radios; daemon up; portal on; audio on; Cairo fork running alongside Explorer; Files B window open for Doug.
