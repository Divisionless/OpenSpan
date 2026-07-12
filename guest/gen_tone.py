import wave, math, struct
w = wave.open('/tmp/tone.wav', 'wb')
w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100)
frames = bytearray()
for i in range(44100 * 3):
    t = i / 44100.0
    freq = 440.0 if t < 1 else (660.0 if t < 2 else 880.0)
    v = int(7000 * math.sin(2 * math.pi * freq * i / 44100.0))
    frames += struct.pack('<hh', v, v)
w.writeframes(bytes(frames))
w.close()
print("tone written")
