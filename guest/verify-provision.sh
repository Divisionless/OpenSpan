#!/bin/bash
# Verify what the provisioner set up — the NON-radio half (units installed +
# enabled, the linger user bus, config files in place). The radio-dependent
# runtime (openspanble actually advertising, A2DP audio) can only be confirmed
# by a human with the dongle attached + an iPad/earbuds, so it is not asserted
# here. Prints a PASS/FAIL line per check and exits non-zero on any failure.
pass=0; fail=0
ok()   { printf '  \033[1;32mPASS\033[0m  %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  \033[1;31mFAIL\033[0m  %s\n' "$1"; fail=$((fail+1)); }
note() { printf '  ----  %s\n' "$1"; }

echo "== units enabled =="
for u in openspanble openspan-agent openspan-btready openspan-pipewire \
         openspan-wireplumber openspan-pipewire-pulse openspan-udprecv; do
  if systemctl is-enabled "$u" >/dev/null 2>&1; then ok "$u enabled"
  else bad "$u NOT enabled"; fi
done

echo "== non-radio services active =="
for u in openspan-pipewire openspan-wireplumber openspan-pipewire-pulse \
         openspan-udprecv openspan-agent; do
  st=$(systemctl is-active "$u" 2>/dev/null)
  [ "$st" = active ] && ok "$u active" || bad "$u is '$st' (expected active)"
done
# openspanble needs the radio; report its state without failing on 'no radio'
note "openspanble: $(systemctl is-active openspanble 2>/dev/null) (needs the dongle to go active)"

echo "== the audio footing =="
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
         /opt/openspan/openspan_ble.py /opt/openspan/udp_to_sink.py; do
  [ -f "$f" ] && ok "$f" || bad "$f MISSING"
done
grep -q 'suspend-timeout-seconds"\] = 0' \
  /usr/share/wireplumber/bluetooth.lua.d/50-bluez-config.lua 2>/dev/null \
  && ok "A2DP suspend disabled in bluez config" \
  || bad "suspend-timeout not set in bluez config"

echo "== radio (informational — attach the dongle for the real test) =="
if hciconfig hci0 >/dev/null 2>&1; then note "hci0 present"
else note "hci0 absent — attach the USB radio + reboot, then pair the iPad"; fi

echo
echo "== $pass passed, $fail failed (non-radio checks) =="
[ "$fail" -eq 0 ]
