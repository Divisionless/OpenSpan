#!/bin/bash
echo "pipewire procs:      $(pgrep -xc pipewire)"
echo "pipewire-pulse procs:$(pgrep -xc pipewire-pulse)"
echo "wireplumber procs:   $(pgrep -xc wireplumber)"
echo "dbus session daemons:$(pgrep -fc 'dbus-daemon --session')"
echo "=== openspan-audio NRestarts ==="
systemctl show openspan-audio -p NRestarts --value
echo "=== wireplumber PID stable over 4s? ==="
pgrep -x wireplumber | tr '\n' ' '; echo "(now)"
sleep 4
pgrep -x wireplumber | tr '\n' ' '; echo "(after 4s)"
