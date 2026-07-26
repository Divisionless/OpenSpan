#!/bin/bash
# Make sure bluetoothd is actually ANSWERING before a radio operation.
#
# bluetoothd can sit in systemd state "active" (or wedge in
# "deactivating (stop-sigterm)" and refuse to die) while its D-Bus interface
# stops replying.  Every caller then blocks until its ssh timeout, and an
# adapter enumeration comes back EMPTY -- which reads as "there are no radios"
# instead of "the Bluetooth daemon is not responding".  That cost a whole
# debugging session, so probe first and unwedge rather than hang.
#
# Exit 0 = the bus answers (already healthy, or recovered here).
set -u

probe() {
    timeout 6 python3 - <<'PY' >/dev/null 2>&1
import dbus
bus = dbus.SystemBus()
om = dbus.Interface(bus.get_object("org.bluez", "/"),
                    "org.freedesktop.DBus.ObjectManager")
om.GetManagedObjects()
PY
}

if probe; then
    echo "BT_OK"
    exit 0
fi

echo "BT_WEDGED -- bluetoothd is not answering D-Bus; recovering" >&2

# SIGTERM is what it ignores when wedged, so go straight to KILL. Stopping the
# HID lanes first releases the GATT objects they registered -- and remember
# exactly WHICH lanes were stopped so the same ones come back. (An earlier
# version stopped the per-device lanes but restarted only the legacy units,
# leaving every real device dead after a recovery.)
STOPPED=""
for unit in $(systemctl list-units --state=active --no-legend 'openspanble@*' \
              2>/dev/null | awk '{print $1}'); do
    systemctl stop "$unit" >/dev/null 2>&1
    STOPPED="$STOPPED $unit"
done
systemctl kill -s KILL bluetooth >/dev/null 2>&1
sleep 2
systemctl reset-failed bluetooth >/dev/null 2>&1
systemctl start bluetooth >/dev/null 2>&1

for _ in $(seq 15); do
    sleep 1
    if probe; then
        # bring back exactly the lanes we stopped
        # shellcheck disable=SC2086
        [ -n "$STOPPED" ] && systemctl start $STOPPED >/dev/null 2>&1
        echo "BT_RECOVERED"
        exit 0
    fi
done

echo "BT_UNRECOVERED -- bluetoothd still not answering after a forced restart" >&2
exit 1
