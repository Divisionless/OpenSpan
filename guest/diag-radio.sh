#!/bin/bash
echo '=== hciconfig (does hci0 exist / state?) ==='
hciconfig -a 2>&1 | head -10
echo '=== try to bring hci0 up ==='
hciconfig hci0 up 2>&1
echo '=== btmgmt info ==='
btmgmt info 2>&1 | head -8
echo '=== rfkill (blocked?) ==='
rfkill list 2>&1
echo '=== USB radio still passed through? ==='
lsusb 2>&1 | grep -i 8087 || echo 'NO 8087 usb'
echo '=== recent bluetooth/usb dmesg ==='
dmesg 2>&1 | grep -iE 'hci0|Bluetooth|btusb|firmware|8087' | tail -12
