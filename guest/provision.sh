#!/bin/bash
# OpenSpan guest provisioner — turns a fresh Debian 12 install into the working
# bridge VM. Built stage by stage against PROVISION_SPEC.md; each stage is
# idempotent and independently cold-tested on real hardware before the next.
#
#   sudo bash provision.sh [stage]
#     stage = all (default) | 1 | 2 | 3 | 4
#
# Stages (detail in PROVISION_SPEC.md):
#   1  base + SSH     packages, linger user-bus, mask stock audio, host key
#   2  BLE HID        bluez main.conf, openspan_ble + units, agent, kernel/USB
#   3  audio          wireplumber bluez config, pipewire stack, udp bridge
#   4  coexistence    enable everything together, final wiring
#
# This script CONFIGURES. It never tests the radio — pairing the iPad and
# checking audio on real hardware is the operator's job after each stage.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OPT=/opt/openspan

log()  { printf '\n\033[1;32m== %s ==\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!! %s\033[0m\n'   "$*" >&2; }

require_root() {
  [ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }
}

# --- idempotency helpers (used as stages get filled in) ---------------------

# ensure_line FILE LINE : append LINE to FILE only if it isn't already present
ensure_line() {
  local f="$1" line="$2"
  touch "$f"
  grep -qxF "$line" "$f" || printf '%s\n' "$line" >> "$f"
}

# install_file SRC DEST MODE : copy only if changed, set mode
install_file() {
  local src="$1" dest="$2" mode="${3:-644}"
  install -D -m "$mode" "$src" "$dest"
}

# --- stages (bodies land in their own cold-tested passes) -------------------

stage1_base() {
  log "STAGE 1 — base + SSH"
  # TODO(stage1): PROVISION_SPEC §2 (apt packages), §3 (linger + mask stock
  #   audio), §6 (install /opt/openspan runtime scripts), §8 (host SSH key).
  #   Checkpoint: the Windows app SSHes in and 'echo ok' returns.
  warn "stage1 not yet implemented"
  return 0
}

stage2_ble() {
  log "STAGE 2 — BLE HID keyboard + mouse"
  # TODO(stage2): PROVISION_SPEC §1 (grub usbcore.autosuspend + btusb
  #   modprobe), §5 (/etc/bluetooth/main.conf), §7 (openspanble + drop-ins,
  #   openspan-agent, openspan-btready). Checkpoint: iPad pairs, mouse moves.
  warn "stage2 not yet implemented"
  return 0
}

stage3_audio() {
  log "STAGE 3 — audio"
  # TODO(stage3): PROVISION_SPEC §4 (modified 50-bluez-config.lua), §7
  #   (openspan-pipewire / wireplumber / pipewire-pulse / udprecv on the
  #   user@0 bus). Checkpoint: earbuds connect, PC audio plays clean.
  warn "stage3 not yet implemented"
  return 0
}

stage4_coexist() {
  log "STAGE 4 — coexistence"
  # TODO(stage4): enable all units together, verify boot order, final wiring.
  #   Checkpoint: keyboard + audio clean simultaneously; Broadcast doesn't
  #   drop the music.
  warn "stage4 not yet implemented"
  return 0
}

main() {
  require_root
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
