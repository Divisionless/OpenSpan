#!/bin/bash
source /opt/openspan/env.sh
echo '=== processes that could be scanning ==='
ps -eo pid,args 2>/dev/null | grep -iE 'openspan-jbl|scan on|bluetoothctl' | grep -viE 'kill-scanner|grep'
echo '=== killing any scanner loops / orphaned scan processes ==='
pkill -f 'openspan-jbl.sh' 2>/dev/null
for p in $(ps -eo pid,args | grep -E 'scan on' | grep -v grep | awk '{print $1}'); do kill "$p" 2>/dev/null; done
echo '=== turn discovery OFF ==='
bluetoothctl scan off >/dev/null 2>&1
sleep 1
echo '=== Discovering now (want: no) ==='
bluetoothctl show 2>&1 | grep -i Discovering
echo '=== any scanners left? ==='
ps -eo pid,args 2>/dev/null | grep -E 'scan on|openspan-jbl.sh' | grep -v grep || echo 'NONE - clean'
