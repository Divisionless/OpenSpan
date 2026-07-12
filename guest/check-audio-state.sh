#!/bin/bash
source /opt/openspan/env.sh
echo '=== which onn/jbl connected? (5th field=1) ==='
bash /opt/openspan/bt-list.sh 2>/dev/null | grep -iE 'onn|jbl'
echo '=== SINKS (want a bluez_output / onn) ==='
wpctl status 2>&1 | sed -n '/Sinks:/,/Sink endpoints/p'
echo '=== bluez A2DP transport negotiated? (sep/fd) ==='
busctl --system tree org.bluez 2>/dev/null | grep -iE '/sep|/fd' | head -3 || echo 'NO A2DP transport'
echo '=== wireplumber recent errors ==='
journalctl -u openspan-audio --no-pager --since '90 sec ago' 2>/dev/null | grep -iE 'NotPermitted|error|profile' | tail -4
echo '=== onn in wpctl? ==='
wpctl status 2>&1 | grep -iE 'onn' || echo 'no onn node anywhere'
