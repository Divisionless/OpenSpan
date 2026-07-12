#!/bin/bash
# Connect MAC honestly. A device that is NOT yet bonded gets ONLY paired on this
# pass (pair DURING a live scan so it isn't purged) -- the audio (A2DP) profile
# comes up on the NEXT Connect, so we must NOT claim CONNECTED after a mere
# pairing pass. A device that is already bonded gets connected for real, and we
# report CONNECTED only when bluez actually reports Connected: yes.
source /opt/openspan/env.sh
MAC="$1"
connected() { bluetoothctl info "$MAC" 2>/dev/null | grep -q 'Connected: yes'; }
paired()    { bluetoothctl info "$MAC" 2>/dev/null | grep -q 'Paired: yes'; }

if paired; then
  # already bonded -> real connect
  bluetoothctl connect "$MAC" >/dev/null 2>&1
  sleep 3
  if connected; then
    echo "CONNECTED — audio is on these earbuds. Play something."
  else
    echo "not connected yet — keep the buds blinking and Connect again."
  fi
else
  # first pass: BOND ONLY (pair + trust) during a live scan. Audio starts on
  # the next Connect, so do not claim it's connected here.
  echo "pairing (keep the buds blinking)…"
  bluetoothctl --timeout 30 scan on >/dev/null 2>&1 &
  SP=$!
  for i in $(seq 15); do
    bluetoothctl info "$MAC" 2>/dev/null | grep -q 'RSSI' && break
    sleep 1
  done
  bluetoothctl pair "$MAC"  >/dev/null 2>&1
  bluetoothctl trust "$MAC" >/dev/null 2>&1
  kill $SP 2>/dev/null
  bluetoothctl scan off >/dev/null 2>&1
  pkill -f 'scan on' 2>/dev/null
  if paired; then
    echo "paired ✓ — double-click Connect once more to start the audio."
  else
    echo "pairing didn't take — keep the buds blinking and try Connect again."
  fi
fi
# record for auto-reconnect ONLY when this really is an audio device: a
# non-audio MAC here (e.g. the iPad, via a stray double-click) would be
# exempted from Broadcast bond-cleanup AND chased by auto-reconnect. Also
# keeps a FAILED pairing of new buds from overwriting the good MAC.
bluetoothctl info "$MAC" 2>/dev/null | grep -qi "Icon: audio" \
  && echo "$MAC" > /opt/openspan/audio-device.txt || true
