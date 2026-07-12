#!/bin/bash
source /opt/openspan/env.sh
echo '=== pipewire-0 socket in /run/openspan? ==='
ls -la /run/openspan/ 2>&1 | grep -iE 'pipewire|pulse|bus' || echo 'EMPTY'
echo '=== wpctl connects? ==='
wpctl status >/dev/null 2>&1 && echo 'PW-OK' || echo 'PW-FAIL'
echo '=== sinks ==='
wpctl status 2>&1 | sed -n '/Sinks:/,/Sink endpoints/p'
echo '=== receiver listening 4010? ==='
ss -ulnp 2>/dev/null | grep -q 4010 && echo 'RECV-OK' || echo 'RECV-NO'
