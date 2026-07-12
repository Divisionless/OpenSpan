#!/bin/bash
echo '=== boot chain enabled for fresh reboot? ==='
for s in openspan-btready openspan-agent openspan-dbus openspan-pipewire openspan-wireplumber openspan-pipewire-pulse openspan-udprecv; do
  printf '%-26s ' "$s"; systemctl is-enabled "$s" 2>/dev/null
done
echo '=== old hacky services OFF? ==='
for s in openspan-audio openspan-hold openspan-jbl; do
  printf '%-20s ' "$s"; systemctl is-enabled "$s" 2>/dev/null || echo disabled
done
