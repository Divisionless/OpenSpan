#!/bin/bash
# Boot-time entry point for an OpenSpan BLE HID lane.
#
# The drop-in names a stable controller MAC in OPENSPAN_CONTROLLER. This
# script resolves that MAC to the current hciN, prepares the radio, and
# execs the BLE daemon -- all in one process tree so the resolved adapter
# propagates through normal environment inheritance.
#
# If OPENSPAN_CONTROLLER is unset the script falls back to OPENSPAN_ADAPTER
# (already an hciN) or the single-radio default hci0. Existing single-radio
# installs and manual invocations keep working with no change.
set -eu

if [ -n "${OPENSPAN_CONTROLLER:-}" ]; then
    HCI=$(python3 /opt/openspan/openspan_bt.py resolve \
          --controller "$OPENSPAN_CONTROLLER" 2>&1) || {
        echo "start-ble-lane: controller $OPENSPAN_CONTROLLER not found" >&2
        exit 1
    }
    case "$HCI" in
        hci[0-9]*) ;;
        *) echo "start-ble-lane: resolve returned invalid adapter: $HCI" >&2
           exit 1 ;;
    esac
    export OPENSPAN_ADAPTER="$HCI"
fi

/opt/openspan/wait-hci0.sh
/opt/openspan/ensure-dualmode.sh
exec /usr/bin/python3 /opt/openspan/openspan_ble.py
