#!/bin/bash
# Connect MAC, report whether a REAL A2DP sink formed. Never removes the device.
# KEY: an unpaired discovered device is purged by BlueZ ~1s after discovery
# stops, so we must PAIR while the scan is still running. Only then stop the
# scan (device is now bonded, won't be purged, audio connect isn't disrupted).
source /opt/openspan/env.sh
MAC="$1"
have_sink(){ wpctl status 2>/dev/null | grep -qiE 'bluez_output'; }
bonded(){ bluetoothctl info "$MAC" 2>/dev/null | grep -q 'Paired: yes'; }

if bonded; then
  echo "reconnecting (already bonded)…"
  bluetoothctl connect "$MAC" 2>&1 | grep -iE 'success|fail|not available|host is down' | tail -1
else
  echo "discovering + pairing DURING a live scan…"
  bluetoothctl --timeout 40 scan on >/dev/null 2>&1 &
  SP=$!
  seen=no
  for i in $(seq 20); do bluetoothctl info "$MAC" 2>/dev/null | grep -q RSSI && { seen=yes; break; }; sleep 1; done
  if [ "$seen" = no ]; then
    kill $SP 2>/dev/null; bluetoothctl scan off >/dev/null 2>&1; pkill -f 'scan on' 2>/dev/null
    echo "NOT FOUND - buds not advertising."; exit 1
  fi
  # pair WHILE scanning -- this is what keeps the device 'available'
  echo "pair:    $(bluetoothctl pair "$MAC" 2>&1 | grep -iE 'success|fail|already|not available' | tail -1)"
  bluetoothctl trust "$MAC" >/dev/null 2>&1
  # now safe to stop the scan (bonded devices are not purged)
  kill $SP 2>/dev/null; bluetoothctl scan off >/dev/null 2>&1; pkill -f 'scan on' 2>/dev/null; sleep 1
  echo "connect: $(bluetoothctl connect "$MAC" 2>&1 | grep -iE 'success|fail|not available|host is down' | tail -1)"
fi

for i in $(seq 12); do have_sink && break; sleep 1; done
if have_sink; then
  id=$(wpctl status 2>/dev/null | grep -iE 'bluez_output' | grep -oE '[0-9]+\.' | head -1 | tr -d '.')
  [ -n "$id" ] && wpctl set-default "$id" >/dev/null 2>&1
  echo "$MAC" > /opt/openspan/audio-device.txt
  echo ">>> CONNECTED - audio sink is up. Play something."
else
  echo ">>> linked but no sink yet — re-blink + Connect again (stays in list)."
fi
