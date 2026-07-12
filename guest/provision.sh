#!/bin/bash
# OpenSpan guest provisioning — run once inside the Debian bridge VM as
# root. Idempotent: safe to re-run. Turns a stock Debian cloud image into
# the BLE-HID keyboard/mouse bridge.
set -e

echo "[provision] enabling non-free firmware + installing packages"
sed -i 's/Components: main$/Components: main non-free-firmware non-free contrib/' \
    /etc/apt/sources.list.d/debian.sources 2>/dev/null || true
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    bluez rfkill usbutils firmware-misc-nonfree firmware-iwlwifi \
    python3-dbus python3-gi >/dev/null

echo "[provision] configuring bluetoothd (LE-only, experimental, keyboard class)"
CONF=/etc/bluetooth/main.conf
sed -i 's/^#\?ControllerMode.*/ControllerMode = le/' "$CONF"
grep -q '^ControllerMode' "$CONF" || \
    sed -i 's/^\[General\]/[General]\nControllerMode = le/' "$CONF"
grep -q '^Experimental = true' "$CONF" || \
    sed -i 's/^\[General\]/[General]\nExperimental = true\nKernelExperimental = true/' "$CONF"
grep -q '^Class = 0x0005C0' "$CONF" || \
    sed -i 's/^\[General\]/[General]\nClass = 0x0005C0/' "$CONF"

echo "[provision] installing daemon + service"
install -Dm755 "$(dirname "$0")/openspan_ble.py" /opt/openspan/openspan_ble.py
install -Dm644 "$(dirname "$0")/openspanble.service" \
    /etc/systemd/system/openspanble.service
# retire the abandoned Classic daemon if present
systemctl disable --now openspan 2>/dev/null || true

systemctl daemon-reload
systemctl restart bluetooth
sleep 2
systemctl enable --now openspanble

echo "[provision] done. Advertising as 'OpenSpan Keyboard'."
echo "            Pair from the iPad; accept the pairing prompt."
systemctl is-active openspanble
