#!/bin/bash
# Boot helper: the USB radio can take ~8-60s to enumerate, often after
# bluetoothd started -- so bluez can end up with no controller. Wait for the
# radio, make sure bluez sees it, power it on, force low LE connection-interval
# bounds (smooth iPad mouse), then bounce the audio stack so A2DP endpoints
# register against the live controller.
#
# NOTE: the retired stack (openspan-audio/hold/jbl) is intentionally NOT touched.
source /opt/openspan/env.sh
/opt/openspan/wait-hci0.sh
sleep 3
# If bluez has no controller registered, restart bluetoothd so it detects hci0.
for try in 1 2 3; do
  if btmgmt info 2>/dev/null | grep -q 'Index list with 0'; then
    systemctl restart bluetooth
    sleep 6
  else
    break
  fi
done
btmgmt power on >/dev/null 2>&1

# Low LE connection-interval bounds so the iPad HID mouse runs at ~7.5-15ms,
# not the 30-50ms kernel default (which makes the pointer laggy/jumpy). This is
# belt-and-suspenders with main.conf Min/MaxConnectionInterval in case a
# boot-order race resets the kernel defaults. Units are raw 1.25ms; 6=7.5ms,
# 12=15ms. Write min first so min<=max always holds.
echo 6  > /sys/kernel/debug/bluetooth/hci0/conn_min_interval 2>/dev/null
echo 12 > /sys/kernel/debug/bluetooth/hci0/conn_max_interval 2>/dev/null

# Radio confirmed present: (re)register A2DP endpoints, then restart the bridge.
systemctl restart openspan-wireplumber
sleep 4
systemctl restart openspan-udprecv
