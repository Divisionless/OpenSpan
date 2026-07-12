#!/bin/bash
source /opt/openspan/env.sh
echo '=== libspa bluez5 plugin present? (required for A2DP) ==='
ls /usr/lib/*/spa-0.2/bluez5/ 2>/dev/null | head || echo 'MISSING'
echo '=== onn connected (busctl)? ==='
busctl --system get-property org.bluez /org/bluez/hci0/dev_B3_BD_E8_69_E5_59 org.bluez.Device1 Connected 2>/dev/null
echo '=== does WirePlumber SEE the onn as a device? ==='
wpctl status 2>&1 | sed -n '/Devices:/,/Sinks:/p' | head -12
echo '=== full wpctl device/sink names ==='
pw-dump 2>/dev/null | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except: sys.exit()
for o in d:
    p=(o.get('info') or {}).get('props') or {}
    n=str(p.get('device.name','') or p.get('node.name',''))
    if 'bluez' in n.lower() or p.get('device.api')=='bluez5':
        print(o.get('type','').split('/')[-1], '|', n, '|', 'profile=', p.get('device.profile.name',''))
"
