#!/bin/bash
echo "=== HCI command reliability (5x read class) ==="
for i in 1 2 3 4 5; do
  if timeout 3 hciconfig hci0 class >/dev/null 2>&1; then echo "$i OK"; else echo "$i TIMEOUT"; fi
done
echo "=== USB / bluetooth kernel errors ==="
dmesg | grep -iE "usb|bluetooth|hci0|xhci|reset|timeout|firmware" | tail -30
echo "=== controller speed ==="
lsusb -t 2>/dev/null | grep -iB1 -A1 8087 || lsusb -t
