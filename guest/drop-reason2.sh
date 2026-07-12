#!/bin/bash
source /opt/openspan/env.sh
echo '=== kernel HCI events (the real disconnect reason) ==='
dmesg 2>/dev/null | grep -iE 'Bluetooth|hci0|B3' | tail -12
echo '=== bluez log around the drop ==='
journalctl -u bluetooth --no-pager --since '3 min ago' 2>/dev/null | grep -iE 'B3_BD|disconnect|timeout|terminat|reason|abnormal|lost' | grep -viE 'Adv Monitor|Endpoint (reg|unreg)' | tail -10
echo '=== onn connected now? ==='
busctl --system get-property org.bluez /org/bluez/hci0/dev_B3_BD_E8_69_E5_59 org.bluez.Device1 Connected 2>/dev/null || echo gone
echo '=== current A2DP codec + config (bandwidth) ==='
wpctl status 2>&1 | grep -i onn
