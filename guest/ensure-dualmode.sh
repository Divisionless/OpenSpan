#!/bin/bash
# Ensure the adapter is powered with BOTH BR/EDR (for A2DP audio) and LE (for
# the iPad HID). If it's ALREADY in that state, do nothing -- a power-cycle
# here would drop an active audio stream, coupling the keyboard restart to the
# audio. Only cycle when a bearer genuinely needs toggling (e.g. right after a
# fresh USB re-enumeration on boot).
HCI="${OPENSPAN_ADAPTER:-hci0}"
IDX="${HCI#hci}"
case "$IDX" in *[!0-9]*|"") echo "invalid adapter $HCI" >&2; exit 1;; esac
cur=$(btmgmt --index "$IDX" info 2>/dev/null | grep 'current settings')
if echo "$cur" | grep -qw powered \
   && echo "$cur" | grep -qw 'br/edr' \
   && echo "$cur" | grep -qw le; then
  exit 0   # already dual-mode + powered: leave audio untouched
fi
btmgmt --index "$IDX" power off >/dev/null 2>&1
btmgmt --index "$IDX" bredr on  >/dev/null 2>&1
btmgmt --index "$IDX" le on     >/dev/null 2>&1
btmgmt --index "$IDX" power on  >/dev/null 2>&1
sleep 1
