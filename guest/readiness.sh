#!/bin/bash
source /opt/openspan/env.sh
# wait up to ~90s for the controller (btready brings it up after the ~62s init)
for i in $(seq 45); do
  btmgmt info 2>/dev/null | grep -q 'Index list with 1' && break
  sleep 2
done
echo '=== controller ==='
btmgmt info 2>/dev/null | grep -q 'Index list with 1' && echo 'UP' || echo 'DOWN'
bluetoothctl show 2>&1 | grep -iE 'Powered|Discovering'
echo '=== services ==='
systemctl is-active openspan-agent openspan-audio openspan-udprecv openspan-hold
echo '=== agent registered? ==='
journalctl -u openspan-agent --no-pager -n 4 2>/dev/null | grep -c 'Agent registered'
echo '=== pipewire ==='
wpctl status >/dev/null 2>&1 && echo 'OK' || echo 'FAIL'
echo '=== auto-scanner disabled? ==='
systemctl is-enabled openspan-jbl 2>/dev/null || echo 'jbl-scanner: disabled (good)'
