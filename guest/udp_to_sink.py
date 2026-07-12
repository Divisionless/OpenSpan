#!/usr/bin/env python3
"""OpenSpan Windows->Bluetooth audio bridge (dead simple).

Receives Windows audio (raw PCM s16le 48k stereo) over UDP :4010 and plays it
to the DEFAULT PipeWire sink. When the Bluetooth headphones are connected,
WirePlumber makes them the default sink, so audio goes to them automatically.

Deliberately NO device targeting / no per-tick pw-dump / no dont-reconnect --
that aggressive grabbing of the bluez sink is what destabilized the A2DP
transport. A single plain pw-play to the default sink does not touch the
transport lifecycle, so the connection stays rock solid.

Buffer sizing matters: the BLE keyboard/mouse share the one radio with the
A2DP stream, so the A2DP transmit gets starved of airtime in bursts. The
pw-play buffer (250ms) gives bluez enough lead time to ride through those
bursts instead of underrunning into garble.

LATENCY (2026-07-10): two mechanisms silently added delay on top of the 250ms.
(1) The stdin pipe to pw-play is a 64KB Linux pipe = a hidden 341ms of audio;
    it is now shrunk to 16KB (~85ms) via F_SETPIPE_SZ, and the Popen is
    unbuffered so Python adds no batching of its own.
(2) The old gap-filler injected silence on EVERY 20ms socket timeout. When
    packets were merely LATE (BLE airtime burst, VM scheduling) the late data
    still arrived after the silence -- net queued bytes grew, playback consumes
    at exactly real-time, and nothing ever reclaimed the excess: latency
    ratcheted up all session. Silence now starts only after MISS_LIMIT
    consecutive timeouts (100ms of genuinely missing data), so ordinary jitter
    never mints excess bytes, and every real pause drains up to 100ms of any
    accumulated excess.

The silence feed itself must NEVER be removed: the WirePlumber suspend-timeout
config does not exist in this VM -- the continuous feed is what keeps the A2DP
transport from idle-suspending (the old "drops ~5s into silence" bug). 100ms
of debounce is far below any idle threshold, so that protection is intact.

To trade ride-through headroom for lower latency, lower LATENCY_MS (e.g. 150)
-- test with heavy mouse + audio before trusting it.
"""
import fcntl
import os
import socket
import subprocess

env = dict(os.environ)
env.setdefault("XDG_RUNTIME_DIR", "/run/openspan")

LATENCY_MS = 250   # pw-play buffer: the anti-garble cushion (see header)
PIPE_BYTES = 16384  # stdin pipe cap: 16KB = ~85ms @ 48k stereo s16
F_SETPIPE_SZ = 1031  # fcntl op (linux); no named constant in this Python

# One socket-timeout's worth of silence @ 48k stereo s16 = 48000*2ch*2bytes*GAP
GAP = 0.02
SILENCE = b"\x00" * int(48000 * 2 * 2 * GAP)  # 20ms -> matches the timeout below
MISS_LIMIT = 5  # inject silence only after 5*GAP = 100ms of true no-data

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
sock.bind(("0.0.0.0", 4010))
sock.settimeout(GAP)

while True:
    player = subprocess.Popen(
        ["pw-play", "--format=s16", "--rate=48000", "--channels=2",
         f"--latency={LATENCY_MS}ms", "-"],
        stdin=subprocess.PIPE, bufsize=0, env=env)
    try:
        fcntl.fcntl(player.stdin.fileno(), F_SETPIPE_SZ, PIPE_BYTES)
    except OSError:
        pass  # kernel refuses -> default 64KB pipe; higher latency, still works
    misses = 0
    try:
        while player.poll() is None:
            try:
                data, _ = sock.recvfrom(8192)
                misses = 0
            except socket.timeout:
                misses += 1
                if misses < MISS_LIMIT:
                    continue  # brief jitter: let the 250ms cushion ride it
                data = SILENCE  # real pause: keep the A2DP stream alive
            player.stdin.write(data)
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            player.terminate()
        except Exception:
            pass
