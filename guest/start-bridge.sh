#!/bin/bash
source /opt/openspan/env.sh
DEV=/org/bluez/hci0/dev_B3_BD_E8_69_E5_59
conn() { busctl --system get-property org.bluez "$DEV" org.bluez.Device1 Connected 2>/dev/null | awk '{print $2}'; }
systemctl restart openspan-udprecv
sleep 4
echo "--- onn connected? --- connected=$(conn)"
echo '--- stream (mp3 -> onn)? ---'
wpctl status 2>&1 | sed -n '/Streams:/,/Settings/p' | grep -iE 'pw-play|playback_F' | head -3
echo '--- 15s hold with the SIMPLE bridge running ---'
for t in 5 10 15; do sleep 5; echo "t=${t}s connected=$(conn)"; done
echo '--- Protocol-not-available during (want 0) ---'
journalctl -u bluetooth --no-pager --since '25 sec ago' 2>/dev/null | grep -c 'Protocol not available'
