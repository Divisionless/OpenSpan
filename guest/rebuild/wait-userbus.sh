#!/bin/bash
# Block until the persistent user session bus exists. FAIL (exit 1) on timeout
# so the service actually reports failure instead of starting into a missing bus.
for i in $(seq 40); do
  [ -S /run/user/0/bus ] && exit 0
  sleep 0.5
done
echo 'FAIL: /run/user/0/bus never appeared (is linger enabled for root?)' >&2
exit 1
