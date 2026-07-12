#!/bin/bash
source /opt/openspan/env.sh
echo '=== openspan-jbl service state ==='
systemctl is-active openspan-jbl 2>/dev/null
echo '=== any openspan-jbl.sh process? ==='
pgrep -af 'openspan-jbl' | grep -v grep || echo 'not running'
echo '=== catch the scan process + trace its ancestry ==='
pid=''
for i in $(seq 15); do
  pid=$(ps -eo pid,args | grep 'scan on' | grep -v grep | awk '{print $1}' | head -1)
  [ -n "$pid" ] && break
  sleep 1
done
echo "scan pid: $pid"
p=$pid
for i in 1 2 3 4 5 6; do
  { [ -z "$p" ] || [ "$p" = "0" ] || [ "$p" = "1" ]; } && break
  echo "  pid=$p ppid=$(ps -o ppid= -p "$p" 2>/dev/null|tr -d ' '): $(ps -o args= -p "$p" 2>/dev/null | cut -c1-70)"
  p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
done
echo '=== all bt/openspan/scan processes ==='
ps -eo pid,ppid,args | grep -iE 'bluetoothctl|openspan|scan on' | grep -v grep
