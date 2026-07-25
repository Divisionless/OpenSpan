#!/bin/bash
# Wait up to 100s for the selected Bluetooth radio to enumerate and load its
# firmware. On a cold VM boot the USB btusb driver can register ~60s in, so
# radio-dependent services must not start before it exists.
HCI="${OPENSPAN_ADAPTER:-hci0}"
case "$HCI" in hci[0-9]*) ;; *) echo "invalid adapter $HCI" >&2; exit 1;; esac
for i in $(seq 100); do
  if hciconfig "$HCI" >/dev/null 2>&1; then
    hciconfig "$HCI" up 2>/dev/null
    exit 0
  fi
  sleep 1
done
# Timed out. Exit 0 anyway (a hard-fail here would just crash-loop the daemon
# with no better outcome), but leave a clear breadcrumb — the usual cause is
# the USB Bluetooth radio not being passed through to the VM.
echo "wait-hci0: $HCI never enumerated after 100s — check the VM's USB" \
     "Bluetooth passthrough (VBoxManage list usbhost / the USB filter)." >&2
exit 0
