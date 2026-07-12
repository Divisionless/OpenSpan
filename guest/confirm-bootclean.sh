#!/bin/bash
source /opt/openspan/env.sh
# stop the leftover cruft now (cosmetic — the shutdown kills it anyway)
for s in openspan-jbl openspan-hold openspan-audio; do
  systemctl stop "$s" 2>/dev/null
  systemctl disable "$s" 2>/dev/null
done
pkill -f 'openspan-jbl.sh' 2>/dev/null
pkill -f 'keepalive-audio.sh' 2>/dev/null
bluetoothctl scan off >/dev/null 2>&1
sleep 3
echo '=== *** BOOT CONFIG: what will start on the fresh reboot *** ==='
echo '-- SHOULD be enabled (the 7 correct services) --'
for s in openspan-btready openspan-agent openspan-dbus openspan-pipewire openspan-wireplumber openspan-pipewire-pulse openspan-udprecv; do
  printf '  %-26s ' "$s"; systemctl is-enabled "$s" 2>/dev/null
done
echo '-- SHOULD be disabled (old cruft) --'
for s in openspan-jbl openspan-hold openspan-audio; do
  printf '  %-22s ' "$s"; systemctl is-enabled "$s" 2>/dev/null
done
echo '=== scanner gone now + discovery quiet? ==='
ps -eo args 2>/dev/null | grep 'scan on' | grep -v grep || echo '  no scanner'
echo -n '  discovering='; busctl --system get-property org.bluez /org/bluez/hci0 org.bluez.Adapter1 Discovering 2>/dev/null
echo '=== USB is xHCI + radio up? ==='
lsusb 2>/dev/null | grep -q 8087 && echo '  radio: present' || echo '  radio: MISSING'
