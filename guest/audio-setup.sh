#!/bin/bash
# Guest audio foundation: enable Classic BT (for A2DP to headphones) while
# keeping LE (for the iPad), and run PulseAudio in system mode with the
# Bluetooth modules so the VM can be an A2DP source.
set -e

echo "[audio] enabling BR/EDR (Classic) alongside LE"
btmgmt power off >/dev/null 2>&1 || true
btmgmt bredr on   >/dev/null 2>&1 || true
btmgmt le on      >/dev/null 2>&1 || true
btmgmt power on   >/dev/null 2>&1 || true
# keep the Classic side NON-discoverable (no iPad decoy); we connect OUT
# to the headphones, so we don't need to be Classic-discoverable
bluetoothctl discoverable off >/dev/null 2>&1 || true

echo "[audio] pulseaudio system-mode config"
# allow root + let the bluetooth policy/discover modules load
mkdir -p /etc/pulse
grep -q 'module-bluetooth-discover' /etc/pulse/system.pa 2>/dev/null || cat >> /etc/pulse/system.pa <<'PA'

### OpenSpan audio bridge
load-module module-bluetooth-policy
load-module module-bluetooth-discover
load-module module-native-protocol-unix
PA

echo "[audio] (re)starting pulseaudio --system"
pkill -9 pulseaudio 2>/dev/null || true
sleep 1
# system mode, headless, root ok
pulseaudio --system --disallow-exit --disallow-module-loading=0 \
    --exit-idle-time=-1 --daemonize=yes \
    --load="module-bluetooth-discover module-bluetooth-policy" 2>/dev/null || \
    pulseaudio --system --daemonize=yes 2>/dev/null || true
sleep 2

echo "[audio] state:"
pactl info 2>/dev/null | grep -E 'Server|Default' || echo "pactl not responding"
echo "--- bluetooth modules loaded? ---"
pactl list modules short 2>/dev/null | grep -i bluetooth || echo "(no bt modules yet)"
echo "--- controller settings ---"
btmgmt info | grep 'current settings'
