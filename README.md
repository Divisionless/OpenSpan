# OpenSpan

**Control your iPad with your PC's keyboard and mouse — free, open source, no cloud, no bloat.**

OpenSpan turns your Windows PC into a Bluetooth keyboard and mouse for a
nearby iPad. Shove your mouse off a screen edge (Input Director / Universal
Control style) and your keyboard + mouse drive iPadOS directly — type in
iMessage, scroll Safari, switch apps. Cross back and you're on the PC again.

No paid software (it replaces "across"), no account, no telemetry. Just
standard-library Python and a tiny Linux VM doing the one thing Windows
forbids: pretending to be a Bluetooth keyboard.

---

## Why a VM?

Windows deliberately blocks applications from publishing the Bluetooth HID
(keyboard/mouse) service — the OS reserves it. Linux's BlueZ does not. So
OpenSpan runs a ~300 MB headless Debian VM that owns the PC's Bluetooth
radio (via USB passthrough) and advertises as a keyboard. A small Windows
app captures your real input and streams it to the VM.

```
your keyboard/mouse
      │  (low-level hooks)
windows: openspan_portal.py ── TCP :9955 ──▶ Debian VM: openspan_ble.py
                                                   │  BlueZ GATT (BLE HID)
                                                   ▼
                                                 iPad  (bonded BLE keyboard)
```

## Hard-won facts (read before you "fix" something)

These cost real debugging; they are the difference between working and not:

1. **BLE, not Classic.** Classic Bluetooth (BR/EDR) HID emulation *pairs*
   with an iPad but the iPad never accepts the keystrokes. Apple only
   cooperates over **BLE HID (HID-over-GATT / HOGP)**. OpenSpan is BLE.
2. **OHCI, not xHCI.** VirtualBox's USB 3 (xHCI) emulation corrupts the
   full-speed Intel Bluetooth controller under load — HCI commands time
   out and the stack wedges. The VM must use **USB 1.1/2.0 (OHCI+EHCI)**:
   `VBoxManage modifyvm OpenSpan --usbxhci off --usbehci on --usbohci on`.
3. **`encrypt-read` forces the bond.** iOS will connect to a BLE keyboard
   and even subscribe to notifications *without bonding*, then silently
   ignore every keystroke. Marking the HID report characteristics
   `encrypt-read` forces iOS to bond, which activates the keyboard.
   (`Settings ▸ General ▸ Keyboard ▸ Hardware Keyboard` appears only once
   bonded — a handy check.)
4. **LE-only kills the duplicate.** A dual-mode adapter shows up twice on
   the iPad. `ControllerMode = le` in `main.conf` leaves one clean entry.

## Layout

```
openspan/
├── guest/                     # runs inside the Debian VM
│   ├── openspan_ble.py        # BLE HID GATT peripheral + :9955 command server
│   ├── openspanble.service    # systemd unit
│   └── provision.sh           # one-shot VM setup (idempotent)
├── win/                       # runs on Windows
│   ├── openspan.py            # control panel (start here)
│   ├── openspan_portal.py     # edge-crossing input router
│   └── openspan_setup.py      # drag-to-arrange the iPad among your monitors
├── openspan_config.json       # your screen arrangement (from setup)
├── openspan_keymap.json       # editable key remaps (Alt→Cmd, Ctrl+C→Cmd+C…)
├── LICENSE                    # MIT
└── README.md
```

## Setup (host)

1. Install [VirtualBox](https://www.virtualbox.org) + Extension Pack.
2. Create a Debian 12 VM named `OpenSpan`, **USB set to OHCI+EHCI** (not
   xHCI), NAT port-forwards `2222→22` and `9955→9955`, and a USB filter for
   the Intel Bluetooth controller (`8087:0aaa` — adjust to your radio).
3. Boot it, copy `guest/` in, run `sudo bash guest/provision.sh`.

## Setup (iPad)

Bluetooth ▸ tap **OpenSpan Keyboard** ▸ **accept the pairing prompt**. Done —
it auto-reconnects after that.

## Daily use

Run `python win/openspan.py` (the control panel). From it:

- **Start bridge VM** / **Start input portal**
- **Arrange iPad** — drag the iPad against the monitor edge it sits next to
- **Edit keymap** — opens `openspan_keymap.json`
- **Give radio to iPad / Windows** — share the Bluetooth radio with your
  headphones when you're not bridging

In the portal: cross the arranged edge to control the iPad. **Ctrl+Alt+Q**
bails out; **Ctrl+Alt+I** toggles manually.

## Keymap

`openspan_keymap.json` — iPadOS uses **Cmd** for system shortcuts:

```json
{
  "modifier_remap": { "alt": "cmd" },
  "overrides": [
    { "from": ["ctrl","c"], "to": ["cmd","c"], "note": "Copy" }
  ]
}
```

`alt→cmd` makes **Alt+Tab** the iPad app switcher (hold Alt, tap Tab to
cycle). Add any `from → to` you like.

## Status & limits

Working: pairing, live keyboard, live mouse, edge crossing, remaps.
Rough edges: BLE sends *relative* mouse motion, so the iPad pointer can
drift from where you expect (a corner-park re-sync is planned); mouse
sensitivity (`MOUSE_SENS`) and the exit distance may want tuning.

## License

MIT. No warranty, no data collection, nothing phones home.
