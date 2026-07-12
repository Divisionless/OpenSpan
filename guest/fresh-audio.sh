#!/bin/bash
source /opt/openspan/env.sh
echo '=== radio + services ==='
hciconfig hci0 2>/dev/null | grep -iE 'UP|DOWN' | head -1
systemctl is-active openspanble openspan-audio openspan-udprecv openspan-hold openspan-jbl
echo '=== WirePlumber state files (the stuck "off" profile cache) ==='
find /root /var/lib /home -path '*wireplumber*' -type f 2>/dev/null | head
echo '=== clearing that cache ==='
rm -rf /root/.local/state/wireplumber /var/lib/wireplumber 2>/dev/null
echo '=== revert hfphsp-backend to default ==='
sed -i 's|^  \["bluez5.hfphsp-backend"\] = "none",|  --["bluez5.hfphsp-backend"] = "native",|' /usr/share/wireplumber/bluetooth.lua.d/50-bluez-config.lua
grep -n 'hfphsp-backend' /usr/share/wireplumber/bluetooth.lua.d/50-bluez-config.lua | head -1
echo '=== restart audio stack clean ==='
systemctl restart openspan-audio
sleep 6
systemctl restart openspan-udprecv openspan-hold openspan-jbl
sleep 2
echo '=== pipewire ok? ==='
wpctl status >/dev/null 2>&1 && echo PW-OK || echo PW-FAIL
echo '=== sinks (built-in only until a headphone connects) ==='
wpctl status 2>&1 | sed -n '/Sinks:/,/Sink endpoints/p'
