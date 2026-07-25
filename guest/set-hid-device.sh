#!/bin/bash
# Assign one independent BLE HID lane to a device instance.
#
# Every device the Windows side knows about gets its own openspanble@<id>
# daemon: its own radio, advertisement, GATT state, bonds and input socket.
# Nothing here knows what the device IS -- id, port and advertised name are all
# supplied by the caller, so N devices work with no code change. This replaces
# the hardcoded ipad/mac lanes of set-hid-target.sh; those legacy units stay
# installed so already-bonded lanes keep working during migration.
#
#   set-hid-device.sh DEVICE_ID CONTROLLER_MAC PORT NAME   e.g.
#     set-hid-device.sh dev3 AA:BB:CC:00:00:03 9957 "OpenSpan Studio"
#   set-hid-device.sh --remove DEVICE_ID
#
# Prints CHANGED|<hci> or UNCHANGED|<hci> (REMOVED|<id> for --remove) as its
# last line; the Windows side parses that.
set -eu

SD=/etc/systemd/system

usage() {
  echo "usage: set-hid-device.sh DEVICE_ID CONTROLLER_MAC PORT NAME" >&2
  echo "       set-hid-device.sh --remove DEVICE_ID" >&2
  exit 2
}

# The id goes straight into a unit name; restrict it to characters systemd
# needs no escaping for rather than round-tripping through systemd-escape.
valid_id() {
  case "$1" in
    ""|*[!A-Za-z0-9_-]*) return 1 ;;
  esac
  return 0
}

dropin_dir() { echo "$SD/openspanble@$1.service.d"; }

# --- teardown: removing a device in the UI must remove its lane -------------
if [ "${1:-}" = "--remove" ]; then
  ID="${2:-}"
  valid_id "$ID" || usage
  # disable --now stops it too; ignore "not loaded" on a lane never assigned.
  systemctl disable --now "openspanble@$ID.service" >/dev/null 2>&1 || true
  rm -rf "$(dropin_dir "$ID")"
  systemctl daemon-reload
  echo "REMOVED|$ID"
  exit 0
fi

# --- argument validation ----------------------------------------------------
ID="${1:-}"
CTRL="${2:-}"
PORT="${3:-}"
NAME="${4:-}"

valid_id "$ID" || usage
[ -n "$CTRL" ] || usage
case "$PORT" in ""|*[!0-9]*) usage ;; esac
[ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || usage
[ -n "$NAME" ] || usage
# the name is written into a systemd Environment= line and advertised over GATT
case "$NAME" in
  *\"*|*\\*) echo "device name cannot contain quotes or backslashes" >&2; exit 2 ;;
esac
[ "$(printf '%s' "$NAME" | wc -l)" -eq 0 ] || {
  echo "device name must be a single line" >&2; exit 2; }
[ "$(printf '%s' "$NAME" | wc -c)" -le 24 ] || {
  echo "device name must be at most 24 UTF-8 bytes" >&2; exit 2; }

# --- resolve the stable controller MAC to its CURRENT hciN ------------------
HCI=$(python3 /opt/openspan/openspan_bt.py resolve --controller "$CTRL")
case "$HCI" in hci[0-9]*) ;; *) echo "invalid resolved adapter: $HCI" >&2; exit 2;; esac

CONF_DIR="$(dropin_dir "$ID")"
CONF="$CONF_DIR/20-device.conf"

# One radio, one lane -- two daemons on one adapter fight over advertisement
# and bonds. Refuse a controller any OTHER instance (or a legacy ipad/mac unit)
# already owns.
for other in "$SD"/openspanble@*.service.d/20-device.conf \
             "$SD"/openspanble.service.d/20-radio.conf \
             "$SD"/openspanble-mac.service.d/20-radio.conf; do
  [ -f "$other" ] || continue
  [ "$other" != "$CONF" ] || continue
  if grep -qx "Environment=OPENSPAN_ADAPTER=$HCI" "$other"; then
    echo "$HCI is already assigned to another HID lane ($other)" >&2
    exit 3
  fi
done

# --- write the per-instance drop-in (idempotent) ----------------------------
mkdir -p "$CONF_DIR"
NEW="[Service]
Environment=OPENSPAN_ADAPTER=$HCI
Environment=OPENSPAN_PORT=$PORT
Environment=\"OPENSPAN_DEVICE_NAME=$NAME\"
"

changed=0
if [ ! -f "$CONF" ] || ! printf '%s' "$NEW" | cmp -s - "$CONF"; then
  printf '%s' "$NEW" > "$CONF.new"
  chmod 644 "$CONF.new"
  mv -f "$CONF.new" "$CONF"
  changed=1
fi

# LE connection interval 12/24 (15-30ms) on THIS lane's radio: the same latency
# tuning the single-lane script applies, per adapter.
BASE="/sys/kernel/debug/bluetooth/$HCI"
[ ! -w "$BASE/conn_min_interval" ] || echo 12 > "$BASE/conn_min_interval"
[ ! -w "$BASE/conn_max_interval" ] || echo 24 > "$BASE/conn_max_interval"

systemctl daemon-reload
systemctl enable "openspanble@$ID.service" >/dev/null 2>&1 || true
# Restart only on a real change (or a dead lane): a needless restart drops an
# established HOGP link and forces the device to reconnect.
if [ "$changed" -eq 1 ] || ! systemctl is-active --quiet "openspanble@$ID.service"; then
  systemctl restart "openspanble@$ID.service"
  echo "CHANGED|$HCI"
else
  echo "UNCHANGED|$HCI"
fi
