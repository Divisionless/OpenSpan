#!/bin/bash
source /opt/openspan/env.sh
bluetoothctl power on >/dev/null 2>&1
bluetoothctl --timeout 16 scan on >/dev/null 2>&1 &
sleep 12
echo '=== audio devices in range ==='
for mac in $(bluetoothctl devices | awk '{print $2}'); do
  info=$(bluetoothctl info "$mac" 2>/dev/null)
  icon=$(echo "$info" | grep 'Icon:' | grep -oiE 'audio[a-z-]*')
  name=$(echo "$info" | grep 'Name:' | cut -d: -f2-)
  rssi=$(echo "$info" | grep 'RSSI:' | grep -oE '\-?[0-9]+')
  conn=$(echo "$info" | grep -c 'Connected: yes')
  if [ -n "$icon" ]; then
    echo "AUDIO: $mac |$name | icon=$icon | rssi=${rssi:-none} | connected=$conn"
  fi
done
echo '--- done ---'
