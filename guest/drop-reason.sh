#!/bin/bash
source /opt/openspan/env.sh
echo '=== recent disconnect reason (bluez + kernel) ==='
journalctl --no-pager --since '3 min ago' 2>/dev/null | grep -iE 'B3:BD|disconn|timeout|supervision|link.*(lost|key)|terminat|hci0.*(err|fail)|abnormal|reason' | tail -20
echo '=== kernel hci events ==='
dmesg 2>/dev/null | grep -iE 'hci0|link|disconn|timeout' | tail -6
