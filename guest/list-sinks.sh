#!/bin/bash
source /opt/openspan/env.sh
pw-dump 2>/dev/null | python3 -c "
import json, sys
data = json.load(sys.stdin)
for o in data:
    info = o.get('info') or {}
    p = info.get('props') or {}
    if p.get('media.class') == 'Audio/Sink':
        print(repr(p.get('node.name')), '|', p.get('node.description'))
"
