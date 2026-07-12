#!/bin/bash
# Boot helper: the USB radio can take ~8-60s to enumerate, often after
# bluetoothd started -- so bluez can end up with no controller. Wait for the
# radio, make sure bluez actually sees it, power it on. Then bounce the
# CURRENT audio stack (WirePlumber + bridge) so A2DP endpoints register
# against the now-present controller.
#
# NOTE: the retired stack (openspan-audio / openspan-hold / openspan-jbl) is
# intentionally NOT touched here. Those are disabled and must stay dead --
# openspan-jbl was the auto-scanner that disrupted A2DP, openspan-hold the old
# keepalive, openspan-audio the old audio-up.sh. Do not re-add them.
source /opt/openspan/env.sh
/opt/openspan/wait-hci0.sh
sleep 3
# If bluez has no controller registered, restart bluetoothd so it detects hci0.
for try in 1 2 3; do
  if btmgmt info 2>/dev/null | grep -q 'Index list with 0'; then
    systemctl restart bluetooth
    sleep 6
  else
    break
  fi
done
btmgmt power on >/dev/null 2>&1
# Radio is confirmed present: (re)register A2DP endpoints against the live
# controller, then restart the UDP->sink bridge.
systemctl restart openspan-wireplumber
sleep 4
systemctl restart openspan-udprecv
