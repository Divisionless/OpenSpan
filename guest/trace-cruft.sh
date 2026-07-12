#!/bin/bash
echo '=== is-enabled (real state) ==='
for s in openspan-jbl openspan-hold openspan-audio; do printf '%-16s ' "$s"; systemctl is-enabled "$s" 2>&1; done

echo '=== who WANTS/REQUIRES them (reverse deps) ==='
for s in openspan-jbl openspan-hold openspan-audio; do
  echo "-- $s --"
  systemctl list-dependencies --reverse "$s" 2>/dev/null | sed '1d' | grep -v '^$'
done

echo '=== .wants / .requires symlinks referencing them ==='
grep -rl 'openspan-jbl\|openspan-hold\|openspan-audio' /etc/systemd/system/*.wants/ /etc/systemd/system/*.requires/ 2>/dev/null
ls -la /etc/systemd/system/*.wants/ 2>/dev/null | grep -i openspan

echo '=== does any OTHER unit list them in Wants=/Requires=/After= ? ==='
grep -rHn 'openspan-jbl\|openspan-hold\|openspan-audio' /etc/systemd/system/ 2>/dev/null | grep -v '/openspan-jbl.service\|/openspan-hold.service\|/openspan-audio.service'

echo '=== how were they started? (activation trace) ==='
for s in openspan-jbl openspan-hold openspan-audio; do
  printf '%-16s ' "$s"
  systemctl show "$s" -p ActiveEnterTimestamp -p TriggeredBy -p WantedBy -p RequiredBy 2>/dev/null | tr '\n' ' '; echo
done

echo '=== rc.local / @reboot cron / boot scripts starting them ==='
grep -rHn 'openspan' /etc/rc.local /etc/crontab /var/spool/cron/crontabs/* /etc/cron.d/* 2>/dev/null | grep -i 'jbl\|hold\|audio\|systemctl start'
crontab -l 2>/dev/null | grep -i openspan
