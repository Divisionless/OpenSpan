#!/bin/bash
# wait for the radio to finish enumerating
for i in $(seq 50); do
  hciconfig hci0 >/dev/null 2>&1 && break
  sleep 2
done
echo "=== radio ==="
hciconfig hci0 2>/dev/null | grep -iE 'UP|DOWN' | head -1
echo "=== services ==="
systemctl is-active openspanble openspan-audio openspan-udprecv openspan-hold openspan-jbl
echo "=== radio dual (want br/edr + le) ==="
btmgmt info 2>/dev/null | grep -i 'current settings' | grep -oE 'br/edr|le' | tr '\n' ' '
echo
source /opt/openspan/env.sh
echo "=== pipewire ==="
wpctl status >/dev/null 2>&1 && echo PW-OK || echo PW-FAIL
echo "=== HFP flood since boot (want 0) ==="
journalctl -u openspan-audio --no-pager 2>/dev/null | grep -c NotPermitted
echo "=== receiver + keepalive listening/active ==="
ss -ulnp 2>/dev/null | grep -q 4010 && echo RECV-OK || echo RECV-NO
