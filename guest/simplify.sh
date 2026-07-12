#!/bin/bash
source /opt/openspan/env.sh
echo '=== stop + disable my custom audio loops (the non-standard junk) ==='
systemctl stop openspan-udprecv openspan-hold 2>/dev/null
systemctl disable openspan-udprecv openspan-hold 2>&1 | tail -2
echo '=== fresh STANDARD WirePlumber, nothing hammering it ==='
systemctl restart openspan-audio
sleep 6
echo '=== pipewire ok? ==='
wpctl status >/dev/null 2>&1 && echo OK || echo FAIL
echo '=== A2DP source endpoints registered? ==='
journalctl -u openspan-audio --no-pager --since '30 sec ago' 2>/dev/null | grep -c 'A2DPSource'
echo '=== bluez Media interface present (endpoint provider)? ==='
busctl --system introspect org.bluez /org/bluez/hci0 2>/dev/null | grep -i 'org.bluez.Media1' | head -1
echo '=== agent still up? ==='
systemctl is-active openspan-agent
echo '--- now it is just: bluez + a pairing agent + stock PipeWire. standard. ---'
