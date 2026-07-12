#!/bin/bash
source /opt/openspan/env.sh
echo '=== audio stack health (independent of the onn) ==='
printf 'services: '; systemctl is-active openspan-pipewire openspan-wireplumber openspan-udprecv 2>/dev/null | tr '\n' ' '; echo
printf 'wireplumber NRestarts: '; systemctl show openspan-wireplumber -p NRestarts --value 2>/dev/null
printf 'A2DP endpoints: '; journalctl -u bluetooth --no-pager -b 2>/dev/null | grep -c 'Endpoint registered'
printf 'wireplumber bus errors: '; journalctl -u openspan-wireplumber --no-pager -b 2>/dev/null | grep -c 'Failed to connect to bus'
printf 'default sink exists: '; wpctl status 2>/dev/null | grep -c 'Built-in Audio'
echo '=== scanning 12s for onn devices (keep buds blinking) ==='
bluetoothctl --timeout 12 scan on >/dev/null 2>&1
sleep 12
for path in $(busctl --system tree org.bluez 2>/dev/null | grep -oE 'dev(_[0-9A-Fa-f]{2}){6}' | sort -u); do
  p="/org/bluez/hci0/$path"
  name=$(busctl --system get-property org.bluez "$p" org.bluez.Device1 Alias 2>/dev/null | cut -d'"' -f2)
  echo "$name" | grep -qi onn || continue
  mac=$(echo "$path" | sed 's/dev_//; s/_/:/g')
  rssi=$(busctl --system get-property org.bluez "$p" org.bluez.Device1 RSSI 2>/dev/null | awk '{print $2}')
  echo "  ONN: $mac | $name | rssi=${rssi:-none(not currently seen)}"
done
bluetoothctl scan off >/dev/null 2>&1; pkill -f 'scan on' 2>/dev/null
echo done
