#!/bin/bash
# Wait up to 100s for the Bluetooth radio (hci0) to enumerate and load its
# firmware. On a cold VM boot the USB btusb driver can register ~60s in, so
# radio-dependent services must not start before it exists.
for i in $(seq 100); do
  if hciconfig hci0 >/dev/null 2>&1; then
    hciconfig hci0 up 2>/dev/null
    exit 0
  fi
  sleep 1
done
exit 0
