#!/bin/bash
# Boot helper: the USB radio can take ~8-60s to enumerate, often after
# bluetoothd started -- so bluez can end up with no controller. Wait for the
# radio, make sure bluez sees it, power it on, force low LE connection-interval
# bounds (smooth iPad mouse), then bounce the audio stack so A2DP endpoints
# register against the live controller.
#
# NOTE: the retired stack (openspan-audio/hold/jbl) is intentionally NOT touched.
source /opt/openspan/env.sh

# Single-radio installs have no override and remain hci0. Multi-radio installs
# persist the stable controller MAC in the openspanble drop-in; resolve it to
# the current hciN before waiting, powering, or applying mouse latency bounds.
HCI="${OPENSPAN_ADAPTER:-}"
RADIO_CONF=/etc/systemd/system/openspanble.service.d/20-radio.conf
if [ -z "$HCI" ] && [ -f "$RADIO_CONF" ]; then
  CTRL=$(sed -n 's/^Environment=OPENSPAN_CONTROLLER=//p' "$RADIO_CONF" | tail -n 1)
  if [ -n "$CTRL" ]; then
    HCI=$(python3 /opt/openspan/openspan_bt.py resolve --controller "$CTRL" 2>/dev/null) || true
  fi
  # fall back to legacy OPENSPAN_ADAPTER= for pre-migration drop-ins
  if [ -z "$HCI" ]; then
    HCI=$(sed -n 's/^Environment=OPENSPAN_ADAPTER=//p' "$RADIO_CONF" | tail -n 1)
  fi
fi
HCI="${HCI:-hci0}"
case "$HCI" in hci[0-9]*) ;; *) echo "invalid adapter $HCI" >&2; exit 1;; esac
IDX="${HCI#hci}"
export OPENSPAN_ADAPTER="$HCI"

/opt/openspan/wait-hci0.sh
sleep 3
# Restart bluetoothd now that the radio is up. TWO boot-races to defeat:
#  1) bluez may have started before the USB radio enumerated -> NO controller
#     registered ('Index list with 0');
#  2) EVEN WITH a controller, bluez arms LE address resolution at startup
#     against a not-yet-ready radio and gets 'Failed to set privacy: Rejected
#     (0x0b)'. Resolution then stays OFF the whole session, so a bonded iPad's
#     resolvable-private-address is never matched to its stored bond on
#     reconnect -> encryption can't start -> the link drops ('security
#     requested but not available'). Fresh pairing dodges it; reconnect dies on
#     it. Restarting against the now-ready radio arms resolution and makes
#     bonded reconnect actually stick. (Known BlueZ bug; the documented fix is
#     exactly this restart-after-settle.)
systemctl restart bluetooth
sleep 6
# belt-and-suspenders: if the controller STILL isn't registered, keep trying
for try in 1 2 3; do
  if ! btmgmt --index "$IDX" info >/dev/null 2>&1; then
    systemctl restart bluetooth
    sleep 6
  else
    break
  fi
done
btmgmt --index "$IDX" power on >/dev/null 2>&1

# LE connection-interval bounds: 15-30ms -- snappy iPad mouse but NOT so
# aggressive it starves A2DP (7.5ms serviced the iPad ~133x/s and garbled the
# audio; the 30-50ms kernel default makes the pointer laggy/jumpy). This is
# belt-and-suspenders with main.conf Min/MaxConnectionInterval in case a
# boot-order race resets the kernel defaults. Units are raw 1.25ms; 12=15ms,
# 24=30ms. Write min first so min<=max always holds.
echo 12  > "/sys/kernel/debug/bluetooth/$HCI/conn_min_interval" 2>/dev/null
echo 24 > "/sys/kernel/debug/bluetooth/$HCI/conn_max_interval" 2>/dev/null

# Radio confirmed present: (re)register A2DP endpoints, then restart the bridge.
systemctl restart openspan-wireplumber
sleep 4
systemctl restart openspan-udprecv
