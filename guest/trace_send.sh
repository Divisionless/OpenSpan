#!/bin/bash
# Capture L2CAP traffic while sending a few keystrokes to the iPad.
timeout 7 btmon > /tmp/btmon.txt 2>&1 &
BTMON=$!
sleep 1
python3 - <<'PY'
import socket, time
s = socket.create_connection(("127.0.0.1", 9955))
s.send(b'{"cmd":"text","text":"abc"}\n')
print("send reply:", s.recv(100))
time.sleep(1)
PY
sleep 5
echo "===INTERRUPT-CHANNEL-FRAMES==="
# PSM 0x0013 = interrupt (19); look for HID data frames (0xa1..)
grep -nE 'channel 0x00(11|13)|a1 01|a1 02|Frame|Handshake|HIDP' /tmp/btmon.txt | head -40
echo "===RAW-TAIL==="
tail -50 /tmp/btmon.txt
