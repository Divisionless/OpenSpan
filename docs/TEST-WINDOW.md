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
| 7 | `swap-build.ps1 -CloseRunning -Elevated` → **55d19b4e** (M10 rows 1–2: LAN node service advertising `_esotericos._tcp` on an OS-assigned port, pairing, `peers` in status.json, `vm: none`) then `bake-in.ps1` once elevated for the firewall program rule (that run IS the consent) | first LAN node on this box | 2 min | staged 15:51 ✔ (58/58 test files green) | `.prev` = 7aab8cb7; `bake-in.ps1 -Undo` removes the rule |

## Done

### Window of 2026-08-16 15:25–15:50 (Doug: "1-5"; estimate given ~40 min + reboot; actual ~25 min, no reboot)

| # | What happened | Outcome |
|---|---|---|
| 1 | `swap-build.ps1 -CloseRunning -Elevated` → **7aab8cb7** live, elevated | ✅ VM stayed warm; portal restored itself (portal memory); audio on; 1/3 devices; `.prev` = b6240700 |
| 2 | Files alpha B opened on the scratch `sample` folder for Doug to drive (no simulated input) | ✅ launched (`sample — EsotericOS Files`, 138 MB, responding); Doug's judgment pending |
| 5 | Shell theme/menus: no relaunch needed — Cairo applies both multi-mon toggles live (`EnableMenuBarMultiMon`, `EnableTaskbarMultiMon`); Doug flips them under EsotericOS menu → Settings | ✅ handed to Doug; running Cairo (07:13 build) already carries theme + menu + status; only the About-title commit is newer |
| 3 | TP-Link custody (Laptop dongle `3C6AD23CD44E`): app tree closed → `vm_off_gently.py` (3 released, clean poweroff) → `take --apply` (6/6 ok) → app relaunched | ⚠️ **bind worked, thesis refuted**: VirtualBox tore the custody node down at VM start and re-added its CAFE proxy anyway. Dongle in service. Side findings: Intel wedged on *release* (proxy Started / real Stopped / `Unavailable`); audit twin-dongle bug found + fixed. Full record: `RADIO-CUSTODY.md` §8, DEVLOG 15:30 |
| 4 | Intel custody + reboot | ⏹ first pass: I abandoned it on my own read of #3 ("no proven benefit"). **Overruled by Doug** — "I need maximal control of all 3 radios" — and run at 16:0x: app down → VM off → Mac dongle `take` (6/6 ok) → Intel wedged on release again → **one proxy-node restart with the VM OFF** brought the real node back Started under the Intel driver → Intel `take` (6/6 ok) → **all three real nodes on `VBoxUSB`, Windows' Bluetooth class empty** → app up → VM took all three on the first start, daemon up. Reboot proof (custody survives boot; take it with the VM off) is on Doug's schedule — the Forge is open |

Desk at close: app **7aab8cb7** on the desktop, elevated; VM up holding all three radios (Intel via proxy on first start, no kick); daemon up; portal on; audio on; all three radios in EsotericOS custody; Cairo fork running alongside Explorer; Files B window open for Doug.
