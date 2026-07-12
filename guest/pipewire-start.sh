#!/bin/bash
# Start the PipeWire stack headless as root (needs XDG_RUNTIME_DIR + a
# session D-Bus). For the first working iteration; systemd-ify later.
export XDG_RUNTIME_DIR=/run/user/0
mkdir -p "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"

pkill -9 pipewire 2>/dev/null; pkill -9 wireplumber 2>/dev/null
pkill -9 pulseaudio 2>/dev/null
sleep 1

# session bus
rm -f "$XDG_RUNTIME_DIR/bus"
dbus-daemon --session --address="unix:path=$XDG_RUNTIME_DIR/bus" --fork
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"

# stack, detached, logged
setsid pipewire        >/var/log/pw.log     2>&1 < /dev/null &
sleep 1
setsid pipewire-pulse  >/var/log/pwpulse.log 2>&1 < /dev/null &
sleep 1
setsid wireplumber     >/var/log/wp.log     2>&1 < /dev/null &
sleep 4

echo "=== processes ==="
pgrep -a 'pipewire|wireplumber' | head
echo "=== wpctl status ==="
wpctl status 2>&1 | head -35
