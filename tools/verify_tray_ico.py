"""Prove every .ico frame is the kit PNG byte-for-byte, and that Windows
can actually load the icon (LoadImageW round-trip at tray sizes)."""
import ctypes
import pathlib
import struct
import sys

KIT = pathlib.Path(
    r"D:\EsotericOS_Antigrav\EsotericOS_Astral_Compass_Brand_Kit_v1.0")
SRC = KIT / "logo" / "png" / "tray-color"
ICO = pathlib.Path(__file__).parent.parent / "brand" / "esotericos-tray.ico"

data = ICO.read_bytes()
count = struct.unpack_from("<H", data, 4)[0]
bad = 0
seen = []
for i in range(count):
    w, h = data[6 + 16 * i], data[7 + 16 * i]
    size, off = struct.unpack_from("<II", data, 6 + 16 * i + 8)
    frame = data[off:off + size]
    s = w or 256
    seen.append(s)
    want = (SRC / f"esotericos-tray-color-{s}.png").read_bytes()
    same = frame == want
    print(("PASS" if same else "FAIL"), f"{s}px frame is the kit PNG verbatim")
    bad += 0 if same else 1

print(("PASS" if seen == [16, 20, 24, 32, 48, 64] else "FAIL"),
      f"frame inventory {seen}")
bad += 0 if seen == [16, 20, 24, 32, 48, 64] else 1

# Windows load check at the two sizes the tray actually asks for.
for s in (16, 32):
    h = ctypes.windll.user32.LoadImageW(None, str(ICO), 1, s, s, 0x10)
    ok = bool(h)
    print(("PASS" if ok else "FAIL"), f"LoadImageW {s}px returns an HICON")
    bad += 0 if ok else 1
    if h:
        ctypes.windll.user32.DestroyIcon(h)

sys.exit(1 if bad else 0)
