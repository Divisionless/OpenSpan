#!/bin/bash
# Assign one independent BLE HID target to a stable controller MAC.
#
# The original iPad/single-radio service remains openspanble.service.  The Mac
# lane is opt-in and is a second daemon on port 9956 with its own radio,
# advertisement, GATT state, bonds, and input socket.
set -eu

TARGET="${1:-}"
CTRL="${2:-}"
case "$TARGET" in
  ipad)
    exec /opt/openspan/set-hid-radio.sh "$CTRL"
    ;;
  mac)
    ;;
  *)
    echo "usage: set-hid-target.sh ipad|mac CONTROLLER_MAC" >&2
    exit 2
    ;;
esac

[ -n "$CTRL" ] || { echo "controller address required" >&2; exit 2; }
HCI=$(python3 /opt/openspan/openspan_bt.py resolve --controller "$CTRL")
case "$HCI" in hci[0-9]*) ;; *) echo "invalid resolved adapter: $HCI" >&2; exit 2;; esac
IPAD_CONF=/etc/systemd/system/openspanble.service.d/20-radio.conf
if [ -f "$IPAD_CONF" ] && \
   grep -qx "Environment=OPENSPAN_ADAPTER=$HCI" "$IPAD_CONF"; then
  echo "$HCI is already assigned to the iPad HID lane" >&2
  exit 3
fi

DIR=/etc/systemd/system/openspanble-mac.service.d
CONF="$DIR/20-radio.conf"
mkdir -p "$DIR"
NEW="[Service]
Environment=OPENSPAN_ADAPTER=$HCI
"

changed=0
if [ ! -f "$CONF" ] || ! printf '%s' "$NEW" | cmp -s - "$CONF"; then
  printf '%s' "$NEW" > "$CONF.new"
  mv -f "$CONF.new" "$CONF"
  changed=1
fi

BASE="/sys/kernel/debug/bluetooth/$HCI"
[ ! -w "$BASE/conn_min_interval" ] || echo 12 > "$BASE/conn_min_interval"
[ ! -w "$BASE/conn_max_interval" ] || echo 24 > "$BASE/conn_max_interval"

systemctl daemon-reload
systemctl enable openspanble-mac.service >/dev/null 2>&1
if [ "$changed" -eq 1 ] || ! systemctl is-active --quiet openspanble-mac; then
  systemctl restart openspanble-mac
  echo "CHANGED|$HCI"
else
  echo "UNCHANGED|$HCI"
fi
