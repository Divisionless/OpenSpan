#!/bin/bash
source /opt/openspan/env.sh
for i in $(seq 45); do
  btmgmt info 2>/dev/null | grep -q 'Index list with 1' && break
  sleep 2
done
echo '=== USB radio enumerated on xHCI? ==='
lsusb 2>/dev/null | grep -i 8087 || echo 'NO 8087 DEVICE'
echo '=== USB tree (want xHCI + btusb) ==='
lsusb -t 2>/dev/null | grep -iE 'xhci|btusb|8087' | head
echo '=== controller up? ==='
btmgmt info 2>/dev/null | grep -q 'Index list with 1' && echo 'CONTROLLER UP' || echo 'CONTROLLER DOWN'
echo '=== radio init dmesg (any errors?) ==='
dmesg 2>/dev/null | grep -iE 'hci0|firmware|xhci.*8087|8087' | tail -6
echo '=== services active? ==='
systemctl is-active openspan-agent openspan-pipewire openspan-wireplumber openspan-udprecv
echo '=== A2DP endpoints registered? ==='
journalctl -u bluetooth --no-pager -b 2>/dev/null | grep -c 'Endpoint registered'
