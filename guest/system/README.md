# guest/system — captured LIVE system config (rebuild insurance)

Byte-for-byte captures from the running VM (2026-07-10). These are the pieces
of the working system that live OUTSIDE `/opt/openspan/` and were previously
not in the repo at all — including the hardest-won fix in the project
(`openspanble.service.d/10-wait.conf`).

| File here | Path in the VM |
|---|---|
| `openspanble.service` | `/etc/systemd/system/openspanble.service` |
| `openspanble.service.d/10-wait.conf` | `/etc/systemd/system/openspanble.service.d/10-wait.conf` — **the Broadcast-kills-audio fix**: resets ExecStartPre to the CONDITIONAL `ensure-dualmode.sh` so a keyboard restart never power-cycles the radio |
| `openspanble.service.d/override.conf` | `/etc/systemd/system/openspanble.service.d/override.conf` |
| `bluetooth-main.conf` | `/etc/bluetooth/main.conf` — LE interval 12/24 (15–30 ms), ControllerMode=dual, Class=0x0005C0 |
| `btusb-noautosuspend.conf` | `/etc/modprobe.d/btusb-noautosuspend.conf` |
| `grub-cmdline.txt` | the `GRUB_CMDLINE_LINUX*` lines from `/etc/default/grub` (`usbcore.autosuspend=-1`; needs `update-grub` + a COLD boot to apply) |

The current-generation *audio* units + deploy script live in `../rebuild/`.
The effective source of truth is always the VM — verify with
`systemctl cat <unit>` (drop-ins can reset ExecStartPre lists; that exact
override was the Broadcast bug).

NOTE (verified 2026-07-10): there is NO WirePlumber bluetooth config in the
VM (`/usr/share/wireplumber/bluetooth.lua.d/` does not exist; no
suspend-timeout / pause-on-idle / with-logind anywhere). What prevents the
A2DP idle-suspend drop is the bridge's continuous silence feed in
`udp_to_sink.py` — do not remove that feed.
