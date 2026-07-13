#!/usr/bin/env python3
"""Frozen entry point for the single-file OpenSpan.exe.

The GUI spawns the input portal and the audio sender as separate processes.
In plain Python those are separate scripts; in the packed exe there is only
ONE binary, so it re-invokes itself with a role flag:

    OpenSpan.exe            -> the control GUI (default)
    OpenSpan.exe --portal   -> the input portal (openspan_portal.py)
    OpenSpan.exe --audio    -> the audio sender (win_audio_send.py)
    OpenSpan.exe --setup    -> the monitor-arrangement setup window
    OpenSpan.exe --traytest -> create+destroy the tray icon, print a result,
                               exit 0/1 (a frozen-mode self-check; no window)

Keep this the PyInstaller entry script so every role lands in the one exe.
"""
import runpy
import sys


def _ensure_std_streams():
    """A --noconsole PyInstaller build has sys.stdout/sys.stderr = None. Any C
    callback (e.g. the tray WNDPROC) that raises makes ctypes try to write a
    traceback to stderr; writing to None faults and HARD-CRASHES the process
    (0xc000041d in _ctypes.pyd). Give the streams a real writable sink so a
    callback exception is logged harmlessly instead of killing the app."""
    if not getattr(sys, "frozen", False):
        return
    if sys.stdout is not None and sys.stderr is not None:
        return
    import os
    try:
        base = os.path.dirname(sys.executable)
        sink = open(os.path.join(base, "openspan_frozen.log"), "a",
                    buffering=1, encoding="utf-8", errors="replace")
    except Exception:
        import io
        sink = io.StringIO()  # last resort: never leave the streams None
    if sys.stdout is None:
        sys.stdout = sink
    if sys.stderr is None:
        sys.stderr = sink


def _traytest():
    """Frozen-mode self-check: exercise the exact ctypes tray path that used to
    crash (WNDPROC callback during CreateWindow + NIM_ADD), then tear it down.
    Prints TRAYTEST_OK / TRAYTEST_FAIL and exits 0/1. A hard crash here (no
    output, nonzero) means the tray path is still unsafe in the frozen exe."""
    import tkinter as tk
    import openspan
    root = tk.Tk()
    root.withdraw()
    ok = False
    try:
        tray = openspan.TrayIcon("OpenSpan self-check", openspan.ICON,
                                 lambda: None)
        root.after(500, root.quit)   # pump the message loop so WNDPROC runs
        root.mainloop()
        tray.destroy()
        ok = True
    except Exception as exc:  # noqa: BLE001
        print("traytest exception:", exc)
    print("TRAYTEST_OK" if ok else "TRAYTEST_FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    _ensure_std_streams()
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--portal":
        del sys.argv[1]
        runpy.run_module("openspan_portal", run_name="__main__")
    elif arg == "--audio":
        del sys.argv[1]
        runpy.run_module("win_audio_send", run_name="__main__")
    elif arg == "--setup":
        del sys.argv[1]
        runpy.run_module("openspan_setup", run_name="__main__")
    elif arg == "--traytest":
        _traytest()
    else:
        import openspan
        openspan.run_app()
