# OpenSpan

**Drive your iPad from your PC — keyboard, mouse, audio, and clipboard — over Bluetooth. Free, local, no cloud, no account.**

OpenSpan turns a Windows PC into a Bluetooth peripheral for a nearby iPad.
Shove your mouse off a screen edge (Universal Control / Input Director style)
and your keyboard and mouse drive iPadOS directly — type in Messages, scroll
Safari, switch apps. Cross back and you're on the PC again. On the same
Bluetooth radio it also routes your PC audio to Bluetooth earbuds and keeps a
two-way clipboard in sync between the two devices.

No paid software, no account, no telemetry. A small Windows app plus a headless
Linux VM doing the one thing Windows won't: pretending to be a Bluetooth
keyboard.

---

## Why a VM?

Windows deliberately blocks applications from publishing the Bluetooth HID
(keyboard/mouse) service — the OS reserves it. Linux's BlueZ does not. So
OpenSpan runs a headless Debian 12 VM that owns the PC's Bluetooth radio (via
USB passthrough) and advertises as a BLE HID keyboard; a Windows app captures
your real input and streams it in.

```
your keyboard / mouse
       │  (low-level Win32 hooks)
Windows: openspan_portal.py ── TCP :9955 ──▶  Debian VM: openspan_ble.py
                                                    │  BlueZ GATT (BLE HID)
                                                    ▼
                                                  iPad  (bonded BLE keyboard)

PC audio ─▶ VB-Cable ─▶ WASAPI loopback ── UDP :4010 ──▶ VM: PipeWire ─▶ A2DP ─▶ earbuds
```

The single radio time-shares both jobs (BLE HID to the iPad, A2DP audio to the
earbuds). Keeping the BLE link's airtime modest is what lets the audio stay
clean — see `TECHNICAL_NOTES.md`.

## Features

- **Keyboard + mouse bridge** — cross a screen edge to control the iPad; a
  keymap remaps modifiers (Alt→Cmd, Ctrl+C→Cmd+C, …).
- **Bluetooth audio routing** — send Windows audio to BT earbuds through the
  same radio, with the normal Windows volume slider and an in-app L/R balance.
- **Two-way clipboard** — plain **Ctrl+C / Ctrl+V** keep both machines in sync
  (via Apple Shortcuts + a tiny token-guarded LAN relay). See
  `CLIPBOARD_SETUP.md`.
- **Lean window + tray** — the command console tucks away by default for a
  compact window; **Send to Tray** keeps the bridge (VM, audio, portal) running
  in the background.
- **Fast pairing + auto-reconnect** — one click frees the whole radio for a
  full-power broadcast so the iPad finds the keyboard quickly, auto-starts the
  bridge the moment it bonds, then brings the earbuds back on their own.
- **Single-file build** — packages to one `OpenSpan.exe` (see `BUILD.md`).

## Hard-won facts (read before you "fix" something)

These cost real debugging; they are the difference between working and not:

1. **BLE, not Classic.** Classic (BR/EDR) HID emulation *pairs* with an iPad
   but the iPad never accepts the keystrokes. Apple only cooperates over **BLE
   HID (HID-over-GATT / HOGP)**. OpenSpan is BLE.
2. **xHCI (USB 3.0) passthrough, not EHCI/OHCI.** The USB 1.1/2.0 controllers
   dropped the Intel radio under streaming load; xHCI enumerates it in ~8 s and
   holds. `VBoxManage modifyvm OpenSpan --usbxhci on` (needs the Extension
   Pack), plus `usbcore.autosuspend=-1` and `options btusb enable_autosuspend=0`
   so the radio never idle-suspends.
3. **`encrypt-read` forces the bond.** iOS will connect to a BLE keyboard and
   even subscribe *without bonding*, then silently ignore every keystroke.
   Marking the HID report characteristics `encrypt-read` forces iOS to bond,
   which activates the keyboard.
4. **Dual mode + not discoverable.** The adapter runs `ControllerMode = dual`
   (BR/EDR stays enabled — the audio needs it) with `Discoverable = false`, so
   the iPad sees one clean LE keyboard entry instead of a second, un-pairable
   Classic decoy. The LE connection interval is pinned to 15–30 ms
   (`MinConnectionInterval = 12` / `MaxConnectionInterval = 24`): tighter
   starves the audio, looser makes the mouse laggy.

## Layout

```
openspan/
├── win/                         # runs on Windows (stdlib Python + ctypes)
│   ├── openspan.py              # control app — start here
│   ├── openspan_portal.py       # edge-crossing keyboard/mouse router
│   ├── win_audio_send.py        # WASAPI loopback → UDP audio sender
│   ├── openspan_setup.py        # drag-to-arrange the iPad among your monitors
│   └── openspan_launcher.py     # role dispatch for the packaged exe
├── guest/                       # runs inside the Debian VM
│   ├── openspan_ble.py          # BLE HID GATT peripheral + :9955 command server
│   ├── udp_to_sink.py           # UDP audio → PipeWire A2DP bridge
│   ├── *.service                # systemd units (BLE daemon, audio stack, agent)
│   ├── bt-list.sh / bt-connect.sh / btready.sh / env.sh …   # runtime helpers
│   ├── system/                  # captured host config (main.conf, drop-ins, grub…)
│   └── rebuild/                 # audio-stack (PipeWire/WirePlumber) install set
├── build_exe.py                 # package into a single OpenSpan.exe
├── TECHNICAL_NOTES.md           # deep "what makes it work" reference
├── BUILD.md · CLIPBOARD_SETUP.md · CLIPBOARD_DESIGN.md
├── LICENSE                      # MIT
└── README.md
```

## Running it (Windows side)

Launch `OpenSpan.exe` (build it with `python build_exe.py`) or `OpenSpan.bat`.
From plain source: `python win/openspan.py` — note Windows' unsigned-app
reputation gate may block a raw `pythonw.exe`; `BUILD.md` explains the packaged
exe and the interpreter workaround. The control app starts/stops the bridge VM
and the input portal, arranges the iPad among your monitors, and edits the
keymap. In the portal, cross the arranged edge to control the iPad;
**Ctrl+Alt+Q** bails out, **Ctrl+Alt+I** toggles manually.

## Setup

The Windows side is turnkey (pure standard-library Python; `pycaw` is an
optional extra for the volume slider). The VM is built with the provided
scripts — a few steps, all scripted:

1. **Create the VM:** `powershell -ExecutionPolicy Bypass -File create-vm.ps1
   -Iso <debian-12-netinst.iso>` — stands it up with the right hardware: xHCI
   USB passthrough, the NAT forwards (`2222→22`, `9955→9955`, UDP `4010→4010`),
   and a USB filter for your Bluetooth radio.
2. **Install Debian 12** into it (minimal — a sudo user + the SSH-server task).
3. **Provision it:** the app auto-generates its SSH key (`id_openspan`) on
   first launch; get its public half into the VM, then copy `guest/` in and
   run `sudo bash guest/provision.sh all` — this installs the packages, the
   BLE-HID + audio stacks, every config and systemd unit — and reboot.

`cold-test.ps1` automates the software half of steps 2–3 against a reachable
VM and verifies it (`verify-provision.sh`). The Windows→VM provisioning path is
**verified on a fresh Debian clone**; the Bluetooth radio + iPad pairing are
the hardware step you confirm once. `PROVISION_SPEC.md` and `TECHNICAL_NOTES.md`
document every piece.

**iPad pairing:** Bluetooth ▸ tap **OpenSpan Keyboard** ▸ accept the prompt.
It auto-reconnects after that. (`Settings ▸ General ▸ Keyboard ▸ Hardware
Keyboard` appears once bonded — a handy check.)

## Roadmap

- **Working & tested:** BLE keyboard + mouse, edge crossing, keymap remaps,
  Bluetooth audio routing (volume + balance), two-way clipboard, fast pairing,
  collapsible console + tray, auto-reconnect, single-file exe.
- **Reproducible VM:** `create-vm.ps1` builds the VM, `guest/provision.sh`
  turns a fresh Debian into the working bridge, and `cold-test.ps1` provisions
  + verifies it — the software path is verified on a fresh clone.
- **Known limits:** BLE sends *relative* mouse motion, so the pointer can drift
  (a corner-park re-sync is planned); touch-made copies on the iPad don't
  auto-sync to the clipboard (use Ctrl+C).

## License

MIT. No warranty, no data collection, nothing phones home.
