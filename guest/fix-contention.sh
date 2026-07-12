#!/bin/bash
# Kill the competing STOCK user-level pipewire/wireplumber stack that fights
# our dedicated /run/openspan SYSTEM stack for the bluez A2DP transport.
# Every SSH login spawned a `systemd --user` that started the stock stack,
# which grabbed then dropped the onn's A2DP link on a ~60s metronome.

echo '=== our system stack MainPIDs (these must survive) ==='
keep=""
for u in openspan-pipewire openspan-wireplumber openspan-pipewire-pulse; do
  p=$(systemctl show "$u" -p MainPID --value 2>/dev/null)
  keep="$keep $p"
  echo "  $u = $p"
done

echo '=== mask stock user units globally (no login can start them again) ==='
systemctl --global mask pipewire.service pipewire.socket \
  pipewire-pulse.service pipewire-pulse.socket wireplumber.service 2>&1 | sed 's/^/  /'

echo '=== stop root user-manager audio + kill stray stock procs ==='
for name in wireplumber pipewire-pulse pipewire; do
  for p in $(pgrep -x "$name" 2>/dev/null); do
    case " $keep " in
      *" $p "*) echo "  keep  $p ($name) [ours]";;
      *) echo "  KILL  $p ($name) [stock]"; kill "$p" 2>/dev/null;;
    esac
  done
done
sleep 2
# second pass for anything that respawned
for name in wireplumber pipewire-pulse pipewire; do
  for p in $(pgrep -x "$name" 2>/dev/null); do
    case " $keep " in *" $p "*) :;; *) kill -9 "$p" 2>/dev/null;; esac
  done
done

echo '=== after: only OUR stack should remain ==='
ps -eo pid,args 2>/dev/null | grep -iE 'pipewire|wireplumber' | grep -v grep
echo '=== who owns the bluez A2DP endpoints now (should be ONE stable sender)? ==='
journalctl -u bluetooth --no-pager --since '20 sec ago' 2>/dev/null | grep -c 'unregistered'
echo "  (^ unregister events in last 20s; want 0)"
