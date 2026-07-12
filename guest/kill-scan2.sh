#!/bin/bash
source /opt/openspan/env.sh
echo '=== what is the scan process + its parent? ==='
for p in $(ps -eo pid,args | grep 'scan on' | grep -v grep | awk '{print $1}'); do
  ppid=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
  echo "scan pid=$p parent=$ppid: $(ps -o args= -p "$ppid" 2>/dev/null)"
  kill "$p" 2>/dev/null
done
bluetoothctl scan off >/dev/null 2>&1
sleep 3
echo '=== discovery now (busctl) ==='
busctl --system get-property org.bluez /org/bluez/hci0 org.bluez.Adapter1 Discovering 2>/dev/null
echo '=== wait 10s, does a scan come BACK? ==='
sleep 10
busctl --system get-property org.bluez /org/bluez/hci0 org.bluez.Adapter1 Discovering 2>/dev/null
ps -eo args 2>/dev/null | grep 'scan on' | grep -v grep && echo 'SCAN RESPAWNED' || echo 'CLEAN - stays off'
