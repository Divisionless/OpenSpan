#!/usr/bin/env python3
"""Build OpenSpan into a single-file OpenSpan.exe (PyInstaller).

    C:\\Python313\\python.exe D:\\OpenSpan\\build_exe.py

Produces D:\\OpenSpan\\OpenSpan.exe. Because the app anchors all its data on
the executable's folder (sys.executable when frozen), the exe is dropped
straight into D:\\OpenSpan\\ next to the configs/keys/scripts it already uses
-- nothing else to assemble. No console window; unelevated (so it dodges the
same shell-reputation gate the openspanw.exe trick was created for).
"""
import os
import shutil
import sys

import PyInstaller.__main__

ROOT = os.path.dirname(os.path.abspath(__file__))
WIN = os.path.join(ROOT, "win")
ICON = os.path.join(ROOT, "openspan.ico")
DIST = os.path.join(ROOT, "dist")
BUILD = os.path.join(ROOT, "build")

args = [
    os.path.join(WIN, "openspan_launcher.py"),
    "--name", "OpenSpan",
    "--onefile",
    "--noconsole",                 # GUI app: no console window
    "--distpath", DIST,
    "--workpath", BUILD,
    "--specpath", BUILD,
    "--paths", WIN,                # so runpy finds the role modules
    # role modules are loaded by name via runpy -> bundle them explicitly
    "--hidden-import", "openspan",
    "--hidden-import", "openspan_portal",
    "--hidden-import", "win_audio_send",
    "--hidden-import", "openspan_setup",
    # dependency trees PyInstaller can under-collect (COM codegen, native)
    "--collect-all", "pycaw",
    "--collect-all", "comtypes",
    "--collect-all", "pyaudiowpatch",
    "--collect-submodules", "numpy",
    "--noconfirm",
    "--clean",
]
if os.path.exists(ICON):
    args += ["--icon", ICON]

print("building OpenSpan.exe …")
PyInstaller.__main__.run(args)

built = os.path.join(DIST, "OpenSpan.exe")
if not os.path.exists(built):
    sys.exit("BUILD FAILED: OpenSpan.exe not produced")
target = os.path.join(ROOT, "OpenSpan.exe")
shutil.copy2(built, target)
size_mb = os.path.getsize(target) / (1024 * 1024)
print(f"\nOK -> {target}  ({size_mb:.0f} MB)")
print("Runs in place (D:\\OpenSpan already holds the data files it needs).")
