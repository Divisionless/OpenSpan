#!/bin/bash
rm -f /tmp/cap2.btsnoop
btmon -w /tmp/cap2.btsnoop > /dev/null 2>&1 &
BTMON=$!
sleep 1
# Generate guaranteed HCI traffic: a short scan.
timeout 3 bluetoothctl scan on > /dev/null 2>&1
bluetoothctl scan off > /dev/null 2>&1
sleep 1
kill $BTMON 2>/dev/null
sleep 1
btmon -r /tmp/cap2.btsnoop > /tmp/decode2.txt 2>&1
echo "decoded lines: $(wc -l < /tmp/decode2.txt)"
echo "HCI Command count: $(grep -c 'HCI Command' /tmp/decode2.txt)"
echo "HCI Event count: $(grep -c 'HCI Event' /tmp/decode2.txt)"
echo "--- sample ---"
grep -E 'HCI Command|HCI Event' /tmp/decode2.txt | head -8
