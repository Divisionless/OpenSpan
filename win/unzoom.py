"""Put desktop magnification back to 1x, from outside the app.

The screen-zoom feature can leave a desktop-scope magnification behind if
its process ends without running its own stop() -- exactly what a hot swap
does. Nothing else on the machine will undo that; this does, and it is a
no-op when the desktop is not magnified. Run by swap-build.ps1 -CloseRunning.
"""
import ctypes
import ctypes.wintypes as wt
import sys

try:
    mag = ctypes.WinDLL("Magnification", use_last_error=True)
    mag.MagInitialize.restype = wt.BOOL
    mag.MagUninitialize.restype = wt.BOOL
    mag.MagSetFullscreenTransform.restype = wt.BOOL
    mag.MagSetFullscreenTransform.argtypes = [ctypes.c_float, ctypes.c_int, ctypes.c_int]
    if not mag.MagInitialize():
        print("unzoom: MagInitialize failed (nothing to undo)")
        sys.exit(0)
    try:
        ok = mag.MagSetFullscreenTransform(1.0, 0, 0)
        print("unzoom: magnification reset to 1x" if ok
              else "unzoom: reset refused (was probably not magnified)")
    finally:
        mag.MagUninitialize()
except OSError as exc:
    print(f"unzoom: Magnification API unavailable ({exc}); nothing to undo")
