#!/bin/bash
source /opt/openspan/env.sh
# wait up to ~2 min for the controller to register (btready is working on it)
for i in $(seq 30); do
  if ! btmgmt info 2>/dev/null | grep -q 'Index list with 0'; then
    break
  fi
  sleep 4
done
echo '=== CONTROLLER (want an address, not "0 items") ==='
btmgmt info 2>/dev/null | head -5
echo '=== btready helper ==='
systemctl is-active openspan-btready
echo '=== audio services ==='
systemctl is-active openspan-audio openspan-udprecv openspan-hold openspan-jbl
echo '=== pipewire ==='
wpctl status >/dev/null 2>&1 && echo PW-OK || echo PW-FAIL
echo '=== controller powered + name ==='
bluetoothctl show 2>&1 | grep -iE 'Powered|Alias' | head -2
