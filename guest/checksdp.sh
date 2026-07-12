#!/bin/bash
echo "=== Local SDP records (HID?) ==="
sdptool browse local 2>&1 | grep -iA6 -E "HID|Human|0x1124" | head -40
echo "=== Our adapter class right now ==="
hciconfig hci0 class
echo "=== Is our profile PSM listening? ==="
ss -l -x 2>/dev/null | grep -iE "l2cap|bluetooth" || echo "(ss shows no unix; check /proc)"
echo "=== HID service via sdptool records ==="
sdptool records local 2>&1 | grep -iA3 "Service Name" | head -30
