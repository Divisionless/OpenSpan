#!/bin/bash
# kill the shepard keepalive loop without the pattern appearing in the
# invoking command line (avoids pkill matching its own ssh shell)
pkill -9 -f 'while true' 2>/dev/null
pkill -9 -x pw-play 2>/dev/null
pkill -9 -f 'DEFAULT_AUDIO_SINK' 2>/dev/null
sleep 1
echo "audio loops cleared"
