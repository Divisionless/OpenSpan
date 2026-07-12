#!/bin/bash
# List BT devices via pure D-Bus (busctl) -- shows the ALIAS (the user-set
# friendly name, exactly like Windows' rename) so two identical headphones can
# be told apart. NO bluetoothctl / no scan, safe to call repeatedly.
# Output: MAC|Alias|paired|connected|icon
for path in $(busctl --system tree org.bluez 2>/dev/null \
        | grep -oE '/org/bluez/hci[0-9]+/dev(_[0-9A-Fa-f]{2}){6}' | sort -u); do
  mac=$(echo "$path" | sed -E 's|.*/dev_||; s/_/:/g')
  # Alias defaults to the reported Name; a rename overrides just the Alias.
  name=$(busctl --system get-property org.bluez "$path" org.bluez.Device1 Alias 2>/dev/null | cut -d'"' -f2)
  [ -z "$name" ] && continue
  [ "$name" = "$mac" ] && continue
  paired=$(busctl --system get-property org.bluez "$path" org.bluez.Device1 Paired 2>/dev/null | awk '{print $2}')
  conn=$(busctl --system get-property org.bluez "$path" org.bluez.Device1 Connected 2>/dev/null | awk '{print $2}')
  icon=$(busctl --system get-property org.bluez "$path" org.bluez.Device1 Icon 2>/dev/null | cut -d'"' -f2)
  p=0; [ "$paired" = "true" ] && p=1
  c=0; [ "$conn" = "true" ] && c=1
  echo "${mac}|${name}|${p}|${c}|${icon}"
done
