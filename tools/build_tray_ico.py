"""Compose brand/esotericos-tray.ico from the kit's EXACT tray PNGs.

The kit's tray assets are size-specific designs (16px drops detail 32px
carries) and its brief forbids rescaling them. Pillow's ICO writer keeps an
opinionated size list and silently dropped the 20px frame, so this assembles
the ICO container by hand: ICONDIR + one directory entry per frame + the
kit PNG files embedded byte-for-byte. PNG-compressed frames are valid in
.ico on the Windows versions this app supports; fidelity is by construction
because the payload IS the kit file.
"""
import pathlib
import struct
import sys

KIT = pathlib.Path(
    r"D:\EsotericOS_Antigrav\EsotericOS_Astral_Compass_Brand_Kit_v1.0")
SRC = KIT / "logo" / "png" / "tray-color"
OUT = pathlib.Path(__file__).parent.parent / "brand" / "esotericos-tray.ico"

sizes = [16, 20, 24, 32, 48, 64]
blobs = []
for s in sizes:
    p = SRC / f"esotericos-tray-color-{s}.png"
    if not p.is_file():
        sys.exit(f"missing kit asset: {p}")
    blobs.append((s, p.read_bytes()))

header = struct.pack("<HHH", 0, 1, len(blobs))
entries = b""
offset = 6 + 16 * len(blobs)
for s, data in blobs:
    entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32,
                           len(data), offset)
    offset += len(data)
OUT.write_bytes(header + entries + b"".join(d for _, d in blobs))
print(f"OK -> {OUT} ({OUT.stat().st_size} bytes, frames {sizes})")
