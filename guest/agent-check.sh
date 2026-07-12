#!/bin/bash
echo '=== bt-agent (bluez-tools)? ==='
which bt-agent 2>/dev/null || echo 'not installed'
echo '=== python3 dbus module? ==='
python3 -c 'import dbus; print("python3-dbus OK")' 2>&1 | head -1
echo '=== python3 gi/GLib (pygobject)? ==='
python3 -c 'from gi.repository import GLib; print("gi OK")' 2>&1 | head -1
echo '=== bluez version ==='
bluetoothd --version 2>/dev/null || /usr/lib/bluetooth/bluetoothd --version 2>/dev/null
echo '=== is a default agent registered right now? ==='
busctl --system introspect org.bluez /org/bluez org.bluez.AgentManager1 2>/dev/null | grep -iE 'method' | head
echo '=== apt available (to install bluez-tools if needed)? ==='
apt-get --version 2>/dev/null | head -1 || echo 'no apt'
