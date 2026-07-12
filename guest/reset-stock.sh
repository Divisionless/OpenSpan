#!/bin/bash
source /opt/openspan/env.sh
echo '=== remove my custom WirePlumber rule (added after it worked) ==='
rm -f /etc/wireplumber/bluetooth.lua.d/51-no-suspend.lua
rm -f /etc/wireplumber/bluetooth.lua.d/52-no-hfp.lua
echo '=== wipe WirePlumber state cache ==='
rm -rf /root/.local/state/wireplumber/*
echo '=== fresh bluetoothd (clean endpoints/SEIDs) ==='
systemctl restart bluetooth
sleep 4
btmgmt power on >/dev/null 2>&1
sleep 1
echo '=== fresh PipeWire/WirePlumber on stock config ==='
systemctl restart openspan-audio
sleep 6
systemctl restart openspan-udprecv openspan-hold openspan-jbl
sleep 2
echo '=== RESULT ==='
btmgmt info 2>/dev/null | grep -q 'Index list with 1' && echo 'controller: UP' || echo 'controller: DOWN'
bluetoothctl show 2>&1 | grep -i Powered
wpctl status >/dev/null 2>&1 && echo 'pipewire: OK' || echo 'pipewire: FAIL'
echo '=== overrides remaining (want none) ==='
find /etc/wireplumber -type f 2>/dev/null || echo 'stock - no custom overrides'
