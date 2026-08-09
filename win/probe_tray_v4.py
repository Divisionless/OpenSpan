"""Live, Tk-free probe for the NOTIFYICON_VERSION_4 tray path."""

import ctypes
import ctypes.wintypes as wt
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from openspan import TrayIcon  # noqa: E402


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wt.HWND), ("message", wt.UINT),
                ("wParam", wt.WPARAM), ("lParam", wt.LPARAM),
                ("time", wt.DWORD), ("pt", wt.POINT)]


def main():
    icon_path = (pathlib.Path(__file__).parent.parent / "brand" /
                 "esotericos-tray.ico")
    tray = None
    try:
        tray = TrayIcon("EsotericOS probe", str(icon_path), lambda: None)
        print(f"NIM_ADD returned {int(tray._nim_add_ok)}")
        print(f"NIM_SETVERSION returned {int(tray._nim_setversion_ok)}")
        if not tray._nim_add_ok or not tray._nim_setversion_ok:
            return 1

        user32 = ctypes.windll.user32
        user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), wt.HWND,
                                        wt.UINT, wt.UINT, wt.UINT]
        user32.PeekMessageW.restype = wt.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
        user32.DispatchMessageW.restype = ctypes.c_ssize_t
        msg = MSG()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0x0001):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.01)
        print("PASS tray V4 live probe")
        return 0
    except OSError as exc:
        print("NIM_ADD returned 0")
        print("NIM_SETVERSION not attempted")
        print(f"FAIL tray V4 live probe: {exc}")
        return 1
    finally:
        if tray is not None:
            tray.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
