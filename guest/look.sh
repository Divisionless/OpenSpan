#!/bin/bash
echo '=== custom WirePlumber overrides I added (should be NONE for stock) ==='
find /etc/wireplumber -type f 2>/dev/null
echo '--- content of each ---'
for f in $(find /etc/wireplumber -type f 2>/dev/null); do echo "### $f"; cat "$f"; echo; done
echo '=== 50-bluez-config: any ACTIVE (uncommented) overrides I made? ==='
grep -nE '^\s*\["bluez5' /usr/share/wireplumber/bluetooth.lua.d/50-bluez-config.lua
echo '=== bluez main.conf key settings ==='
grep -iE '^ControllerMode|^Experimental|^AutoEnable|^Class|^\[' /etc/bluetooth/main.conf
echo '=== WirePlumber state cache files ==='
ls -la /root/.local/state/wireplumber/ 2>/dev/null || echo 'none'
echo '=== services touching bluetooth/audio ==='
systemctl list-units --type=service --state=running 2>/dev/null | grep -iE 'openspan|bluetooth|pipewire' | awk '{print $1}'
