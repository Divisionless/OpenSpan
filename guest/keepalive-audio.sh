#!/bin/bash
# Continuously feed a near-silent local stream to the connected Bluetooth
# sink. A solid local stream keeps the A2DP transport from idling -- this is
# the technique that held the earbuds for 25s in testing, independent of the
# Windows audio path (which can stall when the node churns).
source /opt/openspan/env.sh

find_bt() {
  pw-dump 2>/dev/null | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit()
for o in d:
    p = (o.get('info') or {}).get('props') or {}
    if p.get('media.class') == 'Audio/Sink' and str(p.get('node.name', '')).startswith('bluez_output.'):
        print(p['node.name']); break
"
}

while true; do
  NODE=$(find_bt)
  if [ -n "$NODE" ]; then
    echo "keepalive -> $NODE"
    python3 -c "
import sys
# ~inaudible +/-3 dither, 480 stereo frames per chunk
chunk = bytes([3, 0, 253, 255]) * 480
while True:
    try:
        sys.stdout.buffer.write(chunk)
        sys.stdout.flush()
    except Exception:
        break
" | pw-play --target="$NODE" -P node.dont-reconnect=true \
        --format=s16 --rate=48000 --channels=2 --latency=100ms - 2>/dev/null
    echo "keepalive stream ended (device gone)"
  fi
  sleep 1
done
