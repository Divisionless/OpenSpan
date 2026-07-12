#!/bin/bash
source /opt/openspan/env.sh 2>/dev/null
IPAD=""
for p in $(busctl --system tree org.bluez 2>/dev/null | grep -oE 'dev(_[0-9A-Fa-f]{2}){6}' | sort -u); do
  n=$(busctl --system get-property org.bluez "/org/bluez/hci0/$p" org.bluez.Device1 Alias 2>/dev/null | cut -d'"' -f2)
  echo "$n" | grep -qi ipad && { IPAD=$(echo "$p" | sed 's/dev_//; s/_/:/g'); break; }
done
echo "clearing iPad bond: ${IPAD:-none}"
[ -n "$IPAD" ] && { bluetoothctl disconnect "$IPAD" >/dev/null 2>&1; bluetoothctl remove "$IPAD" >/dev/null 2>&1; }
echo '>>> FORGET "OpenSpan Keyboard" on the iPad + RE-PAIR now. Capturing 90s...'
: > /tmp/int.log
timeout 90 btmon 2>/dev/null | grep --line-buffered -iE 'Connection interval:|Connection latency:|Supervision timeout:' >> /tmp/int.log
echo '===== CONNECTION INTERVAL (want ~15ms/0x000c, latency 0) ====='
grep -iE 'Connection interval:' /tmp/int.log | sort -u
grep -iE 'Connection latency:'  /tmp/int.log | sort -u | head -2
echo '===== subscribed after re-pair? ====='
python3 -c "import socket; s=socket.create_connection(('127.0.0.1',9955),2); s.sendall(b'{\"cmd\":\"status\"}\n'); print(s.recv(200).decode())" 2>&1
