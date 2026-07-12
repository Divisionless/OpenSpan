#!/bin/bash
systemctl reset-failed openspan-wireplumber openspan-udprecv openspan-pipewire-pulse 2>/dev/null
systemctl daemon-reload
systemctl enable openspan-dbus 2>&1 | tail -1
systemctl restart openspan-pipewire openspan-dbus
sleep 3
systemctl restart openspan-wireplumber openspan-pipewire-pulse
sleep 7
systemctl restart openspan-udprecv
sleep 4
echo '=== services (want ALL active) ==='
systemctl is-active openspan-pipewire openspan-dbus openspan-wireplumber openspan-pipewire-pulse openspan-udprecv openspan-agent
echo '=== wireplumber STABLE (same PID over 4s)? ==='
pgrep -x wireplumber | head -1; sleep 4; echo 'after 4s:'; pgrep -x wireplumber | head -1
echo '=== A2DP endpoints registered (want >0)? ==='
journalctl -u bluetooth --no-pager --since '25 sec ago' 2>/dev/null | grep -c 'Endpoint registered'
echo '=== bridge listening on 4010? ==='
ss -ulnp 2>/dev/null | grep -q 4010 && echo yes || echo NO
