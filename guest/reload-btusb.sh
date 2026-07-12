#!/bin/bash
echo '=== stop bluetooth, reload btusb driver, restart ==='
systemctl stop bluetooth
modprobe -r btusb btintel 2>&1
sleep 2
modprobe btusb 2>&1
sleep 6
systemctl start bluetooth
sleep 4
echo '=== controller present now? (want 1 item) ==='
btmgmt info 2>&1 | head -6
echo '=== hciconfig ==='
hciconfig 2>&1 | grep -iE 'hci0|UP|DOWN' | head -2
echo '=== dmesg ==='
dmesg 2>&1 | grep -iE 'hci0|failed|0c03|btusb|firmware' | tail -8
