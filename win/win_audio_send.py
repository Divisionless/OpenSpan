#!/usr/bin/env python3
"""OpenSpan Windows audio sender.

Captures the current default output device via WASAPI loopback and streams
raw PCM (s16le) over UDP to the VM, which plays it to the Bluetooth earbuds.
Because it's loopback, you also hear the audio on your PC speakers -- perfect
for feeling the exact Bluetooth latency (speakers = reference, earbuds = delayed).
"""
import math
import os
import socket
import sys
import threading
import time

import numpy as np
import pyaudiowpatch as pa

DEST = ("127.0.0.1", 4010)
CHUNK = 1400  # UDP-safe payload


def _single_sender_lock():
    """Windows named mutex so only ONE sender ever runs. Two senders both
    streaming to the VM's UDP :4010 interleave into the single player and
    GARBLE the audio -- this makes a duplicate exit before it captures."""
    try:
        import ctypes
        ctypes.windll.kernel32.CreateMutexW(None, False, "OpenSpanAudioSender")
        return ctypes.windll.kernel32.GetLastError() != 183  # 183=ALREADY_EXISTS
    except Exception:
        return True  # never block if the mutex mechanism is unavailable


if not _single_sender_lock():
    sys.exit(0)  # another sender already owns the stream

# Master gain 0.0-1.0, mirrored from the Windows master volume of the default
# output device so your normal Windows volume slider/keys control the earbuds.
# Read in the audio callback, updated by _volume_watcher. 1.0 = full (no change).
GAIN = 1.0

# Per-channel (L, R) gains from the app's Balance slider. The compact UI
# writes a single float -1..+1 to audio_balance.txt next to the app; missing
# file or 0 = perfectly centered (fast path untouched).
if getattr(sys, "frozen", False):  # OpenSpan.exe --audio: data at the exe
    ROOT = os.path.dirname(os.path.abspath(sys.executable))
else:
    ROOT = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".."))
BAL_FILE = os.path.join(ROOT, "audio_balance.txt")
BAL = (1.0, 1.0)


def _read_balance():
    """audio_balance.txt (-1 = full left ... +1 = full right) -> (L, R)
    channel gains. Anything unreadable OR non-finite means centered —
    'nan'/'inf' PARSE fine but would silently hard-pan via the clamp."""
    try:
        with open(BAL_FILE) as f:
            b = float(f.read().strip())
    except (OSError, ValueError):
        return (1.0, 1.0)
    if not math.isfinite(b):
        return (1.0, 1.0)
    b = max(-1.0, min(1.0, b))
    return (min(1.0, 1.0 - b), min(1.0, 1.0 + b))


def _volume_watcher():
    """Mirror the Windows master volume into GAIN (so the normal Windows
    volume slider/keys control the earbuds) and the app's balance file into
    BAL. Fails safe on every path: no pycaw -> GAIN stays 1.0; no balance
    file -> centered; audio itself is never affected by errors here."""
    global GAIN, BAL
    has_vol = False
    try:
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL, CoCreateInstance, GUID
        from pycaw.pycaw import IAudioEndpointVolume, IMMDeviceEnumerator
        clsid = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
        has_vol = True
    except Exception:
        pass  # pycaw missing -> volume fixed at 1.0; balance still works
    vol = None
    while True:
        BAL = _read_balance()
        if has_vol:
            try:
                if vol is None:
                    enum = CoCreateInstance(clsid, IMMDeviceEnumerator,
                                            CLSCTX_ALL)
                    endpoint = enum.GetDefaultAudioEndpoint(0, 1)
                    iface = endpoint.Activate(
                        IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                    vol = cast(iface, POINTER(IAudioEndpointVolume))
                GAIN = 0.0 if vol.GetMute() else float(
                    vol.GetMasterVolumeLevelScalar())
            except Exception:
                vol = None   # re-acquire next tick (e.g. device changed)
                GAIN = 1.0
        time.sleep(0.15)


sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

p = pa.PyAudio()
wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
default_out = p.get_device_info_by_index(wasapi["defaultOutputDevice"])

lb = None
for d in p.get_loopback_device_info_generator():
    if default_out["name"] in d["name"]:
        lb = d
        break
if lb is None:
    for d in p.get_loopback_device_info_generator():
        lb = d
        break
if lb is None:
    print("No WASAPI loopback device found.", file=sys.stderr)
    sys.exit(1)

RATE = int(lb["defaultSampleRate"])
CH = int(lb["maxInputChannels"])
print(f"capturing: {lb['name']}  {RATE}Hz {CH}ch  ->  udp {DEST[0]}:{DEST[1]}")

# The guest end (pw-play) is PINNED to 48000Hz/2ch -- any other format here
# plays pitch-shifted/garbled (gotcha: sample-rate mismatch). VB-Cable's
# default is 48000/2 so normally this is a no-op; if the default device ever
# reports something else, convert instead of silently streaming garble.
NEEDS_CONVERT = not (RATE == 48000 and CH == 2)
if NEEDS_CONVERT:
    print(f"WARNING: device is {RATE}Hz/{CH}ch but the guest needs 48000Hz/"
          "2ch -- converting on the fly.")
    _rs_state = {"prev": np.zeros((1, 2), dtype=np.float32), "pos": 0.0}


def _to_48k_stereo(f):
    """float32 interleaved (CH ch @ RATE Hz) -> stereo 48kHz interleaved.
    Linear resample; the previous block's last sample and the fractional
    read position carry across calls so block boundaries stay click-free."""
    x = f.reshape(-1, CH)
    if CH == 1:
        x = np.repeat(x, 2, axis=1)
    elif CH > 2:
        x = x[:, :2]
    if RATE == 48000:
        return np.ascontiguousarray(x).ravel()
    buf = np.vstack([_rs_state["prev"], x])
    step = RATE / 48000.0
    p = _rs_state["pos"]
    last = buf.shape[0] - 1
    n_out = int((last - p) // step) + 1 if p <= last else 0
    if n_out <= 0:
        _rs_state["prev"] = buf[-1:]
        _rs_state["pos"] = p - last
        return np.empty(0, dtype=np.float32)
    idx = p + step * np.arange(n_out)
    i0 = idx.astype(np.int64)
    frac = (idx - i0).astype(np.float32)[:, None]
    i1 = np.minimum(i0 + 1, last)
    y = buf[i0] * (1.0 - frac) + buf[i1] * frac
    _rs_state["pos"] = (p + step * n_out) - last
    _rs_state["prev"] = buf[-1:]
    return y.astype(np.float32).ravel()


def cb(in_data, frame_count, time_info, status):
    f = np.frombuffer(in_data, dtype=np.float32)
    if NEEDS_CONVERT:
        f = _to_48k_stereo(f)
        if f.size == 0:
            return (None, pa.paContinue)
    g = GAIN
    if g != 1.0:
        f = f * g  # apply the Windows master volume
    lg, rg = BAL
    if lg != 1.0 or rg != 1.0:
        # left/right balance from the app (frombuffer arrays are read-only,
        # so multiply into a new array rather than in place)
        f = (f.reshape(-1, 2) *
             np.array((lg, rg), dtype=np.float32)).ravel()
    i16 = (np.clip(f, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    for off in range(0, len(i16), CHUNK):
        sock.sendto(i16[off:off + CHUNK], DEST)
    return (None, pa.paContinue)


threading.Thread(target=_volume_watcher, daemon=True).start()

stream = p.open(format=pa.paFloat32, channels=CH, rate=RATE,
                input=True, input_device_index=lb["index"],
                frames_per_buffer=128, stream_callback=cb)
stream.start_stream()
print("streaming Windows audio to the VM (volume follows the Windows slider). "
      "Play something! (Ctrl+C to stop)")
try:
    while stream.is_active():
        time.sleep(0.1)
except KeyboardInterrupt:
    pass
finally:
    stream.stop_stream()
    stream.close()
    p.terminate()
