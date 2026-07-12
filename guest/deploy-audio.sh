#!/bin/bash
# stop + disable the old hacky one-script audio service
systemctl stop openspan-audio 2>/dev/null
systemctl disable openspan-audio 2>/dev/null
pkill -x wireplumber 2>/dev/null; pkill -x pipewire-pulse 2>/dev/null; pkill -x pipewire 2>/dev/null
sleep 2
systemctl daemon-reload
systemctl enable openspan-pipewire openspan-wireplumber openspan-pipewire-pulse 2>&1 | tail -1
# start in order: pipewire first, then the two that wait for its socket
systemctl start openspan-pipewire
sleep 3
systemctl start openspan-pipewire-pulse openspan-wireplumber
sleep 9
echo '=== services active? ==='
systemctl is-active openspan-pipewire openspan-wireplumber openspan-pipewire-pulse
echo '=== pipewire socket present? ==='
ls /run/openspan/pipewire-0 2>/dev/null && echo yes || echo NO
echo '=== wpctl works? ==='
export XDG_RUNTIME_DIR=/run/openspan
wpctl status >/dev/null 2>&1 && echo OK || echo FAIL
echo '=== *** A2DP endpoints WirePlumber registered with bluez (want > 0) *** ==='
journalctl -u bluetooth --no-pager --since '25 sec ago' 2>/dev/null | grep -c 'Endpoint registered'
echo '=== HFP NotPermitted flood (want 0) ==='
journalctl -u openspan-wireplumber --no-pager --since '25 sec ago' 2>/dev/null | grep -c 'NotPermitted'
