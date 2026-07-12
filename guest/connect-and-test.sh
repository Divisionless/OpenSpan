#!/bin/bash
source /opt/openspan/env.sh
bluetoothctl agent on >/dev/null 2>&1
bluetoothctl default-agent >/dev/null 2>&1
echo '=== scan for the onn in pairing mode ==='
bluetoothctl --timeout 14 scan on >/dev/null 2>&1 &
sleep 8
MAC=""
for m in $(bluetoothctl devices 2>/dev/null | grep -i 'onn' | awk '{print $2}'); do
  if bluetoothctl info "$m" 2>/dev/null | grep -q 'RSSI'; then MAC="$m"; break; fi
done
[ -z "$MAC" ] && MAC=$(bluetoothctl devices 2>/dev/null | grep -i 'onn' | awk '{print $2}' | head -1)
echo "target onn: $MAC"
[ -z "$MAC" ] && { echo 'NO onn found'; exit 1; }
echo '=== fresh pair (remove stale bond) ==='
bluetoothctl remove "$MAC" >/dev/null 2>&1
sleep 1
bluetoothctl --timeout 16 scan on >/dev/null 2>&1 &
sleep 3
bluetoothctl pair "$MAC" 2>&1 | tail -1
bluetoothctl trust "$MAC" >/dev/null 2>&1
bluetoothctl connect "$MAC" 2>&1 | tail -1
sleep 6
echo "$MAC" > /opt/openspan/audio-device.txt
echo '=== connected? ==='
bluetoothctl info "$MAC" | grep Connected
echo '=== A2DP transport (sep/fd)? ==='
busctl --system tree org.bluez 2>/dev/null | grep -iE '/sep|/fd' | head -2 || echo 'NO transport'
echo '=== onn SINK? ==='
wpctl status 2>&1 | grep -i 'onn'
if wpctl status 2>&1 | grep -qi 'onn True'; then
  echo '=== SINK PRESENT -> playing tone, LISTEN ==='
  python3 - <<'PY'
import math, struct, wave
w = wave.open('/tmp/t.wav', 'w'); w.setnchannels(2); w.setsampwidth(2); w.setframerate(48000)
for i in range(48000 * 3):
    v = int(20000 * math.sin(2 * math.pi * 660 * i / 48000))
    w.writeframesraw(struct.pack('<hh', v, v))
w.close()
PY
  pw-play /tmp/t.wav 2>&1
  echo 'tone finished'
else
  echo '=== NO SINK - A2DP not negotiating ==='
fi
