#!/bin/bash
source /opt/openspan/env.sh
echo '=== 1. any process running a scan? ==='
ps -eo pid,etimes,args 2>/dev/null | grep -iE 'scan on|StartDiscovery' | grep -v grep || echo 'NONE'
echo '=== 2. bluetoothctl / bt-agent / other bluez clients ==='
ps -eo pid,etimes,args 2>/dev/null | grep -iE 'bluetoothctl|bt-agent' | grep -v grep || echo 'NONE'
echo '=== 3. current Discovering state ==='
bluetoothctl show 2>&1 | grep -i Discovering
echo '=== 4. turn discovery OFF + kill any scan procs ==='
bluetoothctl scan off >/dev/null 2>&1
for p in $(ps -eo pid,args | grep 'scan on' | grep -v grep | awk '{print $1}'); do kill "$p" 2>/dev/null; done
sleep 2
echo '=== 5. Discovering right after (want: no) ==='
bluetoothctl show 2>&1 | grep -i Discovering
echo '=== 6. re-check after 6s -- does something turn it back ON? ==='
sleep 6
bluetoothctl show 2>&1 | grep -i Discovering
