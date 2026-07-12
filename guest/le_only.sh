#!/bin/bash
# Force the controller LE-only so the iPad sees a single BLE keyboard
# entry (no dual-mode Classic decoy).
systemctl stop openspanble
sleep 1
btmgmt power off
btmgmt bredr off
btmgmt le on
btmgmt power on
sleep 2
echo "=== controller info ==="
btmgmt info | grep -iE "current settings|name"
systemctl start openspanble
sleep 4
echo "=== daemon ==="
systemctl is-active openspanble
busctl get-property org.bluez /org/bluez/hci0 org.bluez.LEAdvertisingManager1 ActiveInstances 2>/dev/null
journalctl -u openspanble --since "-15s" --no-pager -o cat | tail -5
