#!/bin/bash
source /opt/openspan/env.sh
# wait for the controller (btready is bringing the radio up, ~62s)
for i in $(seq 50); do
  btmgmt info 2>/dev/null | grep -q 'Index list with 1' && break
  sleep 2
done
echo '=== controller ==='
btmgmt info 2>/dev/null | grep -q 'Index list with 1' && echo UP || echo DOWN
bluetoothctl show 2>&1 | grep -i Powered
echo '=== all 7 services active? ==='
systemctl is-active openspan-btready openspan-agent openspan-dbus openspan-pipewire openspan-wireplumber openspan-pipewire-pulse openspan-udprecv
echo '=== wireplumber running? ==='
pgrep -x wireplumber | head -1
echo '=== *** A2DP endpoints registered since boot (want > 0) *** ==='
journalctl -u bluetooth --no-pager -b 2>/dev/null | grep -c 'Endpoint registered'
echo '=== bridge listening on 4010? ==='
ss -ulnp 2>/dev/null | grep -q 4010 && echo yes || echo NO
echo '=== pairing agent registered? ==='
journalctl -u openspan-agent --no-pager -b 2>/dev/null | grep -c 'Agent registered'
echo '=== discovery quiet (no scanning)? ==='
bluetoothctl show 2>&1 | grep -i Discovering
