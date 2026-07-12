#!/bin/bash
rm -f /tmp/cap.btsnoop /tmp/decode.txt
btmon -w /tmp/cap.btsnoop > /dev/null 2>&1 &
BTMON=$!
sleep 1
python3 - <<'PY'
import socket, time
s = socket.create_connection(("127.0.0.1", 9955))
for _ in range(10):
    s.send(b'{"cmd":"keys","mods":0,"keys":[27]}\n'); s.recv(50)
    time.sleep(0.15)
    s.send(b'{"cmd":"keys","mods":0,"keys":[]}\n'); s.recv(50)
    time.sleep(0.15)
print("done")
PY
sleep 1
kill $BTMON 2>/dev/null
sleep 1
btmon -r /tmp/cap.btsnoop > /tmp/decode.txt 2>&1
echo "total decoded lines: $(wc -l < /tmp/decode.txt)"
echo "ACL frame count: $(grep -c 'ACL Data' /tmp/decode.txt)"
