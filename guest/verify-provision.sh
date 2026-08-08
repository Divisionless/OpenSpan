#!/bin/bash
# Verify the software-only half of a fresh OpenSpan guest provision.
# Radio enumeration, pairing, HID traffic, and A2DP playback remain explicit
# hardware checks. A fresh clone with no configured lanes is valid.
pass=0; fail=0
SD=/etc/systemd/system

ok()   { printf '  \033[1;32mPASS\033[0m  %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  \033[1;31mFAIL\033[0m  %s\n' "$1"; fail=$((fail+1)); }
note() { printf '  ----  %s\n' "$1"; }

echo "== required units enabled =="
for u in openspan-agent openspan-btready openspan-pipewire \
         openspan-wireplumber openspan-pipewire-pulse openspan-udprecv; do
  if systemctl is-enabled "$u" >/dev/null 2>&1; then ok "$u enabled"
  else bad "$u NOT enabled"; fi
done

echo "== dormant fixed-lane compatibility =="
for u in openspanble openspanble-mac; do
  if systemctl is-enabled "$u" >/dev/null 2>&1; then
    bad "$u compatibility unit is enabled"
  else
    ok "$u compatibility unit is disabled"
  fi
done
for f in "$SD/openspanble.service.d/20-radio.conf" \
         "$SD/openspanble-mac.service.d/20-radio.conf"; do
  [ ! -e "$f" ] && ok "$f retired" || bad "$f stale radio claim remains"
done

echo "== non-radio services active =="
for u in openspan-pipewire openspan-wireplumber openspan-pipewire-pulse \
         openspan-udprecv openspan-agent; do
  st=$(systemctl is-active "$u" 2>/dev/null)
  [ "$st" = active ] && ok "$u active" || bad "$u is '$st' (expected active)"
done
note "openspan-btready: $(systemctl is-active openspan-btready 2>/dev/null) (radio-dependent)"

echo "== audio footing =="
loginctl show-user root 2>/dev/null | grep -q 'Linger=yes' \
  && ok "root linger enabled" || bad "root linger NOT enabled"
[ -S /run/user/0/bus ] && ok "/run/user/0/bus present" \
  || bad "/run/user/0/bus MISSING (user bus not up)"
[ -L /etc/systemd/user/pipewire.service ] && ok "stock pipewire masked" \
  || bad "stock pipewire NOT masked"

echo "== config files in place =="
for f in /etc/bluetooth/main.conf \
         /usr/share/wireplumber/bluetooth.lua.d/50-bluez-config.lua \
         /etc/modprobe.d/btusb-noautosuspend.conf \
         /lib/firmware/rtl_bt/rtl8761bu_fw.bin \
         /opt/openspan/openspan_ble.py /opt/openspan/openspan_bt.py \
         /opt/openspan/udp_to_sink.py \
         "$SD/openspanble@.service" \
         "$SD/openspanble.service" \
         "$SD/openspanble-mac.service" \
         "$SD/openspanble.service.d/10-wait.conf" \
         "$SD/openspanble.service.d/override.conf"; do
  [ -f "$f" ] && ok "$f" || bad "$f MISSING"
done
for f in /opt/openspan/set-hid-device.sh \
         /opt/openspan/set-hid-radio.sh \
         /opt/openspan/set-hid-target.sh \
         /opt/openspan/bt-preflight.sh \
         /opt/openspan/ensure-dualmode.sh \
         /opt/openspan/wait-hci0.sh; do
  [ -x "$f" ] && ok "$f executable" || bad "$f MISSING or not executable"
done
grep -q 'suspend-timeout-seconds"\] = 0' \
  /usr/share/wireplumber/bluetooth.lua.d/50-bluez-config.lua 2>/dev/null \
  && ok "A2DP suspend disabled in bluez config" \
  || bad "suspend-timeout not set in bluez config"

echo "== configured templated lanes =="
lane_count=0
for conf in "$SD"/openspanble@*.service.d/20-device.conf; do
  [ -f "$conf" ] || continue
  lane_count=$((lane_count+1))
  unit_dir=$(basename "$(dirname "$conf")")
  device_id=${unit_dir#openspanble@}
  device_id=${device_id%.service.d}
  unit="openspanble@$device_id"

  complete=yes
  for key in OPENSPAN_ADAPTER OPENSPAN_PORT OPENSPAN_DEVICE_NAME; do
    grep -q "$key=" "$conf" || complete=no
  done
  [ "$complete" = yes ] && ok "$unit has radio, port, and name" \
    || bad "$unit has an incomplete 20-device.conf"

  if systemctl is-enabled "$unit" >/dev/null 2>&1; then
    ok "$unit enabled"
  else
    bad "$unit configured but NOT enabled"
  fi
  note "$unit: $(systemctl is-active "$unit" 2>/dev/null) (radio-dependent)"
done
if [ "$lane_count" -eq 0 ]; then
  note "no lanes configured yet (expected until the app assigns controllers)"
else
  note "$lane_count templated lane(s) configured"
fi

echo "== radios (informational only) =="
radio_count=0
for radio_path in /sys/class/bluetooth/hci*; do
  [ -e "$radio_path" ] || continue
  radio_count=$((radio_count+1))
  note "$(basename "$radio_path") present"
done
[ "$radio_count" -gt 0 ] || note "no hci controllers present; attach USB radios and cold-reboot"

echo
echo "== $pass passed, $fail failed (software checks) =="
[ "$fail" -eq 0 ]
