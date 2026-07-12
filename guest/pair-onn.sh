#!/bin/bash
# One-shot: pair + connect a specific MAC, then STOP. No keepalive, no re-scan.
source /opt/openspan/env.sh
MAC="$1"
DEV="/org/bluez/hci0/dev_$(echo "$MAC" | tr ':' '_')"
conn(){ busctl --system get-property org.bluez "$DEV" org.bluez.Device1 Connected 2>/dev/null | awk '{print $2}'; }

# drop any stale bond so pairing is clean
bluetoothctl remove "$MAC" >/dev/null 2>&1

# scan until the device is actually seen (max 30s), then stop scanning
bluetoothctl --timeout 30 scan on >/dev/null 2>&1 &
SP=$!
seen=0
for i in $(seq 30); do
  if busctl --system tree org.bluez 2>/dev/null | grep -qi "dev_$(echo "$MAC" | tr ':' '_')"; then
    rssi=$(busctl --system get-property org.bluez "$DEV" org.bluez.Device1 RSSI 2>/dev/null | awk '{print $2}')
    [ -n "$rssi" ] && { seen=1; echo "seen at ${i}s rssi=$rssi"; break; }
  fi
  sleep 1
done
[ "$seen" = 0 ] && echo "seen at ?s (in tree, no rssi yet) -- proceeding"

# STOP scanning before pairing (scanning during A2DP disrupts it)
kill $SP 2>/dev/null
bluetoothctl scan off >/dev/null 2>&1
pkill -f 'scan on' 2>/dev/null
sleep 2

echo '--- pair ---'
bluetoothctl --timeout 20 pair "$MAC" 2>&1 | grep -iE 'success|fail|already|not available'
bluetoothctl trust "$MAC" >/dev/null 2>&1
echo '--- connect ---'
bluetoothctl --timeout 20 connect "$MAC" 2>&1 | grep -iE 'success|fail|already|not available'
sleep 3
echo "--- connected? --- $(conn)"
echo '--- A2DP sink present + set default? ---'
sink=$(wpctl status 2>/dev/null | grep -iE 'bluez_output|onn' | head -1)
echo "sink: ${sink:-NONE}"
id=$(echo "$sink" | grep -oE '[0-9]+\.' | head -1 | tr -d '.')
[ -n "$id" ] && { wpctl set-default "$id" 2>/dev/null; echo "set default sink -> $id"; }
echo 'DONE'
