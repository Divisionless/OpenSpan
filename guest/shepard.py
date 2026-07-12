import wave, math, struct
sr = 44100
dur = 12.0                      # loop length (seconds)
n = int(sr * dur)
n_oct = 7
base = 55.0                     # A1
center = n_oct / 2.0
width = n_oct / 4.0
w = wave.open('/tmp/shepard.wav', 'wb')
w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
frames = bytearray()
for i in range(n):
    t = i / sr
    p = (t / dur)               # 0..1 shift over the loop
    s = 0.0
    for k in range(n_oct):
        pos = (k + p) % n_oct
        freq = base * (2 ** pos)
        env = math.exp(-0.5 * ((pos - center) / width) ** 2)
        s += env * math.sin(2 * math.pi * freq * t)
    v = int(5000 * s / 2.2)
    v = max(-28000, min(28000, v))
    frames += struct.pack('<hh', v, v)
w.writeframes(bytes(frames))
w.close()
print("shepard tone written")
