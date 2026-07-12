#!/usr/bin/env python3
"""Frozen entry point for the single-file OpenSpan.exe.

The GUI spawns the input portal and the audio sender as separate processes.
In plain Python those are separate scripts; in the packed exe there is only
ONE binary, so it re-invokes itself with a role flag:

    OpenSpan.exe            -> the control GUI (default)
    OpenSpan.exe --portal   -> the input portal (openspan_portal.py)
    OpenSpan.exe --audio    -> the audio sender (win_audio_send.py)
    OpenSpan.exe --setup    -> the monitor-arrangement setup window

Keep this the PyInstaller entry script so every role lands in the one exe.
"""
import runpy
import sys

if __name__ == "__main__":
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
    else:
        import openspan
        openspan.run_app()
