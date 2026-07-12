#!/bin/bash
# Apply the expert's fix: one media app + fresh SEID pool + dual mode.
set +e

echo "=== stop BLE daemon (its ExecStartPre does btmgmt bredr off) ==="
systemctl stop openspanble

echo "=== kill ALL media apps (only one may talk to bluez) ==="
pkill -9 pulseaudio 2>/dev/null; pkill -9 pipewire 2>/dev/null
pkill -9 wireplumber 2>/dev/null; pkill -9 bluealsa 2>/dev/null
sleep 1
pgrep -a 'pulseaudio|pipewire|wireplumber|bluealsa' && echo "STILL RUNNING ^" || echo "media apps: none"

echo "=== ControllerMode=dual (probe A2DP at adapter registration) ==="
sed -i 's/^ControllerMode = le/ControllerMode = dual/' /etc/bluetooth/main.conf
grep '^ControllerMode' /etc/bluetooth/main.conf

echo "=== foreign MediaEndpoint objects before restart? ==="
busctl --system tree org.bluez 2>/dev/null | grep -i MediaEndpoint || echo "(none)"

echo "=== restart bluetoothd (resets SEID pool, re-probes dual) ==="
systemctl restart bluetooth
sleep 3
hciconfig hci0 up 2>/dev/null
btmgmt info | grep 'current settings'

echo "=== start PulseAudio ONCE ==="
rm -f /var/run/pulse/native
pulseaudio --system --disallow-exit --disallow-module-loading \
    --exit-idle-time=-1 --daemonize=yes
sleep 4

echo "=== bluetoothd endpoint registration (the moment of truth) ==="
journalctl -u bluetooth --since '-25s' --no-pager | grep -iE 'endpoint|media|a2dp' | tail -8 || echo "(no endpoint log)"
echo "=== PA up + bluetooth module? ==="
export PULSE_SERVER=unix:/var/run/pulse/native
pactl info 2>&1 | grep 'Server Name' || echo "pactl NOT responding"
pactl list modules short 2>/dev/null | grep -i blue || echo "(no bt module)"
