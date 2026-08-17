#!/bin/bash
# Boot helper: the USB radio can take ~8-60s to enumerate, often after
# bluetoothd started -- so bluez can end up with no controller. Wait for the
# radio, make sure bluez sees it, power it on, force low LE connection-interval
# bounds (smooth iPad mouse), then bounce the audio stack so A2DP endpoints
# register against the live controller.
#
# NOTE: the retired stack (openspan-audio/hold/jbl) is intentionally NOT touched.
#
# THE LAW (DEVLOG ~Jul 6). BLE HID and A2DP sharing one radio is proven, and it
# is a supported configuration -- the breakage was never the protocol, it was
# OPERATIONAL COUPLING: "restarting audio must never touch the keyboard, and
# vice versa." Every restart below is scoped, ordered and conditioned for that
# reason. Do not make one of them unconditional again: on 2026-08-17 the
# unconditional 'systemctl restart openspan-wireplumber' at the bottom of this
# file fired 2.1s AFTER the headphones' A2DP stream had landed, unregistered the
# live endpoints, forced an AVDTP renegotiation, desynced the release handshake
# and left the transport dead -- with the app still showing "connected ✓".
source /opt/openspan/env.sh

# Completion marker: the Windows app treats the bridge as READY (and then
# auto-reconnects the earbuds) as soon as the guest answers ssh, which used to
# be true LONG before this script's last restart. The app now waits for this
# file, so nothing invites a device onto the radio mid-bounce. Written on EVERY
# exit path via the trap -- a script that dies early must release the app, not
# hang it (the app also has its own bounded timeout as a second backstop).
BTREADY_MARK=/run/openspan/btready.done
mkdir -p /run/openspan 2>/dev/null
rm -f "$BTREADY_MARK" 2>/dev/null
trap 'date -u +%Y-%m-%dT%H:%M:%SZ > "$BTREADY_MARK" 2>/dev/null' EXIT

# Is any A2DP audio actually live on ANY controller right now? If so the audio
# stack is serving a real stream and must not be bounced underneath it.
a2dp_transport_live() {
  if command -v busctl >/dev/null 2>&1; then
    if busctl --system call org.bluez / \
         org.freedesktop.DBus.ObjectManager GetManagedObjects 2>/dev/null \
         | grep -q 'org.bluez.MediaTransport1'; then
      return 0
    fi
  fi
  # fallback for images without busctl (and for bluez too old for the
  # 'devices Connected' filter): walk every known device, ask each one.
  local mac info
  for mac in $(bluetoothctl devices 2>/dev/null | awk '{print $2}'); do
    info=$(bluetoothctl info "$mac" 2>/dev/null) || continue
    if echo "$info" | grep -q 'Connected: yes' \
       && echo "$info" | grep -qi 'Icon: audio'; then
      return 0
    fi
  done
  return 1
}

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
#  Conditioned, not removed: if an A2DP stream is ALREADY live then bluetoothd
#  plainly has its controller and address resolution is moot -- bouncing it
#  there would be the bulldozer the Jul-6 law forbids.
if a2dp_transport_live; then
  echo "btready: A2DP transport already live -- skipping 'restart bluetooth'" >&2
else
  systemctl restart bluetooth
  sleep 6
fi
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
#
# CONDITIONAL. Restarting wireplumber unregisters the media endpoints, which
# tears down any stream currently running on them. At boot there is normally
# nothing to tear down and the re-registration is exactly what we want -- but if
# the earbuds beat us to it, the endpoints are already live and correct and the
# restart is pure damage. Defense in depth behind the READY gate above: even if
# the ordering ever slips again, this self-heals rather than bulldozes.
if a2dp_transport_live; then
  echo "btready: A2DP transport live -- skipping 'restart openspan-wireplumber'" \
       "(endpoints are already registered and in use; bouncing them would tear" \
       "down the stream -- see the Jul-6 law at the top of this file)" >&2
else
  systemctl restart openspan-wireplumber
  sleep 4
fi
# udprecv is the Windows->guest audio sender's sink. It carries no Bluetooth
# state and touches no device, so it stays unconditional.
systemctl restart openspan-udprecv
