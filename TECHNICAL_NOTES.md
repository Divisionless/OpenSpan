# OpenSpan — Technical Notes: What Makes It Work

*A consolidation of the observations and fixes that took OpenSpan from "flaky"
to a working Windows → iPad bridge + Windows → Bluetooth audio router. Written
as a rebuild-from-scratch reference. Last updated 2026-07-10.*

---

## 1. The core architecture (and its one hard constraint)

One PC. **One Intel Bluetooth radio.** That radio is passed through (xHCI / USB
3.0) into a **headless Debian 12 VirtualBox VM**, and the VM performs *both*
Bluetooth roles on it simultaneously:

1. **A2DP audio source** → Bluetooth earbuds (onn True Wireless).
2. **BLE HID keyboard + mouse** (HID-over-GATT peripheral) → iPad.

The single physical antenna is the whole story. The two roles are
**operationally independent** (separate bonds, names, pairing flows — neither
reacts to the other) but they are **not physically independent**: they
*time-share the one radio's airtime*. Every hard problem in this project traces
back to that shared antenna. Coexistence is clean only when the BLE side's
airtime demand stays modest.

Windows side runs a Tkinter control app (`win/openspan.py`), an audio sender
(`win/win_audio_send.py`), and an input portal (`win/openspan_portal.py`).

---

## 2. The audio path

```
PC apps → VB-Audio Virtual Cable (default output)
        → WASAPI loopback capture (win_audio_send.py)
        → UDP :4010
        → VM: udp_to_sink.py → pw-play → PipeWire → BlueZ A2DP
        → earbuds
```

**What makes it hold together:**

- **PipeWire / WirePlumber / BlueZ run on a real, persistent systemd *user*
  bus.** `loginctl enable-linger root` gives a durable `/run/user/0/bus`. This
  is the crux — an earlier hand-rolled `dbus-daemon` could not accept
  WirePlumber's reconnects. Do **not** hand-roll dbus.
- **The A2DP transport must not idle-suspend. TWO mechanisms hold it, both
  present (verified from the live VM 2026-07-12 via `dpkg -V`):** (1) the
  WirePlumber bluez config `/usr/share/wireplumber/bluetooth.lua.d/50-bluez-config.lua`
  is *modified* from stock to set `session.suspend-timeout-seconds = 0`,
  `node.pause-on-idle = false`, `with-logind = false` (captured at
  `guest/system/wireplumber-50-bluez-config.lua`); and (2) `udp_to_sink.py`
  never stops feeding the stream. **Never remove the silence feed.** *(An
  earlier note here claimed this config didn't exist and the feed was the
  whole story — that was wrong; ground truth shows both. HFP is disabled in
  this same file via `bluez5.hfphsp-backend = "none"`, so the repo's
  `52-no-hfp.lua` is unused.)*
- The stock per-login `pipewire` user service is **masked** (login-churn
  contention).
- **Sample rate must match end-to-end.** VB-Cable's default is 48000 Hz / 2 ch;
  `pw-play` is pinned to `--rate=48000 --channels=2`. A mismatch plays at the
  wrong speed = garbled/pitch-shifted, not just quiet.
- **Exactly one sender may run.** Two `win_audio_send.py` processes both
  streaming to UDP :4010 interleave into the single `pw-play` and *garble the
  audio*. Enforced two ways: a Windows named mutex `OpenSpanAudioSender` in the
  sender (a duplicate `sys.exit(0)`s before it captures) and an `_audio_lock`
  around the app's sender (re)launch so overlapping status ticks can't spawn two.
- **Bridge buffering absorbs radio contention — and is bounded so latency
  can't creep (2026-07-10).** `udp_to_sink.py` uses `pw-play --latency=250ms`
  as the anti-garble cushion. Two silent latency-adders were fixed: the stdin
  pipe to pw-play defaulted to 64 KB (a hidden **341 ms** of audio on top of
  the 250) — now shrunk to 16 KB (~85 ms) via `F_SETPIPE_SZ`, with
  `bufsize=0` so Python adds no batching; and the gap-filler used to inject
  silence on *every* 20 ms socket timeout, so merely-late packets (BLE
  airtime bursts) permanently ratcheted the queue upward — silence now starts
  only after 100 ms of genuinely missing data (5 consecutive timeouts), then
  feeds at real time. Ordinary jitter rides the 250 ms cushion unaided, and
  every real pause drains up to 100 ms of accumulated excess. To trade
  headroom for lower latency, lower `LATENCY_MS` (e.g. 150) and test with
  heavy mouse + audio.

**Volume control (the "Windows sound thing"):** the VB-Cable loopback the sender
captures ignores the Windows volume slider, so there was no volume control. Fix:
`win_audio_send.py` runs a watcher thread that mirrors the Windows *master
volume of the default output device* (via pycaw's `IAudioEndpointVolume
.GetMasterVolumeLevelScalar()`, polled every 150 ms) into a live gain the audio
callback applies. Now the normal Windows volume slider / keys / mute control the
earbuds. Fails safe: any Core-Audio error or missing pycaw → gain 1.0 (full,
audio unaffected). Dependency: `pycaw` (+`comtypes`), installed `--user`.

---

## 3. The keyboard / mouse path

```
Windows input portal (openspan_portal.py, captures at the screen edge)
        → TCP :9955
        → openspan_ble.py (BLE HID-over-GATT peripheral)
        → iPad
```

**What makes it work:**

- iOS needs **LE HID (HOGP)**, not Classic. An auto-accept pairing agent
  (`NoInputNoOutput`) handles bonding.
- **The LE connection interval is the master dial** — it governs both mouse
  latency *and* how much airtime the BLE link steals from A2DP audio. Set to
  **15–30 ms** (`MinConnectionInterval=12` / `MaxConnectionInterval=24`, raw
  1.25 ms units, in `/etc/bluetooth/main.conf` + debugfs + `btready.sh`):
  - 7.5 ms (what "fixed" the mouse first) is too aggressive — it services the
    iPad ~133×/s and *starves the A2DP stream into constant garble*.
  - The kernel default 30–50 ms is audio-clean but the mouse feels laggy.
  - 15–30 ms is the balance: snappy mouse, airtime left for clean audio.
- **`Discoverable = False` on the adapter.** A dual-mode adapter that is
  discoverable shows a second, un-pairable "OpenSpan Keyboard" (a BR/EDR Classic
  decoy) in the iPad's device list. BR/EDR stays *enabled* (audio needs it), just
  not discoverable. The iPad still finds the real keyboard via its LE
  advertisement.
- **Restarting the keyboard daemon must not power-cycle the radio.**
  `openspanble`'s `ExecStartPre` uses `ensure-dualmode.sh`, which **only**
  toggles bearers if the adapter isn't *already* powered + dual-mode, and
  otherwise `exit 0`s. An unconditional `btmgmt power off … power on` here drops
  active A2DP audio — coupling the keyboard to the audio. **Verify with
  `systemctl cat openspanble`, not just by reading `ensure-dualmode.sh`:** a
  drop-in (`10-wait.conf`) can *reset the ExecStartPre list* and reintroduce the
  unconditional power-cycle. This exact override was the "Broadcast instantly
  kills the audio" bug.
- **Broadcast (Pair/Broadcast) is audio-safe — and FAIL-CLOSED (2026-07-10).**
  It restarts `openspanble` for a fresh GATT server and clears stale bonds so
  the iPad re-pairs clean, but a bond is removed **only** when
  `bluetoothctl info` *explicitly* shows `Connected: no`, the device has no
  `Icon: audio`, and it is not the MAC in `audio-device.txt`. If `info` comes
  back empty (busy bluetoothd) the device is KEPT — the old fail-open loop
  could remove the *playing* earbuds' bond on an empty read, force-
  disconnecting them mid-song. Note the residual physics: while the iPad is
  actually pairing (LE connect + SMP + GATT discovery burst), the shared
  antenna can still starve A2DP for a few seconds — the app now self-heals
  audio afterwards (see auto-reconnect in §6).
- **Fresh GATT on restart.** iOS caches the CCCD subscription; after any
  `openspanble` restart the reliable input path is *forget-on-iPad → Broadcast →
  fresh pair*. A proper Service-Changed indication is the open long-term fix.

---

## 4. The radio + the VM

- **xHCI (USB 3.0) passthrough, not EHCI.** On xHCI the radio enumerates in
  ~8 s and holds under streaming load; EHCI/OHCI dropped it.
- **Never let the radio idle-suspend:** `usbcore.autosuspend=-1` (grub) +
  `options btusb enable_autosuspend=0`.
- **Cold boot is mandatory for the kernel cmdline to apply.** A VirtualBox
  saved-state resume skips grub/kernel changes and can leave the passed-through
  radio wedged. `start_vm_clean()` does `VBoxManage discardstate` (if saved) then
  `startvm --type headless` — a guaranteed cold boot + clean radio
  re-enumeration.
- A full **host power-off** unwedges a stuck controller
  (`hci0: Opcode 0x0c03 failed: -110`).
- The onn earbuds **change MAC on factory reset** — discover by *name*
  ("onn True Wireless ENC"), not a hard-coded address.
- **Never auto-scan.** Scanning is user-initiated only; the device list is built
  from `busctl` (BlueZ object tree), which needs no advertisement monitor.

---

## 5. Windows launch + lifecycle

- **On-demand only.** Launches from a Start Menu shortcut *or*
  `D:\OpenSpan\OpenSpan.bat`. The launcher prefers the packaged
  **`OpenSpan.exe`** (single file, built via `build_exe.py` — see BUILD.md)
  when present, and falls back to **`C:\Python313\openspanw.exe
  win\openspan.py`** otherwise.
- **Single-file packaging (2026-07-12).** `build_exe.py` → PyInstaller →
  one ~64 MB `OpenSpan.exe` that runs in place (data files anchor on
  `sys.executable`'s folder, which is `D:\OpenSpan`). The GUI's separate
  portal/audio processes become **role flags of the same exe** (`--portal`,
  `--audio`, `--setup`) dispatched by `openspan_launcher.py` (the frozen
  entry script); every module switches its path anchor from `__file__` to
  `sys.executable` under `sys.frozen`. Built `--noconsole` + unflagged →
  unelevated, no console flash. The exe / `build/` / `dist/` are gitignored.
- **Why a renamed interpreter (`openspanw.exe`):** `C:\Python313\pythonw.exe`
  carries a `RUNASADMIN` AppCompatFlags layer that forces elevation of the
  unsigned interpreter, which trips Windows' shell reputation gate ("an
  administrator has blocked this app" — independent of the UAC prompt setting).
  A same-directory unflagged copy (`Copy-Item pythonw.exe openspanw.exe`) runs
  unelevated → no block. OpenSpan needs no admin (VBoxManage / ssh / shutdown /
  subprocess all work unelevated).
- **Do not put the launcher in a OneDrive-synced folder** — that was a false
  lead, but OneDrive placeholders add their own friction; keep launchers local
  (Start Menu / D: drive).
- **Closing is a full stop — after confirmation.** The X opens the
  close-confirm dialog; *Yes, shut it down* (`_full_stop`) terminates the
  portal + audio sender *and* powers off the VM, so nothing lingers and the
  next launch is a clean cold boot. *Send to system tray* keeps the whole
  bridge running with no window.
- **Guest scripts auto-deploy on launch** (`_sync_guest_scripts`):
  `win/guest-bt-connect.sh` → `/opt/openspan/bt-connect.sh`,
  `guest/udp_to_sink.py` → `/opt/openspan/udp_to_sink.py` (loads on the next
  "⟳ Restart audio"), and `guest/btready.sh` → `/opt/openspan/btready.sh`
  (runs at next VM boot). Content is written as **LF bytes over ssh stdin** —
  never `text=True`, which re-adds `\r\n` and breaks bash (`env.sh␍: No such
  file`) — and lands via write-then-`mv` so a script that is running right
  now is never truncated mid-execution.
- Radio ownership is a "station" vs "windows" **mode** (`mode.txt`); the app only
  grabs the radio in station mode.
- **SSH key is self-provisioning (2026-07-12).** The host↔VM key `id_openspan`
  is gitignored (a private key must never ship), so `run_app()` calls
  `ensure_ssh_key()` on launch: if the key is missing it generates an
  ed25519 keypair (no passphrase — unattended loopback to a local VM) and
  never regenerates an existing one. The public half goes into the VM via
  `guest/install-authorized-key.sh` (installs openssh-server, appends the
  key de-duped, sets `PermitRootLogin prohibit-password` + `PubkeyAuthentication
  yes`, restarts sshd) — called by the provisioner or once by hand. This is
  what lets a fresh clone reach its own bridge instead of dying on a missing
  key.

---

## 6. The control app UI

- One window (1500x860), three columns side by side — **iPad Bridge** |
  **Bluetooth & Headphones** | a **persistent Console** that logs every
  command the app runs (color-coded, routine polls silenced) and carries a big
  **READY** banner (stopped → booting ~90 s → READY = VM up + daemon
  reachable). No tabs; everything is visible at once.
- **The X asks before killing everything**: a dialog (reentry-guarded — a
  second X focuses it instead of stacking another) offers *Send to system
  tray* (window hides, VM/audio/portal/watchdog all keep running; click the
  icon to come back), *Yes, shut it down* (the full stop below), or *Cancel*.
- **The tray icon is crash- and strand-proof by construction**: the window
  class + WNDPROC thunk are registered once per process and never freed (a
  dangling `lpfnWndProc` is a native 0xC0000005); the hidden window is a
  *real* window, not message-only, so it receives the broadcast
  `TaskbarCreated` after an explorer.exe restart and re-adds the icon; and
  the 3 s poll calls `TrayIcon.ensure()` while hidden — if the icon can't be
  restored, the window deiconifies itself. The app can never be stranded
  invisible with the single-instance mutex held.
- **Worker threads never touch Tk — not even `after()`.** A background
  `after()` racing the UI thread hard-crashes the interpreter
  (`PyEval_RestoreThread` GIL abort; reproduced in the render harness).
  Workers queue closures via `App.ui()` (a plain `queue.Queue`); the UI
  thread drains it every 50 ms (`_drain_ui`).
- BT device list columns: **Device | Status | Type | Address**. Status is
  `● Connected` / `○ Paired (idle)` / `Available` / `⛔ Blacklisted`.
- **Inline rename** (edit box on the row), persistent custom names + a blacklist
  in `bt_prefs.json` (keyed by MAC) that survive re-pairing; right-click actions.
- **Connect is honest AND stubborn (2026-07-11, review-hardened):** one
  click retries up to **5 attempts over ~30 s** (2.5 s apart), stopping the
  moment a real link reports `CONNECTED` — earbuds waking from the case
  routinely miss the first page. Branch-aware: a pairing pass ("paired ✓")
  rolls into the connect with a **fresh** 30 s budget; a FAILED pairing
  stops the loop outright (retrying it = another 30 s scan volley — one
  scan per click, always); the bonded fast path retries on a short 20 s
  ssh timeout so a wedged attempt can't hold the loop for minutes. Every
  attempt is logged `[n/5]`. While the loop runs, Scan / Disconnect /
  Forget / Blacklist are locked (a mid-loop Forget would be silently
  undone by the next attempt re-pairing the device) and auto-reconnect
  defers. Refreshes never get swallowed mid-loop — a busy refresh queues a
  trailing rerun so a fresh CONNECTED can't be painted over by a pre-link
  snapshot.
- **One window: Audio panel + collapsible console (2026-07-13):** there is no
  separate "compact" mode. The command console (right panel) collapses by
  default so the window opens lean; the header's **Console** toggle packs/
  unpacks it and grows/shrinks the window width to match (a width change
  requested while maximized is deferred and re-applied on un-maximize, so it
  can't restore wrong-sized). The header's **Send to Tray** hides the window
  while the bridge — VM, audio, portal — keeps running; the tray icon restores
  it. An always-visible **Audio & status** panel (in the Bluetooth column,
  shown in both the console-open and tray-restored states) carries the
  readiness line, VM/iPad/Audio/Portal dots, the connected headphones, a
  **Volume** slider (sets the Windows master volume — the exact dial the
  sender's GAIN mirror follows), and an **L↔R Balance** slider. Balance CANNOT
  use Windows channel volumes (the sender's loopback capture is pre-volume —
  same reason the GAIN mirror exists); the slider writes `audio_balance.txt`
  (-1..+1, gitignored) which the sender polls every 150 ms and applies as
  per-channel gains in the callback (centered = untouched fast path). **All
  volume COM lives on one dedicated `_volume_thread`** (the sender's proven
  pattern): COM on the Tk thread froze the whole UI whenever AudioSrv was busy,
  and a cached endpoint goes stale on a default-device switch with NO error —
  the thread re-resolves it every ~30 s. The slider hands the thread a
  latest-wins target; pycaw missing → slider disabled, audio unaffected.
  Balance writes are atomic (`.new` + `os.replace`) — a truncate-then-write
  raced the sender's 150 ms poll into audible one-tick recenters mid-drag —
  and both readers treat non-finite values ('nan'/'inf', which PARSE fine) as
  centered rather than letting the clamp hard-pan them.
- **Auto-reconnect (2026-07-10, hardened by adversarial review):** the buds
  page the adapter during the ~90 s VM boot, give up before the stack is
  READY, and never retry — so the app retries for them.
  `_auto_reconnect_audio` fires on the READY edge and after an iPad pairing
  completes. It is **structurally unable to scan or pair**: it never calls
  `bt-connect.sh` (whose unpaired branch scans) — it runs a connect-only
  command that re-verifies `Paired: yes` guest-side *in the same shell* and
  does nothing on any doubt. Guards: target must be the `audio-device.txt`
  MAC, **bonded, idle, `Icon: audio`, not blacklisted**; defers to any
  in-flight manual Connect/Scan/Forget; suppressed while a Broadcast is in
  flight; ≥90 s cooldown between firings (a flapping READY edge can't storm
  the radio); pauses for the session after 3 failed rounds (never sits there
  paging powered-off buds). Honest console lines: "connected ✓" / "didn't
  respond — wake the buds". `audio-device.txt` itself is only ever written
  by `bt-connect.sh` for devices whose Icon says audio, so a stray click on
  a non-audio row can't poison the target.
- **Broadcast bookkeeping:** `_pair_inflight` is set the moment the button
  is pressed (the `openspanble` restart flaps the daemon port, and the READY
  re-edge must not fire auto-reconnect into the middle of a pairing); an
  abandoned Broadcast expires after 5 minutes so it can't suppress
  auto-reconnect forever.
- **Two-way clipboard with the iPad (2026-07-10, review-hardened):** a
  token-gated LAN relay inside the app (`ClipboardServer`, stdlib, :9966 —
  GET `/clip` = Windows clipboard out, POST = in; 30 s socket timeout,
  keep-alive-safe error paths, strict JSON validation, crash-proof
  handlers) + two iPad Shortcuts, triggered by FKA key combos the portal
  sends through the HID keyboard: **Ctrl+Alt+Shift+V** = iPad fetches the
  PC clipboard, **Ctrl+Alt+Shift+C** = iPad pushes its own. Chords are
  autorepeat/mash-guarded, SERIALIZED (never interleave), and
  passthrough-suppressed while in flight.
  **Seamless mode (2026-07-11): plain Ctrl+C/V are all the user touches** — the
  portal fires the chords itself: on portal ENTRY it hands the Windows
  clipboard to the iPad iff `GetClipboardSequenceNumber` says it changed
  since the last sync, and every Ctrl+C/X inside iPad mode schedules a push
  back to the PC (~350 ms after the copy; overlapping copies collapse into
  one pending push; a recent chord defers rather than drops the push, else
  the clipboards silently diverge). Touch-made iPad copies don't auto-push
  (no key event to see) — Ctrl+C after selecting, or the manual combo.
  Setup recipe: `CLIPBOARD_SETUP.md`; design + platform facts:
  `CLIPBOARD_DESIGN.md`. **The token lives ONLY in the gitignored
  `openspan_settings.json`** (the first minted token was burned in a review
  artifact and rotated — never commit that file). Ctrl+Alt+V typing paste
  remains the zero-setup fallback. Found while building this: ctypes
  clipboard calls MUST prototype HANDLE restypes — the default 32-bit
  restype truncates 64-bit clipboard handles into silent empty reads (the
  portal's paste had carried that latent bug from day one).
- **Guest journal is persistent** (`/var/log/journal`, prepped idempotently at
  every launch sync), and every VM power-off path (`_full_stop`, Stop VM,
  Cold-restart) runs a best-effort `journalctl --sync` first so the final
  minutes aren't lost to the page cache. "X broke the audio" reports can be
  diagnosed after the fact with
  `journalctl -u bluetooth -u openspanble -u openspan-wireplumber --since …`.

---

## 7. Hard-won gotchas — do not reintroduce

| Symptom | Cause | Fix |
|---|---|---|
| A2DP drops ~5 s into silence | transport idle-suspend | modified `50-bluez-config.lua` (`suspend-timeout=0`, `pause-on-idle=false`, `with-logind=false`) **and** the bridge's continuous silence feed — both present, keep both |
| Audio latency grows over a session | open-loop silence injection + hidden 64 KB stdin pipe | debounced gap-filler (100 ms) + `F_SETPIPE_SZ` 16 KB |
| WirePlumber won't reconnect | hand-rolled dbus | linger user bus `/run/user/0/bus` |
| Garbled / pitch-shifted audio | sample-rate mismatch | pin both ends to 48000/2 |
| Garbled audio, quality-wise fine | **two** audio senders on :4010 | single-instance mutex + launch lock |
| Constant audio garble w/ mouse active | LE interval too aggressive (7.5 ms) | 15–30 ms interval |
| Second un-pairable "OpenSpan Keyboard" on iPad | discoverable BR/EDR decoy | `Discoverable=False` |
| Broadcast instantly kills audio | drop-in reset ExecStartPre → unconditional radio power-cycle | conditional `ensure-dualmode.sh`; check `systemctl cat` |
| "an administrator has blocked this app" | `pythonw.exe` RUNASADMIN forces elevation of unsigned exe | launch via unflagged `openspanw.exe` |
| `env.sh␍: No such file` after deploy | CRLF from `text=True` ssh stdin | write LF bytes |
| Radio drops under load | EHCI passthrough / autosuspend | xHCI + autosuspend off |
| Kernel cmdline / radio wedged after resume | saved-state resume | `discardstate` + cold boot |

---

*The through-line: it's one antenna doing two jobs. Keep the BLE side gentle,
keep exactly one of everything, cold-boot the radio, and never couple a keyboard
action to the audio path.*
