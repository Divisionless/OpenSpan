#!/bin/bash
source /opt/openspan/env.sh 2>/dev/null
pass=1
ok(){  printf '  \033[32mOK\033[0m  %s\n' "$1"; }
bad(){ printf '  \033[31mXX\033[0m  %s\n' "$1"; pass=0; }

echo '== 1. linger persisted across reboot =='
[ "$(loginctl show-user root -p Linger --value 2>/dev/null)" = yes ] && ok 'linger=yes' || bad 'linger NOT set'
echo '== 2. real user session bus + pipewire socket =='
[ -S /run/user/0/bus ]        && ok 'user bus present'        || bad 'no /run/user/0/bus'
[ -S /run/user/0/pipewire-0 ] && ok 'pipewire socket present' || bad 'no /run/user/0/pipewire-0'
echo '== 3. audio + bt services active =='
for s in openspan-btready openspan-agent openspan-pipewire openspan-wireplumber openspan-pipewire-pulse openspan-udprecv; do
  st=$(systemctl is-active "$s" 2>/dev/null); [ "$st" = active ] && ok "$s" || bad "$s = $st"
done
echo '== 4. no cruft / hand-rolled dbus running =='
for s in openspan-dbus openspan-jbl openspan-hold openspan-audio; do
  st=$(systemctl is-active "$s" 2>/dev/null); [ "$st" != active ] && ok "$s inactive" || bad "$s ACTIVE"
done
echo '== 5. radio on xHCI + controller up (no wedge) =='
lsusb 2>/dev/null | grep -qi 8087 && ok 'radio present' || bad 'radio MISSING'
btmgmt info 2>/dev/null | grep -q 'Index list with 1' && ok 'controller up' || bad 'controller down'
echo '== 6. A2DP endpoints registered =='
n=$(journalctl -u bluetooth --no-pager -b 2>/dev/null | grep -c 'Endpoint registered')
[ "${n:-0}" -gt 0 ] && ok "$n endpoints" || bad '0 endpoints'
echo '== 7. wireplumber healthy =='
systemctl is-active openspan-wireplumber >/dev/null 2>&1 \
  && ok "wireplumber active (NRestarts=$(systemctl show openspan-wireplumber -p NRestarts --value))" \
  || bad 'wireplumber down'
echo '== 8. bridge listening on udp 4010 =='
ss -uln 2>/dev/null | grep -q ':4010' && ok 'udp:4010 bound' || bad 'udp:4010 not bound'
echo '== 9. usb autosuspend off (kernel cmdline) =='
grep -q 'usbcore.autosuspend=-1' /proc/cmdline && ok 'autosuspend=-1' || bad 'autosuspend cmdline missing'
echo '== 10. discovery quiet =='
d=$(busctl --system get-property org.bluez /org/bluez/hci0 org.bluez.Adapter1 Discovering 2>/dev/null)
echo "$d" | grep -q false && ok "discovering=$d" || bad "discovering=$d"
echo '== 11. onn bond intact (auto-reconnects when woken) =='
busctl --system get-property org.bluez /org/bluez/hci0/dev_58_C8_23_4B_16_32 org.bluez.Device1 Paired 2>/dev/null | grep -q true \
  && ok 'onn paired+trusted' || bad 'onn bond missing'

echo
[ "$pass" = 1 ] && printf '\033[42m\033[30m  BOOT CLEAN — survives reboot  \033[0m\n' \
               || printf '\033[41m  ISSUES ABOVE  \033[0m\n'
