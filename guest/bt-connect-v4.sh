#!/bin/bash
# Connect MAC and report the TRUTH. Distinguishes three real states:
#   1. earbuds never connected (asleep/off/on another device)  -> NOT CONNECTED
#   2. connected at Bluetooth but no A2DP sink formed           -> nudge WirePlumber
#   3. connected + a real bluez_output sink                     -> CONNECTED
# Never removes the device (stays in the list). Never touches the keyboard.
source /opt/openspan/env.sh
MAC="$1"
info(){ bluetoothctl info "$MAC" 2>/dev/null; }
connected(){ info | grep -q 'Connected: yes'; }
bonded(){ info | grep -q 'Paired: yes'; }
have_sink(){ wpctl status 2>/dev/null | grep -qiE 'bluez_output'; }

if ! bonded; then
  # Fresh pair: pair DURING a live scan (an unpaired device is purged ~1s after
  # discovery stops, so we must bond it while scanning), then stop scan.
  echo "pairing (buds must be blinking)…"
  bluetoothctl --timeout 40 scan on >/dev/null 2>&1 &
  SP=$!
  seen=no
  for i in $(seq 20); do info | grep -q 'RSSI' && { seen=yes; break; }; sleep 1; done
  if [ "$seen" = yes ]; then
    bluetoothctl pair "$MAC"  >/dev/null 2>&1
    bluetoothctl trust "$MAC" >/dev/null 2>&1
  fi
  kill $SP 2>/dev/null; bluetoothctl scan off >/dev/null 2>&1; pkill -f 'scan on' 2>/dev/null; sleep 1
  if [ "$seen" = no ]; then
    echo "NOT CONNECTED - the earbuds aren't advertising. Take them OUT of the case, get them blinking, and Connect again."
    exit 1
  fi
fi

# Try to connect a few times -- these earbuds have a short wake window.
echo "connecting…"
for try in 1 2 3; do
  bluetoothctl connect "$MAC" >/dev/null 2>&1
  connected && break
  sleep 2
done

# STATE 1: never actually connected at Bluetooth.
if ! connected; then
  echo "NOT CONNECTED - the earbuds didn't respond. They're asleep/off, or connected to your phone. Wake them (out of the case, blinking), turn off phone Bluetooth, and Connect again."
  exit 1
fi

# STATE 2: connected at Bluetooth -- wait for the A2DP sink; if it doesn't
# appear, nudge WirePlumber so it adopts the now-connected earbuds.
for i in $(seq 8); do have_sink && break; sleep 1; done
if ! have_sink; then
  echo "connected — bringing up the audio profile…"
  systemctl restart openspan-wireplumber >/dev/null 2>&1
  for i in $(seq 12); do have_sink && break; sleep 1; done
fi

# STATE 3: real sink -> success.
if have_sink; then
  id=$(wpctl status 2>/dev/null | grep -iE 'bluez_output' | grep -oE '[0-9]+\.' | head -1 | tr -d '.')
  [ -n "$id" ] && wpctl set-default "$id" >/dev/null 2>&1
  echo "$MAC" > /opt/openspan/audio-device.txt
  echo "CONNECTED - audio is routed to these earbuds. Play something."
else
  echo "Bluetooth connected but the audio (A2DP) profile did not come up even after a WirePlumber restart. Connect once more; if it repeats, the earbuds refused A2DP this session."
fi
