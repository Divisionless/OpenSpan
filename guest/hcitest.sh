#!/bin/bash
# Wait for hci0, then hammer HCI commands to test reliability under the
# new OHCI passthrough (the xHCI path wedged under load).
for i in $(seq 1 20); do
  hciconfig hci0 >/dev/null 2>&1 && break
  sleep 1
done
if ! hciconfig hci0 >/dev/null 2>&1; then
  echo "NO-HCI0"; dmesg | grep -iE 'btusb|firmware|bluetooth' | tail -6; exit 1
fi
systemctl restart bluetooth 2>/dev/null; sleep 2
echo "=== 20x rapid HCI read class ==="
ok=0; to=0
for i in $(seq 1 20); do
  if timeout 2 hciconfig hci0 class >/dev/null 2>&1; then ok=$((ok+1)); else to=$((to+1)); fi
done
echo "OK=$ok TIMEOUT=$to"
echo "=== sustained: name + rssi + scan pressure ==="
timeout 4 hcitool inq >/dev/null 2>&1
for i in $(seq 1 10); do timeout 2 hciconfig hci0 up >/dev/null 2>&1 && printf . || printf X; done; echo
echo "=== hung-task / -110 check ==="
dmesg | grep -iE 'hung|-110|hci0.*failed' | tail -4 || echo "(none)"
echo "=== firmware line ==="
dmesg | grep -iE 'ibt|firmware' | tail -3
