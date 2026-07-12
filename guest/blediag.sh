#!/bin/bash
echo "=== connected devices ==="
bluetoothctl devices Connected 2>/dev/null || bluetoothctl paired-devices
echo "=== all known devices ==="
bluetoothctl devices
echo "=== LE connection (hcitool con) ==="
hcitool con
echo "=== our device info ==="
for d in $(bluetoothctl devices | awk '{print $2}'); do
  echo "-- $d --"
  bluetoothctl info "$d" | grep -E "Name|Paired|Bonded|Trusted|Connected|Appearance"
done
