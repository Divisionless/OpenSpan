#!/bin/bash
# Select the controller used by the single OpenSpan HID lane. The public/default
# path never calls this and remains hci0. Multi-radio mode stores a stable
# controller MAC and resolves its current hciN name on every apply.
set -eu
CTRL="${1:-}"
[ -n "$CTRL" ] || { echo "controller address required" >&2; exit 2; }

DIR=/etc/systemd/system/openspanble.service.d
CONF="$DIR/20-radio.conf"
mkdir -p "$DIR"

apply_latency() {
  local hci="$1"
  local base="/sys/kernel/debug/bluetooth/$hci"
  [ ! -w "$base/conn_min_interval" ] || \
    echo 12 > "$base/conn_min_interval"
  [ ! -w "$base/conn_max_interval" ] || \
    echo 24 > "$base/conn_max_interval"
}

if [ "$CTRL" = "--default" ]; then
  apply_latency hci0
  if [ ! -f "$CONF" ]; then
    systemctl is-active --quiet openspanble || systemctl restart openspanble
    echo "UNCHANGED|hci0"
    exit 0
  fi
  rm -f "$CONF"
  systemctl daemon-reload
  systemctl restart openspanble
  echo "CHANGED|hci0"
  exit 0
fi

HCI=$(python3 /opt/openspan/openspan_bt.py resolve --controller "$CTRL")
case "$HCI" in hci[0-9]*) ;; *) echo "invalid resolved adapter: $HCI" >&2; exit 2;; esac
MAC_CONF=/etc/systemd/system/openspanble-mac.service.d/20-radio.conf
if [ -f "$MAC_CONF" ] && \
   grep -qx "Environment=OPENSPAN_ADAPTER=$HCI" "$MAC_CONF"; then
  echo "$HCI is already assigned to the managed Mac HID lane" >&2
  exit 3
fi
apply_latency "$HCI"

NEW="[Service]
Environment=OPENSPAN_ADAPTER=$HCI
"
if [ -f "$CONF" ] && printf '%s' "$NEW" | cmp -s - "$CONF"; then
  systemctl is-active --quiet openspanble || systemctl restart openspanble
  echo "UNCHANGED|$HCI"
  exit 0
fi
printf '%s' "$NEW" > "$CONF.new"
mv -f "$CONF.new" "$CONF"
systemctl daemon-reload
systemctl restart openspanble
echo "CHANGED|$HCI"
