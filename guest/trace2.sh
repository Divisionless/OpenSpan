#!/bin/bash
# Proper capture: binary btsnoop + continuous key burst, then decode.
rm -f /tmp/cap.log
stdbuf -oL btmon -w /tmp/cap.btsnoop > /dev/null 2>&1 &
BTMON=$!
sleep 1

# Fire a 3-second burst of the letter 'x' so there's plenty to catch.
python3 - <<'PY' &
import socket, time
s = socket.create_connection(("127.0.0.1", 9955))
end = time.time() + 3
n = 0
while time.time() < end:
    s.send(b'{"cmd":"keys","mods":0,"keys":[27]}\n')  # 27 = 'x'
    s.recv(50)
    n += 1
    time.sleep(0.1)
print("bursts sent:", n)
PY
BURST=$!
wait $BURST
sleep 1
kill $BTMON 2>/dev/null
sleep 1

echo "===DECODED CAPTURE (tail)==="
btmon -r /tmp/cap.btsnoop 2>/dev/null | grep -nE 'Data TX|Data RX|Channel|Connect|Configure|L2CAP|HIDP|Handshake|a1 |Frame' | tail -40

echo "===L2CAP CHANNEL STATES==="
cat /sys/kernel/debug/bluetooth/hci0/l2cap 2>/dev/null || echo "(debugfs l2cap not available)"
