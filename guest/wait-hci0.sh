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
# Timed out. Exit 0 anyway (a hard-fail here would just crash-loop the daemon
# with no better outcome), but leave a clear breadcrumb — the usual cause is
# the USB Bluetooth radio not being passed through to the VM.
echo "wait-hci0: hci0 never enumerated after 100s — check the VM's USB" \
     "Bluetooth passthrough (VBoxManage list usbhost / the USB filter)." >&2
exit 0
