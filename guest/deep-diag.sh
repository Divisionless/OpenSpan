#!/bin/bash
source /opt/openspan/env.sh
echo '=== adapter: powered + does it advertise A2DP Source (110a)? ==='
bluetoothctl show 2>&1 | grep -iE 'Powered|UUID'
echo '=== WirePlumber bluez monitor activity (excluding the flood) ==='
journalctl -u openspan-audio --no-pager 2>/dev/null | grep -iE 'bluez|a2dp|sbc|codec|endpoint|monitor|spa' | grep -ivE 'NotPermitted' | tail -12
echo '=== bluez Media interface present on adapter? ==='
busctl --system introspect org.bluez /org/bluez/hci0 2>/dev/null | grep -iE 'Media' | head
