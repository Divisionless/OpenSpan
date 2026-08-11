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
ICON = os.path.join(ROOT, "brand", "esotericos-app.ico")
if not os.path.isfile(ICON):
    ICON = os.path.join(ROOT, "openspan.ico")
DIST = os.path.join(ROOT, "dist")
BUILD = os.path.join(ROOT, "build")
# The product is EsotericOS. This defaulted to "OpenSpan" long after the
# rename, so a plain `python build_exe.py` quietly produced the old name --
# which is how six differently-named binaries ended up in one folder, and why
# the build stamp has to derive "is this a test build?" from the exe's name.
# The env var still overrides, for staging a build under a throwaway name.
NAME = os.environ.get("ESOTERICOS_BUILD_NAME",
                      os.environ.get("OPENSPAN_BUILD_NAME", "EsotericOS")
                      ).strip() or "EsotericOS"

args = [
    os.path.join(WIN, "openspan_launcher.py"),
    "--name", NAME,
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
    "--hidden-import", "openspan_targets",
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

print(f"building {NAME}.exe …")
PyInstaller.__main__.run(args)

built = os.path.join(DIST, f"{NAME}.exe")
if not os.path.exists(built):
    sys.exit(f"BUILD FAILED: {NAME}.exe not produced")
target = os.path.join(ROOT, f"{NAME}.exe")


def _locked(path):
    """True when Windows will refuse to overwrite this file.

    Doug works while builds run -- that is the normal rhythm here, not an
    accident -- so the target is often the exe he is using. Opening it for
    append is the cheap way to ask Windows, and it changes nothing.
    """
    if not os.path.exists(path):
        return False
    try:
        with open(path, "ab"):
            return False
    except PermissionError:
        return True


# Ask BEFORE spending two minutes compiling into a copy that cannot land.
if _locked(target):
    target = os.path.join(ROOT, f"{NAME}-next.exe")
    staged = True
else:
    staged = False

shutil.copy2(built, target)
size_mb = os.path.getsize(target) / (1024 * 1024)
print(f"\nOK -> {target}  ({size_mb:.0f} MB)")
if staged:
    print(f"\n{NAME}.exe is RUNNING, so this build was staged beside it.")
    print("When you are ready to swap, with the app closed:")
    print(f"    cd /d/{os.path.basename(ROOT)} && "
          f"mv -f {NAME}.exe {NAME}.exe.prev && "
          f"mv -f {NAME}-next.exe {NAME}.exe && (./{NAME}.exe &)")
print(f"Runs in place ({ROOT} holds the data files it needs).")
