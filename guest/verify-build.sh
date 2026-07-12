#!/bin/bash
source /opt/openspan/env.sh
systemctl daemon-reload
systemctl enable openspan-udprecv 2>&1 | tail -1
systemctl restart openspan-udprecv
sleep 5
echo '=== services active now? ==='
systemctl is-active openspan-pipewire openspan-wireplumber openspan-pipewire-pulse openspan-udprecv openspan-agent bluetooth
echo '=== udp bridge listening on 4010? ==='
ss -ulnp 2>/dev/null | grep -q 4010 && echo yes || echo NO
echo '=== adapter advertises A2DP Source (110a = endpoints live)? ==='
busctl --system get-property org.bluez /org/bluez/hci0 org.bluez.Adapter1 UUIDs 2>/dev/null | grep -c '110a'
echo '=== BOOT CHAIN enabled for next reboot? ==='
for s in openspan-btready openspan-agent openspan-pipewire openspan-wireplumber openspan-pipewire-pulse openspan-udprecv; do
  printf '%-28s ' "$s"; systemctl is-enabled "$s" 2>/dev/null
done
echo '=== old hacky service gone? ==='
systemctl is-enabled openspan-audio 2>/dev/null || echo 'openspan-audio: disabled (good)'
