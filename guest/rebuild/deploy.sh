#!/bin/bash
# Move the OpenSpan audio stack onto the persistent systemd user bus
# (/run/user/0/bus via linger) and retire the hand-rolled openspan-dbus.
# Idempotent; verifies each layer.
set -u
SD=/etc/systemd/system

echo '=== 0. sanity: real user bus present ==='
[ -S /run/user/0/bus ] || { echo 'FAIL: /run/user/0/bus missing'; exit 1; }
echo "ok: $(ls -la /run/user/0/bus)"

echo '=== 1. install helpers + env ==='
install -m 755 /tmp/rebuild/wait-userbus.sh /opt/openspan/wait-userbus.sh
install -m 755 /tmp/rebuild/wait-pw.sh       /opt/openspan/wait-pw.sh
cp /tmp/rebuild/env.sh /opt/openspan/env.sh

echo '=== 2. install rewritten units ==='
for u in openspan-pipewire openspan-wireplumber openspan-pipewire-pulse openspan-udprecv; do
  cp "/tmp/rebuild/$u.service" "$SD/$u.service"
  echo "  installed $u.service"
done

echo '=== 3. retire hand-rolled session bus ==='
systemctl disable --now openspan-dbus 2>/dev/null
echo "  openspan-dbus: $(systemctl is-active openspan-dbus 2>/dev/null)"

echo '=== 4. reload + (re)start the stack in dependency order ==='
systemctl daemon-reload
systemctl reset-failed openspan-pipewire openspan-wireplumber openspan-pipewire-pulse openspan-udprecv 2>/dev/null
systemctl restart openspan-pipewire;       sleep 2
systemctl restart openspan-wireplumber;    sleep 3
systemctl restart openspan-pipewire-pulse
systemctl restart openspan-udprecv;        sleep 3

echo '=== 5. verify ==='
for s in openspan-pipewire openspan-wireplumber openspan-pipewire-pulse openspan-udprecv openspan-agent; do
  printf '  %-26s %s\n' "$s" "$(systemctl is-active "$s" 2>/dev/null)"
done
echo -n '  pipewire socket: '; ls /run/user/0/pipewire-0 2>&1
echo -n '  A2DP endpoints registered: '
journalctl -u bluetooth --no-pager --since '25 sec ago' 2>/dev/null | grep -c 'Endpoint registered'
echo '  --- wireplumber bus/connect errors (want none) ---'
journalctl -u openspan-wireplumber --no-pager --since '25 sec ago' 2>/dev/null \
  | grep -iE 'error|fail|could not connect|no such file' | grep -viE 'libcamera' | tail -5 || true
echo '  --- wpctl sees the graph? ---'
XDG_RUNTIME_DIR=/run/user/0 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/0/bus \
  wpctl status 2>&1 | sed -n '1,14p'
