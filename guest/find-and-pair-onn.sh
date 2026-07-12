#!/bin/bash
# Discover the onn by NAME (handles MAC changes across resets), then
# pair + connect + set as default sink. One shot. No keepalive, no re-scan.
source /opt/openspan/env.sh

# scan until we find a device whose name contains 'onn' (max 30s)
bluetoothctl --timeout 30 scan on >/dev/null 2>&1 &
SP=$!
MAC=""
for i in $(seq 30); do
  while read -r path; do
    d=$(echo "$path" | grep -oE 'dev_[0-9A-F_]+')
    [ -z "$d" ] && continue
    n=$(busctl --system get-property org.bluez "/org/bluez/hci0/$d" org.bluez.Device1 Name 2>/dev/null | sed 's/^s //; s/"//g')
    if echo "$n" | grep -qi 'onn'; then
      MAC=$(echo "$d" | sed 's/dev_//; s/_/:/g')
      echo "found onn: $MAC ($n) at ${i}s"
      break
    fi
  done < <(busctl --system tree org.bluez 2>/dev/null | grep -oE '/org/bluez/hci0/dev_[0-9A-F_]+')
  [ -n "$MAC" ] && break
  sleep 1
done

# stop scanning BEFORE pairing (scanning during A2DP disrupts it)
kill $SP 2>/dev/null; bluetoothctl scan off >/dev/null 2>&1; pkill -f 'scan on' 2>/dev/null
sleep 2

if [ -z "$MAC" ]; then echo 'ONN NOT FOUND — not advertising'; exit 1; fi

DEV="/org/bluez/hci0/dev_$(echo "$MAC" | tr ':' '_')"
conn(){ busctl --system get-property org.bluez "$DEV" org.bluez.Device1 Connected 2>/dev/null | awk '{print $2}'; }

bluetoothctl remove "$MAC" >/dev/null 2>&1
sleep 1
echo '--- pair ---'
bluetoothctl --timeout 20 pair "$MAC" 2>&1 | grep -iE 'success|fail|already|not available'
bluetoothctl trust "$MAC" >/dev/null 2>&1
echo '--- connect ---'
bluetoothctl --timeout 20 connect "$MAC" 2>&1 | grep -iE 'success|fail|already|not available'
sleep 3
echo "--- connected? --- $(conn)"
id=$(wpctl status 2>/dev/null | grep -iE 'onn|bluez_output' | grep -oE '[0-9]+\.' | head -1 | tr -d '.')
if [ -n "$id" ]; then wpctl set-default "$id" 2>/dev/null; echo "A2DP sink -> default id=$id"; else echo 'NO SINK'; fi
echo "MAC=$MAC"
echo DONE
