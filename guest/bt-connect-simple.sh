#!/bin/bash
# Simple, HONEST connect. Fresh-pairs the given MAC (put the earbuds in pairing
# mode / blinking first), then reports the REAL result: whether a Bluetooth
# audio SINK actually came up -- not bluez's misleading "Connected: yes".
source /opt/openspan/env.sh
MAC="$1"

# start clean so pairing is deterministic (no "AlreadyExists" noise)
bluetoothctl remove "$MAC" >/dev/null 2>&1
sleep 1

# one continuous scan: wait until the earbuds are actually seen, then pair+trust
bluetoothctl --timeout 25 scan on >/dev/null 2>&1 &
seen=no
for i in $(seq 20); do
  bluetoothctl info "$MAC" 2>/dev/null | grep -q 'RSSI' && { seen=yes; break; }
  sleep 1
done
if [ "$seen" = no ]; then
  bluetoothctl scan off >/dev/null 2>&1; pkill -f 'scan on' 2>/dev/null
  echo "NOT FOUND - the earbuds aren't broadcasting. Make sure they're blinking (pairing mode) and try again."
  exit 1
fi
bluetoothctl pair "$MAC"  >/dev/null 2>&1
bluetoothctl trust "$MAC" >/dev/null 2>&1
bluetoothctl scan off >/dev/null 2>&1; pkill -f 'scan on' 2>/dev/null
sleep 1
bluetoothctl connect "$MAC" >/dev/null 2>&1

# REAL success = an A2DP sink node actually exists in the audio graph
sink=""
for i in $(seq 10); do
  sink=$(wpctl status 2>/dev/null | grep -iE 'bluez_output|onn' | head -1)
  [ -n "$sink" ] && break
  sleep 1
done
if [ -n "$sink" ]; then
  id=$(echo "$sink" | grep -oE '[0-9]+\.' | head -1 | tr -d '.')
  [ -n "$id" ] && wpctl set-default "$id" >/dev/null 2>&1
  echo "$MAC" > /opt/openspan/audio-device.txt
  echo "CONNECTED - audio is routed to these earbuds. Play something."
else
  echo "NOT CONNECTED - bluez linked but no audio sink came up. The earbuds are half-asleep; re-blink them and try once more."
fi
