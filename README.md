# OpenSpan

**Drive your iPad or managed Mac from your PC — keyboard and mouse over
Bluetooth, with iPad audio/clipboard support. Free, local, no cloud, no
account.**

OpenSpan turns a Windows PC into a Bluetooth peripheral for a nearby iPad.
Shove your mouse off a screen edge (Universal Control / Input Director style)
and your keyboard and mouse drive iPadOS directly — type in Messages, scroll
Safari, switch apps. Cross back and you're on the PC again. On the same
Bluetooth radio it also routes your PC audio to Bluetooth earbuds and keeps a
two-way clipboard in sync between the two devices.

No paid software, no account, no telemetry. A small Windows app plus a headless
Linux VM doing the one thing Windows won't: pretending to be a Bluetooth
keyboard. Multi-radio mode can publish a second, independent Bluetooth HID
device for a managed Mac without installing software on that Mac.

---

## Run it as administrator

**If you run *any* application as administrator, you must run OpenSpan as
administrator too.**

Windows UIPI does not deliver low-level input hooks to a lower-privilege
process while an **elevated window has focus**. OpenSpan's edge crossing is
built on those hooks — so if, say, you keep an admin terminal open, the moment
that window has focus the mouse simply **stops crossing to the iPad**. There is
no error, no exception, nothing in any log, and `SetWindowsHookEx` still reports
success. Re-installing the hook does not help. Only closing the elevated window
(or restarting OpenSpan, which steals focus back) appears to "fix" it.

It looks exactly like a bug in OpenSpan. It isn't. EsotericOS therefore treats
administrator integrity as a launch invariant, not a user choice. A
non-elevated bootstrap immediately requests an elevated copy and exits before
keys, Bluetooth, audio, or the VM are touched; there is no normal-mode GUI.
Automatic sign-in uses the verified `EsotericOS App (elevated)` scheduled task
at RunLevel Highest rather than an HKCU Run entry.

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

Windows: the same portal ───── TCP :9956 ──▶ second HID daemon/radio ─▶ Mac

PC audio ─▶ VB-Cable ─▶ WASAPI loopback ── UDP :4010 ──▶ VM: PipeWire ─▶ A2DP ─▶ earbuds

this PC  ── DNS-SD advert (OS) ──▶ LAN ──▶ another PC running EsotericOS
         ◀── TCP, OS-assigned port, HMAC-signed ──▶   (a "LAN node")
```

## Ports

Bluetooth lanes use fixed ports because the guest VM's NAT rules have to name
them. **LAN nodes deliberately do not.**

| Port | What | Chosen by |
|---|---|---|
| 9955 | first device's BLE HID daemon, in the VM | us (NAT rule; +1 per device) |
| 9956 | second device's daemon (managed Mac lane) | us (NAT rule) |
| 4010 | UDP audio, Windows → VM PipeWire | us (NAT rule) |
| 5353 | mDNS, **only** on the fallback discovery path | RFC 6762 |
| *ephemeral* | a LAN node's pairing + service port | **the OS**, every launch |

A LAN node binds port `0` and reads back whatever Windows hands it, then
advertises that number. Nothing connects to a port the source code knows, so
there is no number to collide with on somebody else's machine — and this is
why the firewall rule allows the **program** and never a port: a port rule
would be stale by the next restart. `tools\app-firewall.ps1` owns that exact
program identity for the install path. Re-running installation after an update
refreshes a renamed executable and removes the broad per-build rules Windows'
first-listen prompt would otherwise accumulate.

Discovery is the OS's own DNS-SD (`dnsapi.dll`, Windows 10 1809+), registering
`_esotericos._tcp.local`. That is the protocol Bonjour speaks, so a Mac or iPad
can find a node later without inventing anything. Where the API is missing, a
minimal RFC 6762 mDNS responder on 224.0.0.251:5353 takes over. The Console
names which path is live at startup.

## LAN nodes (one desk across two PCs)

Bluetooth exists here for machines that **resist installation** — a managed
Mac, an iPad. A Windows PC you own gets a better lane: TCP over the LAN.

Drop the portable folder (`install\make-portable.ps1 -Zip`) on the other PC,
run `bake-in.ps1` as administrator once, then launch it. Under **Devices ▸
Nodes on this network** each machine sees the other;
press **Pair**, compare the six-digit code shown on both screens, and press
**Same code** on **both** — one side alone pairs nothing. From then on every
message between them is HMAC-signed with a secret neither machine transmitted.

Identity is a **32-byte key**, not an address. Rename the PC, change networks,
get a new IP: the pairing survives, because nothing about it is positional. A
paired node becomes an ordinary device on the desk (`kind: "node"`, `lane:
"lan"`), holding one placeholder screen until desk federation lands.

If this PC has no VirtualBox or no guest VM, that is not an error — it is a
**LAN node**, it says so once, and nothing polls a VM that is not there.

The single radio runs both jobs concurrently: BLE HID peripheral service to the
iPad and an A2DP central connection to the earbuds. Those are independent
BlueZ device/profile lifecycles even when they share one controller. Preparing,
pairing, connecting, resetting, or disconnecting the iPad lane must never
disconnect audio. Keeping the BLE link's airtime modest is what lets both links
stay clean — see `TECHNICAL_NOTES.md`.

**A radio is owned by the device that claims it, and there is no mode to set.**
Right-click a paired device ▸ **Assign to radio** and pick from the controllers
actually present; a radio another device holds is shown but not selectable, and
says who holds it. The claim lasts until that device is forgotten, which
releases it. Assignments are stored by controller MAC address so they survive
`hci0`/`hci1` renumbering, and the arrangement is derived from those claims —
one controller behaves as single-radio, several as multi, with nothing to toggle
and therefore no toggle that can go stale against reality. Different radios add
airtime and hardware-fault isolation; they are not required for
connection-lifecycle isolation. Pairing the iPad leaves headphone audio alone
in either arrangement.

Repair, custody and layout controls are **fault remedies, not settings**: they
appear above the device list only when an audit finds something wrong — a
capture that did not land, a driver Windows Update re-bound, three radios not
laid out on three lanes — each carrying the one action that fixes it. A healthy
desk shows none of them.

With three radios, the recommended layout is the internal controller for the
iPad compatibility/backup lane, one external TP-Link controller for the
managed Mac, and the other external TP-Link controller for audio/scanning.
The iPad and Mac daemons have separate ports, advertisements, GATT state, and
bonds. Existing VM clones are upgraded in place: the app adds the Mac daemon's
TCP `9956` forwarding rule automatically without restarting the VM.

The current three-radio development bench uses
`configure-multiradio.ps1` once, while `OpenSpan-Codex` is powered off, to add
the two serial-specific TP-Link USB filters alongside the existing Intel
filter. The helper does not restart Windows or power off the VM.

## Features

- **Keyboard + mouse bridge** — cross a screen edge to control the iPad; a
  keymap remaps modifiers (Alt→Cmd, Ctrl+C→Cmd+C, …).
- **Managed Mac lane** — a second independent BLE keyboard/mouse target on its
  own radio. Its display editor supports 1–8 screens, manual resolution,
  refresh rate, 0°/90°/180°/270° rotation, and physical-size layout.
- **Physical desk canvas** — drag the iPad and Mac screens where they sit on
  your desk; this PC moves as one block, its own screens laid out exactly as
  Windows has them. Every screen is drawn to real physical scale: PC monitors
  read their size from the panel's EDID (a typed diagonal overrides it), Mac
  and iPad screens take a typed diagonal. **Identify** flashes a number on each
  real PC monitor, matching the number on its rectangle. Windows **PRIMARY**
  and the EsotericOS **DESKTOP** are independent marks on each PC screen; use a
  screen's right-click menu to move the built-in EsotericOS surface without
  changing Windows' primary monitor. The Desktop choice follows the physical
  panel if Windows renumbers it. The EsotericOS GUI, shell top bar, desktop icon
  field, and single dock travel together as that one Desktop role; the Windows
  primary remains independent. Optional multi-monitor dock mode still places a
  dock on every screen. Screens snap edge to edge and are never allowed to overlap.
- **Bluetooth audio routing** — send Windows audio to BT earbuds through the
  same radio, with the normal Windows volume slider and an in-app L/R balance.
- **Two-way clipboard** — plain **Ctrl+C / Ctrl+V** keep both machines in sync
  (via Apple Shortcuts + a tiny token-guarded LAN relay). See
  `CLIPBOARD_SETUP.md`.
- **Single scrolling control page** — Desk, Devices, Bluetooth, System and
  Console are one ordered vertical document with **one scrollbar, and only one**.
  Every container grows to fit its contents, so the wheel always scrolls the page:
  no device list, log or panel inside it ever captures your scroll. The readiness
  header stays pinned; **Console** jumps to the log without hiding any section.
  That single page scrollbar carries the dark EsotericOS style and never falls
  back to Windows' light native track. Where a container cannot grow forever the
  *content* is bounded instead — the Console keeps its most recent lines.
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
5. **Multiple radios use stable controller addresses.** Multi-radio mode is
   opt-in; the default remains the original single-radio path. Assignments are
   saved by controller MAC, resolved to the current Linux `hciN` on every app
   start, and the selected HID controller receives the same 15–30 ms interval.
   RTL8761BU/TP-Link radios also need Debian's `firmware-realtek` package.

## Layout

```
openspan/
├── win/                         # runs on Windows (stdlib Python + ctypes)
│   ├── openspan.py              # control app — start here
│   ├── openspan_portal.py       # edge-crossing keyboard/mouse router
│   ├── openspan_targets.py      # multi-target display geometry + migration
│   ├── win_audio_send.py        # WASAPI loopback → UDP audio sender
│   ├── openspan_setup.py        # drag-to-arrange the iPad among your monitors
│   └── openspan_launcher.py     # role dispatch for the packaged exe
├── guest/                       # runs inside the Debian VM
│   ├── openspan_ble.py          # BLE HID GATT peripheral + :9955 command server
│   ├── set-hid-target.sh        # independent iPad/Mac controller assignment
│   ├── udp_to_sink.py           # UDP audio → PipeWire A2DP bridge
│   ├── *.service                # systemd units (BLE daemon, audio stack, agent)
│   ├── bt-list.sh / bt-connect.sh / btready.sh / env.sh …   # runtime helpers
│   ├── system/                  # captured host config (main.conf, drop-ins, grub…)
│   └── rebuild/                 # audio-stack (PipeWire/WirePlumber) install set
│   └── lan_nodes.py             # LAN node identity, DNS-SD/mDNS, pairing
├── install/                     # what goes on the SECOND machine
│   ├── make-portable.ps1        # assemble EsotericOS-portable\ (+ -Zip)
│   └── README-PORTABLE.md       # the whole manual for a fresh node
├── build_exe.py                 # package into a single OpenSpan.exe
├── TECHNICAL_NOTES.md           # deep "what makes it work" reference
├── BUILD.md · CLIPBOARD_SETUP.md · CLIPBOARD_DESIGN.md
├── LICENSE                      # AGPL-3.0-or-later
├── NOTICE                       # copyright + why AGPL, third-party terms
└── README.md
```

## Running it (Windows side)

Launch `OpenSpan.exe` (build it with `python build_exe.py`) or `OpenSpan.bat`.
From plain source: `python win/openspan.py` — note Windows' unsigned-app
reputation gate may block a raw `pythonw.exe`; `BUILD.md` explains the packaged
exe and the interpreter workaround. The control app starts/stops the bridge VM
and the input portal, arranges the iPad among your monitors, and edits the
keymap. In the portal, cross the arranged edge to control the iPad;
**Esc pressed 3× in a row** is the panic bail — it always returns the mouse to the PC, even if the bridge is broken. (**Ctrl+Alt+Q** still works as a backup; **Ctrl+Alt+I** toggles manually.)

### Surface mode vs window mode

The app asks **once at startup** who the session's shell is (`win/surface_mode.py`)
and is one of two things for the rest of its life:

- **Surface mode** — `EsotericOS.Shell.exe` is the shell. During the
  one-release identity bridge, the legacy `CairoDesktop.exe` alias is accepted
  as the same shell. The app is part of the desktop surface: **no minimize
  button, no X, and WM_CLOSE is refused**, so Alt+F4 and the taskbar's Close do
  nothing but log a line. Sign-out,
  restart and shutdown are unaffected — Windows ends a session with
  `WM_QUERYENDSESSION`, which never reaches that handler.
- **Window mode** — Explorer is the shell (the deliberate debugging visit).
  Exactly the behaviour it has always had: minimize, X, the close dialog.

The detection is a **live probe, not the registry**: the process owning
`GetShellWindow()`, then either recognized EsotericOS Shell image, and only
then the Winlogon `Shell` value. The registry says what will start at *next*
sign-in, and the one
moment the two disagree is the debugging visit itself. Anything uncertain
resolves to **window mode** — the direction that stays closeable.

**The escape hatch is `--window`**: `EsotericOS.exe --window` forces window mode
whatever the shell is, and it survives the elevation relaunch. (`--surface`
forces the other way, for testing.) A flag always beats the probe.

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
  single-page controls, persistent Console, auto-reconnect, single-file exe.
- **Reproducible VM:** `create-vm.ps1` builds the VM, `guest/provision.sh`
  turns a fresh Debian into the working bridge, and `cold-test.ps1` provisions
  + verifies it — the software path is verified on a fresh clone.
- **Known limits:** BLE sends *relative* mouse motion, so the pointer can drift
  (a corner-park re-sync is planned); touch-made copies on the iPad don't
  auto-sync to the clipboard (use Ctrl+C).

## License

**AGPL-3.0-or-later** (relicensed from MIT on 2026-08-16). No warranty, no data
collection, nothing phones home. Strong copyleft is deliberate: EsotericOS runs
over the network (LAN nodes, clipboard relay, input portal), and the Affero
clause keeps a networked, modified version from being served to users without
its source. Replace proprietary with copyleft, and make the free solution the
one everyone wants. See `LICENSE` and `NOTICE`; third-party components keep their
own terms (dependency-licence audit tracked on the board).

**[POSITION_MODEL.md](POSITION_MODEL.md)** — the four laws behind moving one pointer across several machines over a link that can only move it *by* an amount, never *to* a place.
