#!/bin/bash
source /opt/openspan/env.sh
echo '=== supervisor log (did it target the onn?) ==='
journalctl -u openspan-udprecv --no-pager -n 25 2>/dev/null | grep -iE 'targeting|receiver on|error|traceback' | tail -8
echo '=== bluetooth disconnect REASON ==='
journalctl -u bluetooth --no-pager -n 60 2>/dev/null | grep -iE 'disconnect|B3:BD|transport|timeout|terminat|reason' | tail -14
echo '=== wireplumber version ==='
dpkg-query -W -f='${Version}\n' wireplumber 2>/dev/null
echo '=== wireplumber config format (0.4=lua.d, 0.5=conf.d) ==='
ls -d /etc/wireplumber/*/ 2>/dev/null
ls /usr/share/wireplumber/*/ -d 2>/dev/null | head
echo '=== existing no-suspend rule ==='
cat /etc/wireplumber/bluetooth.lua.d/51-no-suspend.lua 2>/dev/null || echo 'none'
