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
| 1 | `swap-build.ps1 -CloseRunning -Elevated` → app relaunches as **7aab8cb7** (custody line per radio + "Take custody" button in the Bluetooth pane; portal restore already in) | custody UI live; VM stays warm | 1 min | staged exe present | `.prev` = b6240700 |
| 2 | Files alpha B live look: open `EsotericOS.Files.exe` on the scratch folder — tabs (Ctrl+T/W), column view (Ctrl+2), Cut dims, copy/paste with the collision panel, drag inside its own window only | you judge B before C starts | 5 min | B agent's report landed | close the window |
| 3 | Radio custody, TP-Link first (Doug's hands): app full stop → VM off → `radio_custody.py take <tp-link id> --apply` → VM on → confirm `list usbhost` shows it Captured with the proxy bound at boot | proves the bind before the Intel | 10–15 min | entry 1 done; you at the desk | `return <id> --apply` + replug |
| 4 | Radio custody, Intel (Doug's hands): same sequence on port 14; then a Windows restart with NO captures held to prove it survives boot | the actual fix for the wedge | 15 min + reboot | entry 3 proven | `return --apply`; Windows restart |
| 5 | Shell relaunch with EsotericOS theme/menu/status (already running); enable bars on all three panels; look at popups/menus for the quiet-dark pass | v3.105/109 judgment | 5 min | — | Cairo → Exit; Explorer taskbar returns |
| 6 | Forge compatibility audit under the shell (rect before/after, taskbar/tray, toasts, file dialogs, showItemInFolder) — **parked until Doug says the Forge is ready** | v3.114 blocks the swap | 15 min | Forge modernization landed | none needed (read + relaunch shell) |

## Done

_(none yet)_
