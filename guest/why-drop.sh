#!/bin/bash
source /opt/openspan/env.sh
echo '--- onn connected now? ---'
busctl --system get-property org.bluez /org/bluez/hci0/dev_B3_BD_E8_69_E5_59 org.bluez.Device1 Connected 2>/dev/null || echo 'device gone'
echo '--- disconnect reason (bluez + kernel, last 3 min) ---'
journalctl --no-pager --since '3 min ago' 2>/dev/null | grep -iE 'B3_BD|B3:BD|disconnect|timeout|supervision|terminat|hci0.*(err|lost)|abnormal|reason' | grep -viE 'Adv Monitor|Endpoint (reg|unreg)' | tail -18
echo '--- bridge (udp_to_sink) recent log ---'
journalctl -u openspan-udprecv --no-pager -n 6 2>/dev/null | tail -4
echo '--- is anything scanning? (should be no) ---'
bluetoothctl show 2>&1 | grep -i Discovering
