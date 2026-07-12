#!/bin/bash
# Get system-mode PulseAudio talking to BlueZ (the fiddly permission bit).
set +e

# the 'pulse' system user needs group + D-Bus access to org.bluez
usermod -aG bluetooth,lp,audio pulse 2>/dev/null

cat > /etc/dbus-1/system.d/pulseaudio-bluetooth.conf <<'XML'
<!DOCTYPE busconfig PUBLIC
 "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <policy user="pulse">
    <allow send_destination="org.bluez"/>
    <allow send_interface="org.bluez.MediaEndpoint1"/>
    <allow send_interface="org.bluez.MediaTransport1"/>
  </policy>
</busconfig>
XML
systemctl reload dbus 2>/dev/null || systemctl restart dbus 2>/dev/null

pkill -9 pulseaudio 2>/dev/null; sleep 1
pulseaudio --system --disallow-exit --disallow-module-loading \
    --exit-idle-time=-1 --daemonize=yes
sleep 3

export PULSE_SERVER=unix:/var/run/pulse/native
echo "--- pulseaudio up? ---"
pactl info 2>&1 | grep -E 'Server Name|Server Version' || echo "pactl NOT responding"
echo "--- bluetooth modules ---"
pactl list modules short 2>/dev/null | grep -i bluetooth || echo "(none)"
echo "--- sinks/sources ---"
pactl list short sinks 2>/dev/null; pactl list short sources 2>/dev/null
