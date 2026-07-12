#!/bin/bash
# OpenSpan guest provisioner — turns a fresh Debian 12 install into the working
# bridge VM. Built against PROVISION_SPEC.md from a read-only audit of the
# working VM; each source file below is the verified-current one.
#
#   sudo bash provision.sh [stage]
#     stage = all (default) | 1 | 2 | 3 | 4
#
# Stages:
#   1  base + SSH     packages, linger user-bus, mask stock audio, /opt scripts, host key
#   2  BLE HID        kernel/USB, bluez main.conf, openspanble + agent + btready units
#   3  audio          wireplumber bluez config, pipewire stack + udp bridge units
#   4  coexistence    bring everything up together; recommend a validating reboot
#
# This script CONFIGURES. It never tests the radio — pairing the iPad and
# checking audio on real hardware is the operator's job after the reboot.
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

# --- STAGE 1 : base + SSH ---------------------------------------------------
stage1_base() {
  log "STAGE 1 — base + SSH"

  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y \
    bluez bluez-tools libspa-0.2-bluetooth pipewire pipewire-pulse \
    pipewire-audio-client-libraries wireplumber dbus-user-session \
    python3-dbus python3-gi openssh-server socat rfkill alsa-utils \
    pulseaudio-utils

  # persistent systemd USER bus for root (/run/user/0/bus) — the audio crux
  loginctl enable-linger root

  # mask the stock per-login audio so it can't contend for the endpoints
  systemctl --global mask \
    pipewire.service pipewire.socket \
    pipewire-pulse.service pipewire-pulse.socket \
    wireplumber.service

  # /opt/openspan runtime scripts (sources are the verified-current ones:
  # env.sh + wait-pw/wait-userbus come from rebuild/, the rest from guest/)
  install -d "$OPT"
  install -m 755 "$HERE/openspan_ble.py" "$OPT/openspan_ble.py"
  install -m 755 "$HERE/udp_to_sink.py"  "$OPT/udp_to_sink.py"
  for s in bt-connect.sh bt-list.sh btready.sh ensure-dualmode.sh \
           wait-hci0.sh install-authorized-key.sh; do
    install -m 755 "$HERE/$s" "$OPT/$s"
  done
  install -m 644 "$HERE/rebuild/env.sh"          "$OPT/env.sh"
  install -m 755 "$HERE/rebuild/wait-pw.sh"      "$OPT/wait-pw.sh"
  install -m 755 "$HERE/rebuild/wait-userbus.sh" "$OPT/wait-userbus.sh"

  # host SSH key so the Windows app can reach the VM. Stage id_openspan.pub
  # next to provision.sh; otherwise install it by hand later.
  if [ -f "$HERE/id_openspan.pub" ]; then
    bash "$OPT/install-authorized-key.sh" "$HERE/id_openspan.pub"
  else
    warn "id_openspan.pub not staged next to provision.sh."
    warn "  install it later:  $OPT/install-authorized-key.sh <id_openspan.pub>"
  fi
}

# --- STAGE 2 : BLE HID keyboard + mouse -------------------------------------
stage2_ble() {
  log "STAGE 2 — BLE HID keyboard + mouse"

  # radio must never idle-suspend: kernel cmdline + btusb module option
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

  # bluetooth adapter config (dual mode, LE 12/24, not-discoverable is set by
  # the daemon at runtime)
  install -m 644 "$HERE/system/bluetooth-main.conf" /etc/bluetooth/main.conf

  # BLE HID daemon + both drop-ins (10-wait.conf is the Broadcast-safe fix),
  # the persistent pairing agent, and the radio-ready boot helper
  install -m 644 "$HERE/system/openspanble.service" "$SD/openspanble.service"
  install -d "$SD/openspanble.service.d"
  install -m 644 "$HERE/system/openspanble.service.d/10-wait.conf" \
    "$SD/openspanble.service.d/10-wait.conf"
  install -m 644 "$HERE/system/openspanble.service.d/override.conf" \
    "$SD/openspanble.service.d/override.conf"
  install -m 644 "$HERE/openspan-agent.service"   "$SD/openspan-agent.service"
  install -m 644 "$HERE/openspan-btready.service" "$SD/openspan-btready.service"

  systemctl daemon-reload
  systemctl enable openspan-agent.service openspan-btready.service \
    openspanble.service
}

# --- STAGE 3 : audio --------------------------------------------------------
stage3_audio() {
  log "STAGE 3 — audio"

  # A2DP suspend + HFP config: an EDITED stock file (suspend-timeout=0,
  # pause-on-idle=false, with-logind=false, hfphsp-backend=none)
  install -d /usr/share/wireplumber/bluetooth.lua.d
  install -m 644 "$HERE/system/wireplumber-50-bluez-config.lua" \
    /usr/share/wireplumber/bluetooth.lua.d/50-bluez-config.lua

  # audio stack units (current /run/user/0 generation, from rebuild/)
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
  log "STAGE 4 — coexistence"
  systemctl daemon-reload
  # best-effort bring-up now; the real validation is a clean reboot (below),
  # which also establishes the linger user bus and applies the kernel cmdline
  for u in openspan-pipewire openspan-wireplumber openspan-pipewire-pulse \
           openspan-udprecv openspan-agent openspanble; do
    systemctl restart "$u.service" 2>/dev/null || \
      warn "$u didn't start now — a reboot should bring it up"
  done
  systemctl start openspan-btready.service 2>/dev/null || true

  log "provisioned. COLD-REBOOT the VM now to validate boot order + the"
  log "usbcore.autosuspend kernel cmdline, then (operator):"
  log "  1. iPad Bluetooth -> tap 'OpenSpan Keyboard' -> pair : the mouse moves"
  log "  2. connect earbuds : PC audio plays clean"
}

main() {
  require_root
  preflight
  case "${1:-all}" in
    1)   stage1_base ;;
    2)   stage2_ble ;;
    3)   stage3_audio ;;
    4)   stage4_coexist ;;
    all) stage1_base; stage2_ble; stage3_audio; stage4_coexist ;;
    *)   echo "usage: $0 [all|1|2|3|4]"; exit 1 ;;
  esac
  log "provision.sh done: stage ${1:-all}"
}

main "$@"
