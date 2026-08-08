#!/bin/bash
# OpenSpan guest provisioner - turns a fresh Debian 12 install into the bridge
# VM used by the current templated multi-device topology.
#
#   sudo bash provision.sh [stage]
#     stage = all (default) | 1 | 2 | 3 | 4
#
# Stages:
#   1  base + SSH     packages, linger user-bus, /opt scripts, host key
#   2  BLE HID        kernel/USB, BlueZ, lane template, agent, boot helper
#   3  audio          WirePlumber config, PipeWire stack, UDP bridge
#   4  coexistence    bring up non-radio services; recommend a cold reboot
#
# This script installs the generic lane machinery. It cannot invent controller
# MACs, ports, or device names: after radios are attached, the app (or
# set-hid-device.sh) creates one openspanble@<id> instance per device.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OPT=/opt/openspan
SD=/etc/systemd/system

log()  { printf '\n\033[1;32m== %s ==\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!! %s\033[0m\n'   "$*" >&2; }

require_root() { [ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }; }

preflight() {
  command -v apt-get >/dev/null || { echo "not a Debian/apt system"; exit 1; }
  command -v systemctl >/dev/null || { echo "systemd required"; exit 1; }
}

refuse_legacy_in_place() {
  # This is a fresh-clone provisioner, not an in-place topology migrator.
  # Silently stopping an active fixed lane would turn a working production VM
  # into an unconfigured one. Refuse before an `all` run changes any package or
  # runtime file; stage 2 also calls this when it is run on its own.
  legacy_in_use=0
  for unit in openspanble.service openspanble-mac.service; do
    systemctl is-active --quiet "$unit" && legacy_in_use=1
    systemctl is-enabled --quiet "$unit" && legacy_in_use=1
  done
  for conf in "$SD/openspanble.service.d/20-radio.conf" \
              "$SD/openspanble-mac.service.d/20-radio.conf"; do
    [ ! -e "$conf" ] || legacy_in_use=1
  done
  if [ "$legacy_in_use" -ne 0 ]; then
    echo "legacy fixed HID lane detected; refusing an in-place migration" >&2
    echo "provision a separate fresh VM or migrate the lane through OpenSpan" >&2
    exit 1
  fi
}

# --- STAGE 1 : base + SSH ---------------------------------------------------
stage1_base() {
  log "STAGE 1 - base + SSH"

  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y \
    bluez bluez-tools libspa-0.2-bluetooth pipewire pipewire-pulse \
    pipewire-audio-client-libraries wireplumber dbus-user-session \
    python3-dbus python3-gi openssh-server socat rfkill alsa-utils \
    pulseaudio-utils firmware-realtek

  # Persistent systemd user bus for root (/run/user/0/bus): the audio footing.
  loginctl enable-linger root

  # Mask the stock per-login audio so it cannot contend for the endpoints.
  systemctl --global mask \
    pipewire.service pipewire.socket \
    pipewire-pulse.service pipewire-pulse.socket \
    wireplumber.service

  # Runtime scripts. The current HID path is generic: bt-preflight protects
  # every radio operation and set-hid-device owns each per-device drop-in.
  install -d "$OPT"
  install -m 755 "$HERE/openspan_ble.py" "$OPT/openspan_ble.py"
  install -m 755 "$HERE/udp_to_sink.py"  "$OPT/udp_to_sink.py"
  for s in bt-connect.sh bt-list.sh btready.sh openspan_bt.py \
           set-hid-device.sh set-hid-radio.sh set-hid-target.sh \
           bt-preflight.sh ensure-dualmode.sh wait-hci0.sh \
           start-ble-lane.sh install-authorized-key.sh; do
    install -m 755 "$HERE/$s" "$OPT/$s"
  done
  install -m 644 "$HERE/rebuild/env.sh"          "$OPT/env.sh"
  install -m 755 "$HERE/rebuild/wait-pw.sh"      "$OPT/wait-pw.sh"
  install -m 755 "$HERE/rebuild/wait-userbus.sh" "$OPT/wait-userbus.sh"

  # The current Windows Bluetooth panel still exposes its older iPad/Mac radio
  # selectors. Keep their helpers available until that UI is retired, but keep
  # the corresponding fixed units disabled below. New device setup uses only
  # set-hid-device.sh and openspanble@<id>.

  # Host SSH key so the Windows app can reach the VM. Stage id_openspan.pub
  # next to provision.sh; otherwise install it by hand later.
  if [ -f "$HERE/id_openspan.pub" ]; then
    bash "$OPT/install-authorized-key.sh" "$HERE/id_openspan.pub"
  else
    warn "id_openspan.pub not staged next to provision.sh."
    warn "  install it later: $OPT/install-authorized-key.sh <id_openspan.pub>"
  fi
}

# --- STAGE 2 : BLE HID keyboard + mouse ------------------------------------
stage2_ble() {
  log "STAGE 2 - templated BLE HID lanes"
  refuse_legacy_in_place

  # Radios must never idle-suspend: kernel cmdline + btusb module option.
  if grep -q '^GRUB_CMDLINE_LINUX=' /etc/default/grub; then
    if ! grep -q 'usbcore.autosuspend=-1' /etc/default/grub; then
      sed -i 's/^\(GRUB_CMDLINE_LINUX="[^"]*\)"/\1 usbcore.autosuspend=-1"/' \
        /etc/default/grub
      update-grub
    fi
  else
    echo 'GRUB_CMDLINE_LINUX="usbcore.autosuspend=-1"' >> /etc/default/grub
    update-grub
  fi
  install -m 644 "$HERE/system/btusb-noautosuspend.conf" \
    /etc/modprobe.d/btusb-noautosuspend.conf

  install -m 644 "$HERE/system/bluetooth-main.conf" /etc/bluetooth/main.conf

  # One unit template serves every device. No naked template is enabled: a
  # lane becomes valid only after set-hid-device.sh writes 20-device.conf.
  install -m 644 "$HERE/system/openspanble@.service" "$SD/openspanble@.service"
  install -m 644 "$HERE/openspan-agent.service" "$SD/openspan-agent.service"
  install -m 644 "$HERE/openspan-btready.service" "$SD/openspan-btready.service"

  # Disabled compatibility definitions for the still-visible legacy radio
  # controls. They are deliberately NOT enabled and have no 20-radio claim,
  # so they cannot contend with a templated lane during a normal fresh boot.
  install -m 644 "$HERE/system/openspanble.service" "$SD/openspanble.service"
  install -m 644 "$HERE/system/openspanble-mac.service" \
    "$SD/openspanble-mac.service"
  install -d "$SD/openspanble.service.d"
  install -m 644 "$HERE/system/openspanble.service.d/10-wait.conf" \
    "$SD/openspanble.service.d/10-wait.conf"
  install -m 644 "$HERE/system/openspanble.service.d/override.conf" \
    "$SD/openspanble.service.d/override.conf"

  systemctl daemon-reload
  systemctl enable openspan-agent.service openspan-btready.service
}

# --- STAGE 3 : audio --------------------------------------------------------
stage3_audio() {
  log "STAGE 3 - audio"

  install -d /usr/share/wireplumber/bluetooth.lua.d
  install -m 644 "$HERE/system/wireplumber-50-bluez-config.lua" \
    /usr/share/wireplumber/bluetooth.lua.d/50-bluez-config.lua

  for u in openspan-pipewire openspan-wireplumber openspan-pipewire-pulse \
           openspan-udprecv; do
    install -m 644 "$HERE/rebuild/$u.service" "$SD/$u.service"
  done

  systemctl daemon-reload
  systemctl enable openspan-pipewire.service openspan-wireplumber.service \
    openspan-pipewire-pulse.service openspan-udprecv.service
}

# --- STAGE 4 : coexistence --------------------------------------------------
stage4_coexist() {
  log "STAGE 4 - coexistence"
  systemctl daemon-reload
  # Establish the lingering user bus now so the audio stack can come up without
  # waiting for a reboot.
  systemctl start user@0.service 2>/dev/null || true
  sleep 2
  # Start only non-radio units. openspan-btready and configured openspanble@*
  # lanes wait for USB hardware and are exercised after the cold reboot.
  for u in openspan-pipewire openspan-wireplumber openspan-pipewire-pulse \
           openspan-udprecv openspan-agent; do
    systemctl restart "$u.service" 2>/dev/null || \
      warn "$u did not start now; the reboot should bring it up"
  done

  log "provisioned. COLD-REBOOT the VM to apply the kernel cmdline, then:"
  log "  1. attach one controller per HID device, plus the audio controller"
  log "  2. assign each device in the app (or with set-hid-device.sh)"
  log "  3. pair each advertised OpenSpan device and test keyboard + mouse"
  log "  4. connect the audio device and confirm clean playback"
}

main() {
  require_root
  preflight
  case "${1:-all}" in
    1)   stage1_base ;;
    2)   stage2_ble ;;
    3)   stage3_audio ;;
    4)   stage4_coexist ;;
    all) refuse_legacy_in_place; stage1_base; stage2_ble; stage3_audio;
         stage4_coexist ;;
    *)   echo "usage: $0 [all|1|2|3|4]"; exit 1 ;;
  esac
  log "provision.sh done: stage ${1:-all}"
}

main "$@"
