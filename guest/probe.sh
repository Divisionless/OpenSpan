#!/bin/bash
python3 - <<'PY'
import socket
s = socket.create_connection(("127.0.0.1", 9955))
for msg in (b'{"cmd":"status"}\n', b'{"cmd":"keys","mods":0,"keys":[27]}\n',
            b'{"cmd":"text","text":"Z"}\n'):
    s.send(msg)
    print(msg.strip().decode(), "->", s.recv(200).decode().strip())
PY
echo "--- daemon recent log ---"
journalctl -u openspan --since "-2min" --no-pager -o cat | tail -15
