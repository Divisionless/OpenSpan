#!/bin/bash
source /opt/openspan/env.sh
MAC="$1"
bluetoothctl agent on >/dev/null 2>&1
bluetoothctl default-agent >/dev/null 2>&1
bluetoothctl --timeout 18 scan on >/dev/null 2>&1 &
sleep 4
echo "=== pair $MAC ==="
bluetoothctl pair "$MAC" 2>&1 | tail -2
bluetoothctl trust "$MAC" >/dev/null 2>&1
echo '=== connect ==='
bluetoothctl connect "$MAC" 2>&1 | tail -2
sleep 5
echo '=== state (want Connected: yes) ==='
bluetoothctl info "$MAC" | grep -E 'Connected|Paired'
echo '=== supervisor streaming to it? ==='
wpctl status 2>&1 | sed -n '/Streams:/,/Settings/p' | grep -iE 'pw-play|playback_F' | head -3
echo '=== 30s HOLD (keepalive off - raw device behavior) ==='
for t in 6 12 18 24 30; do
  sleep 6
  C=$(bluetoothctl info "$MAC" | grep -c 'Connected: yes')
  echo "t=${t}s connected=${C}"
done
