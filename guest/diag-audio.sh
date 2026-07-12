#!/bin/bash
echo '=== pipewire socket location ==='
find /run /root /tmp /home -name 'pipewire-0' 2>/dev/null
echo '=== pipewire pid + its XDG_RUNTIME_DIR ==='
PW=$(pgrep -x pipewire | head -1)
echo "pipewire pid: $PW"
tr '\0' '\n' < /proc/$PW/environ 2>/dev/null | grep -iE 'XDG_RUNTIME|PIPEWIRE_RUNTIME|USER='
echo '=== /run/user/0 dir + contents ==='
ls -ld /run/user/0 2>&1
ls -la /run/user/0/ 2>&1 | head
echo '=== wireplumber pid + its XDG_RUNTIME_DIR ==='
WP=$(pgrep -x wireplumber | head -1)
tr '\0' '\n' < /proc/$WP/environ 2>/dev/null | grep -iE 'XDG_RUNTIME'
echo '=== wpctl via /run/user/0 ==='
export XDG_RUNTIME_DIR=/run/user/0
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/0/bus
wpctl status 2>&1 | head -6
