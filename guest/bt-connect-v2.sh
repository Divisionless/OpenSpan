#!/bin/bash
# Connect MAC and report whether a REAL A2DP sink formed. NEVER removes the
# device -- it stays in the list. Uses the existing bond if present (fast
# reconnect, no scan, no re-pair); only scans+pairs when not bonded yet.
source /opt/openspan/env.sh
MAC="$1"
have_sink(){ wpctl status 2>/dev/null | grep -qiE 'bluez_output'; }
bonded(){ bluetoothctl info "$MAC" 2>/dev/null | grep -q 'Paired: yes'; }

if bonded; then
  echo "reconnecting (already bonded)…"
  bluetoothctl connect "$MAC" 2>&1 | grep -iE 'successful|fail|not available|host is down' | tail -1
else
  echo "pairing (buds must be blinking)…"
  bluetoothctl --timeout 20 scan on >/dev/null 2>&1 &
  seen=no
  for i in $(seq 15); do bluetoothctl info "$MAC" 2>/dev/null | grep -q RSSI && { seen=yes; break; }; sleep 1; done
  bluetoothctl scan off >/dev/null 2>&1; pkill -f 'scan on' 2>/dev/null; sleep 1
  if [ "$seen" = no ]; then echo "NOT FOUND - buds aren't advertising. Make sure they're blinking + charged."; exit 1; fi
  bluetoothctl pair "$MAC" >/dev/null 2>&1
  bluetoothctl trust "$MAC" >/dev/null 2>&1
  bluetoothctl connect "$MAC" 2>&1 | grep -iE 'successful|fail|not available|host is down' | tail -1
fi

for i in $(seq 12); do have_sink && break; sleep 1; done
if have_sink; then
  id=$(wpctl status 2>/dev/null | grep -iE 'bluez_output' | grep -oE '[0-9]+\.' | head -1 | tr -d '.')
  [ -n "$id" ] && wpctl set-default "$id" >/dev/null 2>&1
  echo "$MAC" > /opt/openspan/audio-device.txt
  echo "CONNECTED - audio is routed to these earbuds. Play something."
else
  echo "linked but no audio sink yet — buds likely dropped mid-handshake. Re-blink + Connect again (they stay in the list now)."
fi
