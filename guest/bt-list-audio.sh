#!/bin/bash
# List BT devices via busctl -- shows the ALIAS (rename). This panel is for
# HEADPHONES, so it filters out non-audio clutter: the iPad HID keyboard (shows
# as multimedia-player), computers/phones/input devices, and unnamed junk whose
# only "name" is its own MAC. Audio + unknown-icon devices (new, not-yet-
# classified headphones) are kept so pairing new buds still works.
# Output: MAC|Alias|paired|connected|icon
for path in $(busctl --system tree org.bluez 2>/dev/null \
        | grep -oE '/org/bluez/hci[0-9]+/dev(_[0-9A-Fa-f]{2}){6}' | sort -u); do
  mac=$(echo "$path" | sed -E 's|.*/dev_||; s/_/:/g')
  name=$(busctl --system get-property org.bluez "$path" org.bluez.Device1 Alias 2>/dev/null | cut -d'"' -f2)
  [ -z "$name" ] && continue
  # drop entries whose name is just the address (colon or dash form) = junk
  [ "$name" = "$mac" ] && continue
  [ "$name" = "$(echo "$mac" | tr ':' '-')" ] && continue
  icon=$(busctl --system get-property org.bluez "$path" org.bluez.Device1 Icon 2>/dev/null | cut -d'"' -f2)
  # keep audio + unknown; drop known non-headphone device classes
  case "$icon" in
    multimedia-player|computer|phone|input-keyboard|input-mouse|input-tablet|input-gaming|camera-*|printer|scanner|network-*) continue ;;
  esac
  paired=$(busctl --system get-property org.bluez "$path" org.bluez.Device1 Paired 2>/dev/null | awk '{print $2}')
  conn=$(busctl --system get-property org.bluez "$path" org.bluez.Device1 Connected 2>/dev/null | awk '{print $2}')
  p=0; [ "$paired" = "true" ] && p=1
  c=0; [ "$conn" = "true" ] && c=1
  echo "${mac}|${name}|${p}|${c}|${icon}"
done
