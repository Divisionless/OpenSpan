#!/bin/bash
source /opt/openspan/env.sh
pass=1
say(){ printf '%s\n' "$1"; }
ok(){ printf '  \033[32mOK\033[0m  %s\n' "$1"; }
bad(){ printf '  \033[31mXX\033[0m  %s\n' "$1"; pass=0; }

say '===== 1. USB is xHCI + radio enumerated ====='
for i in $(seq 30); do lsusb 2>/dev/null | grep -qi 8087 && break; sleep 1; done
if lsusb 2>/dev/null | grep -qi 8087; then ok "radio present: $(lsusb | grep -i 8087)"; else bad 'radio 8087 MISSING'; fi
if lsusb -t 2>/dev/null | grep -qi xhci; then ok 'on xHCI controller'; else bad 'NOT on xhci'; fi

say '===== 2. HCI controller UP (no -110 wedge) ====='
if btmgmt info 2>/dev/null | grep -q 'Index list with 1'; then ok 'controller up'; else bad 'controller DOWN'; fi
werr=$(dmesg 2>/dev/null | grep -iE 'hci0.*(failed|-110|timeout)' | tail -3)
if [ -z "$werr" ]; then ok 'no HCI errors in dmesg'; else bad "HCI errors: $werr"; fi

say '===== 3. autosuspend disabled (needs the real boot) ====='
gsus=$(cat /proc/cmdline 2>/dev/null | tr ' ' '\n' | grep autosuspend)
if echo "$gsus" | grep -q 'usbcore.autosuspend=-1'; then ok "kernel cmdline: $gsus"; else bad "cmdline missing autosuspend=-1 (got: $gsus)"; fi
bmod=$(cat /sys/module/btusb/parameters/enable_autosuspend 2>/dev/null)
if [ "$bmod" = "N" ] || [ "$bmod" = "0" ]; then ok "btusb enable_autosuspend=$bmod"; else bad "btusb autosuspend=$bmod (want N/0)"; fi

say '===== 4. the 7 services active ====='
for s in openspan-btready openspan-agent openspan-dbus openspan-pipewire openspan-wireplumber openspan-pipewire-pulse openspan-udprecv; do
  st=$(systemctl is-active "$s" 2>/dev/null)
  if [ "$st" = active ]; then ok "$s"; else bad "$s = $st"; fi
done

say '===== 5. old cruft NOT running ====='
for s in openspan-jbl openspan-hold openspan-audio; do
  st=$(systemctl is-active "$s" 2>/dev/null)
  if [ "$st" != active ]; then ok "$s = $st"; else bad "$s is ACTIVE (cruft!)"; fi
done
if ps -eo args 2>/dev/null | grep 'scan on' | grep -qv grep; then bad 'a scan process is running'; else ok 'no scan process'; fi

say '===== 6. A2DP endpoints registered (WirePlumber) ====='
ep=$(journalctl -u bluetooth --no-pager -b 2>/dev/null | grep -c 'Endpoint registered')
if [ "${ep:-0}" -gt 0 ]; then ok "$ep A2DP endpoints registered"; else bad '0 endpoints (WirePlumber not registering)'; fi

say '===== 7. pairing agent registered ====='
if systemctl is-active openspan-agent >/dev/null 2>&1 && pgrep -f 'bt-agent' >/dev/null; then ok 'bt-agent running'; else bad 'bt-agent NOT running'; fi

say '===== 8. discovery quiet (busctl, no false-positive) ====='
disc=$(busctl --system get-property org.bluez /org/bluez/hci0 org.bluez.Adapter1 Discovering 2>/dev/null)
if echo "$disc" | grep -q 'false'; then ok "discovering=$disc"; else bad "discovering=$disc (something is scanning)"; fi

say '===== 9. bridge listening on UDP 4010 ====='
if ss -uln 2>/dev/null | grep -q ':4010'; then ok 'udp:4010 bound'; else bad 'udp:4010 not bound'; fi

echo
if [ "$pass" = 1 ]; then printf '\033[42m\033[30m  FULL GREEN  \033[0m\n'; else printf '\033[41m  NOT GREEN — see XX above  \033[0m\n'; fi
