#!/bin/bash
# List named BT devices via pure D-Bus (busctl) -- NO bluetoothctl, so it does
# NOT register advertisement monitors and does NOT make the radio scan. Safe
# to call repeatedly (the app refreshes with this) without disrupting audio.
# Output: MAC|Name|paired|connected|icon
for path in $(busctl --system tree org.bluez 2>/dev/null \
        | grep -oE '/org/bluez/hci[0-9]+/dev(_[0-9A-Fa-f]{2}){6}' | sort -u); do
  mac=$(echo "$path" | sed -E 's|.*/dev_||; s/_/:/g')
  name=$(busctl --system get-property org.bluez "$path" org.bluez.Device1 Name 2>/dev/null | cut -d'"' -f2)
  [ -z "$name" ] && continue
  [ "$name" = "$mac" ] && continue
  paired=$(busctl --system get-property org.bluez "$path" org.bluez.Device1 Paired 2>/dev/null | awk '{print $2}')
  conn=$(busctl --system get-property org.bluez "$path" org.bluez.Device1 Connected 2>/dev/null | awk '{print $2}')
  icon=$(busctl --system get-property org.bluez "$path" org.bluez.Device1 Icon 2>/dev/null | cut -d'"' -f2)
  p=0; [ "$paired" = "true" ] && p=1
  c=0; [ "$conn" = "true" ] && c=1
  echo "${mac}|${name}|${p}|${c}|${icon}"
done
