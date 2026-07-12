#!/bin/bash
source /opt/openspan/env.sh
F=/usr/share/wireplumber/bluetooth.lua.d/50-bluez-config.lua
# Disable the HFP/HSP backend -- headphones for music only need A2DP. This is
# what stops the RegisterProfile NotPermitted flood that's starving A2DP.
sed -i 's|.*\["bluez5.hfphsp-backend"\].*|  ["bluez5.hfphsp-backend"] = "none",|' "$F"
echo 'setting:'; grep 'hfphsp-backend' "$F" | grep -v '^[[:space:]]*--'
systemctl restart openspan-audio
sleep 8
echo '=== RegisterProfile flood in last 12s (want 0) ==='
journalctl -u openspan-audio --no-pager --since '12 sec ago' 2>/dev/null | grep -c NotPermitted
echo '=== wireplumber healthy? ==='
wpctl status >/dev/null 2>&1 && echo OK || echo FAIL
echo '=== A2DP source endpoints registered now? ==='
journalctl -u openspan-audio --no-pager --since '12 sec ago' 2>/dev/null | grep -c 'A2DPSource'
