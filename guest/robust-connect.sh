#!/bin/bash
source /opt/openspan/env.sh
have_sink(){ wpctl status 2>/dev/null | grep -qiE 'bluez_output'; }
echo '=== scanning up to 18s for ANY onn (by name, handles MAC change) ==='
bluetoothctl --timeout 20 scan on >/dev/null 2>&1 &
MAC=""
for i in $(seq 18); do
  for path in $(busctl --system tree org.bluez 2>/dev/null | grep -oE 'dev(_[0-9A-Fa-f]{2}){6}' | sort -u); do
    p="/org/bluez/hci0/$path"
    nm=$(busctl --system get-property org.bluez "$p" org.bluez.Device1 Alias 2>/dev/null | cut -d'"' -f2)
    echo "$nm" | grep -qi onn || continue
    rssi=$(busctl --system get-property org.bluez "$p" org.bluez.Device1 RSSI 2>/dev/null | awk '{print $2}')
    [ -n "$rssi" ] && { MAC=$(echo "$path"|sed 's/dev_//;s/_/:/g'); echo "FOUND: $MAC ($nm) rssi=$rssi"; break; }
  done
  [ -n "$MAC" ] && break
  sleep 1
done
bluetoothctl scan off >/dev/null 2>&1; pkill -f 'scan on' 2>/dev/null; sleep 1
if [ -z "$MAC" ]; then
  echo '>>> NO ONN REACHABLE. The buds are off/asleep/dead — not the software. Charge them (or use the other pair), get them blinking, and rerun.'
  exit 1
fi
echo '=== pair + trust + connect (3 tries) ==='
bluetoothctl pair "$MAC"  >/dev/null 2>&1
bluetoothctl trust "$MAC" >/dev/null 2>&1
for try in 1 2 3; do
  r=$(bluetoothctl connect "$MAC" 2>&1 | grep -iE 'success|fail|host is down|not available' | tail -1)
  echo "  try $try: $r"
  for i in $(seq 6); do have_sink && break 2; sleep 1; done
done
echo '=== RESULT ==='
if have_sink; then
  id=$(wpctl status 2>/dev/null | grep -iE 'bluez_output' | grep -oE '[0-9]+\.' | head -1 | tr -d '.')
  [ -n "$id" ] && wpctl set-default "$id" >/dev/null 2>&1
  echo "$MAC" > /opt/openspan/audio-device.txt
  echo ">>> CONNECTED — audio sink is up. Play something."
else
  echo ">>> Reached bluez but NO audio sink after 3 tries = the buds keep dropping the A2DP link (low battery / faulty). Stack is fine."
  bluetoothctl info "$MAC" 2>/dev/null | grep -E 'Connected|Battery'
fi
