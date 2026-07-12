#!/bin/bash
# Ensure the adapter is powered with BOTH BR/EDR (for A2DP audio) and LE (for
# the iPad HID). If it's ALREADY in that state, do nothing -- a power-cycle
# here would drop an active audio stream, coupling the keyboard restart to the
# audio. Only cycle when a bearer genuinely needs toggling (e.g. right after a
# fresh USB re-enumeration on boot).
cur=$(btmgmt info 2>/dev/null | grep 'current settings')
if echo "$cur" | grep -qw powered \
   && echo "$cur" | grep -qw 'br/edr' \
   && echo "$cur" | grep -qw le; then
  exit 0   # already dual-mode + powered: leave audio untouched
fi
btmgmt power off >/dev/null 2>&1
btmgmt bredr on  >/dev/null 2>&1
btmgmt le on     >/dev/null 2>&1
btmgmt power on  >/dev/null 2>&1
sleep 1
