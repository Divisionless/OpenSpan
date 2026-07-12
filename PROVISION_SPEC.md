# OpenSpan — Provisioner Spec

Ground truth captured read-only from the working VM (2026-07-12). This is what
a blank Debian 12 install must become. Every value here was measured, not
inferred — sources noted where they'd otherwise be assumed. It's the checklist
2CE (the guest provisioner) transcribes.

Verified base: **Debian 12.14, kernel 6.1.0-50-amd64** (nocloud image).

---

## 1 · Kernel + USB — the radio must never idle-suspend

- grub: `usbcore.autosuspend=-1` on `GRUB_CMDLINE_LINUX`, then `update-grub`.
  **Needs a cold reboot to take** (confirmed live in `/proc/cmdline`).
- `/etc/modprobe.d/btusb-noautosuspend.conf` → `options btusb enable_autosuspend=0`
  (confirmed live).

## 2 · Packages (`apt-get install`)

Beyond the base image (measured via `apt-mark showmanual` + `dpkg -l`):

```
bluez bluez-tools libspa-0.2-bluetooth pipewire pipewire-pulse \
pipewire-audio-client-libraries wireplumber dbus-user-session \
python3-dbus python3-gi openssh-server socat rfkill alsa-utils \
pulseaudio-utils
```

- `bt-agent` comes from **bluez-tools**. `libspa-0.2-bluetooth` is the A2DP SPA
  plugin — audio silently won't work without it.
- Working versions: bluez 5.66, pipewire 0.3.65, wireplumber 0.4.13.
- `openssh-server` is already in the base here, but list it for a minimal image.

## 3 · The user-bus footing (the audio crux)

- `loginctl enable-linger root` → `/run/user/0` + `/run/user/0/bus` persist with
  no login. (Confirmed `Linger=yes`.)
- Mask the stock per-login audio so it can't contend for the endpoints:
  `systemctl --global mask pipewire pipewire-pulse wireplumber` (+ their
  `.socket`). (Confirmed all six symlinked to `/dev/null`.)

## 4 · WirePlumber A2DP config — **was missing from the repo; now captured**

The working audio depends on an **edited** stock file, not a custom drop-in.
`dpkg -V wireplumber` flags `/usr/share/wireplumber/bluetooth.lua.d/50-bluez-config.lua`
as modified. Ship `guest/system/wireplumber-50-bluez-config.lua`; the
load-bearing settings are:

- `["session.suspend-timeout-seconds"] = 0`  — disables A2DP idle-suspend
- `["node.pause-on-idle"] = false`
- `["with-logind"] = false`
- `["bluez5.hfphsp-backend"] = "none"`  — kills the HFP retry flood
- `["bluez5.auto-connect"] = "[ hfp_hf hsp_hs a2dp_sink ]"`

> Correction this audit forced: earlier docs claimed no suspend config existed
> and the silence feed in `udp_to_sink.py` was "the whole story." **Both exist.**
> The repo's `guest/52-no-hfp.lua` is **not deployed** — HFP is disabled inside
> this file instead. Drop `52-no-hfp.lua` from the repo.

## 5 · Bluetooth adapter (`/etc/bluetooth/main.conf`)

Ship `guest/system/bluetooth-main.conf` (confirmed live):
`ControllerMode=dual`, `Experimental=true`, `KernelExperimental=true`,
`Class=0x0005C0`, `[LE] MinConnectionInterval=12 / MaxConnectionInterval=24 /
ConnectionLatency=0 / ConnectionSupervisionTimeout=200`, `[Policy]
AutoEnable=true`.

## 6 · `/opt/openspan` runtime scripts

Install only the runtime set (the live box still has ~90 legacy dev scripts —
they are clutter, not dependencies):

- `openspan_ble.py` — BLE HID daemon
- `udp_to_sink.py` — audio bridge
- `bt-connect.sh`, `bt-list.sh` — app-invoked over ssh
- `btready.sh`, `env.sh`
- `ensure-dualmode.sh`, `wait-hci0.sh`, `wait-pw.sh`, `wait-userbus.sh`
- `install-authorized-key.sh`

## 7 · systemd units — install + enable exactly these seven

Confirmed enabled + running (`list-unit-files` + `list-units --running`):

- `openspan-agent` — `bt-agent -c NoInputNoOutput`
- `openspan-btready` — runs `btready.sh` *(fix: its unit has a stale
  `After=openspan-audio.service` pointing at a retired unit — drop it)*
- `openspan-pipewire`, `openspan-wireplumber`, `openspan-pipewire-pulse` — all
  pinned to `XDG_RUNTIME_DIR=/run/user/0` + the user@0 bus, gated on
  `wait-userbus.sh` / `wait-pw.sh`
- `openspan-udprecv` — runs `udp_to_sink.py`
- `openspanble` + **both drop-ins**: `10-wait.conf` (resets `ExecStartPre` to
  `wait-hci0.sh` + conditional `ensure-dualmode.sh` — the Broadcast-safe fix)
  and `override.conf` (`After=btready+wireplumber`, `Restart=always`)

**Do NOT install/enable** `openspan-audio`, `openspan-hold`, `openspan-jbl`,
`openspan-dbus` — confirmed retired (`is-enabled` → not-installed/disabled; the
first three live in `/opt/openspan/retired-units/`).

## 8 · SSH (host ↔ VM)

- `install-authorized-key.sh` drops `id_openspan.pub` into
  `/root/.ssh/authorized_keys` (dir `700`, file `600`).
- Debian's defaults already allow root-by-key: `sshd -T` shows
  `permitrootlogin without-password`, `pubkeyauthentication yes`. The script's
  sshd edits are belt-and-suspenders, not required.
- Bootstrap: a fresh install accepts password auth (`passwordauthentication yes`
  default), so the very first key install can go in over a password login.

## Open items the audit surfaced (fold into 2CE)

- `openspan_hid.py` — on disk and repo-kept, but `openspan_ble.py` doesn't
  import it and no *enabled* unit runs it. Likely legacy; verify before
  shipping.
- `audio-up.sh` + `keepalive-audio.sh` — only referenced by the retired
  `openspan-audio`/`hold` units. The 1B prune over-kept them; drop from the
  provisioner and the repo.
- `guest/52-no-hfp.lua` — unused (see §4); drop from the repo.

---

*Next: 2CE turns this into a staged provisioner (base → BLE → audio →
coexistence → cold-clone), each stage cold-tested on real hardware.*
