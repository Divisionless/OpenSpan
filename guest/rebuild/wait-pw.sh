#!/bin/bash
# Block until BOTH the user bus and PipeWire's socket exist. FAIL on timeout.
for i in $(seq 40); do
  [ -S /run/user/0/bus ] && [ -S /run/user/0/pipewire-0 ] && exit 0
  sleep 0.5
done
echo 'FAIL: /run/user/0/bus or /run/user/0/pipewire-0 never appeared' >&2
exit 1
