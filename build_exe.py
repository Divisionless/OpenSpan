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
    # The optional modules ship WITH the app. They are discovered by reading
    # plugin.json off disk, so they must exist as real files at runtime --
    # a hidden-import would not do, because nothing imports them by name.
    # module_host.bundled_root() looks in sys._MEIPASS/modules when frozen,
    # which is exactly where --add-data puts this.
    "--add-data", os.path.join(WIN, "modules") + os.pathsep + "modules",
    # WHAT THE MODULES THEMSELVES IMPORT MUST BE DECLARED HERE.
    #
    # A module ships as DATA, so PyInstaller never analyses it and cannot see
    # its imports. On 2026-08-15 the agent monitor shipped and loaded and did
    # nothing: replacing _usage_worker removed the last statically visible
    # `import usage_monitor` from analysed code, so usage_monitor was dropped
    # from the bundle entirely and every row came back ModuleNotFoundError.
    # Nothing failed loudly -- the panel simply reported that it could not
    # read anything.
    #
    # test_module_deps.py enforces this list against what the modules
    # actually import, so a new module cannot quietly ship without its
    # dependencies.
    "--hidden-import", "usage_monitor",
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

# NOTHING IS OVERWRITTEN WITHOUT BEING KEPT. On 2026-08-15 this script wrote
# straight over EsotericOS.exe -- the declared benchmark, sha b13ac1d0... --
# because the app happened to be closed, so the lock check passed and the
# staging path never ran. Those bytes are gone. The rollback the manual swap
# instructions describe (mv EsotericOS.exe EsotericOS.exe.prev) only ever
# happened when the user typed it, which is exactly the moment a build does
# not need it: a build landing in place is when the outgoing binary is about
# to disappear.
if os.path.exists(target):
    # ANY existing target, staged or not. The first version of this guard was
    # written `if not staged`, which is the very mistake it was added to fix:
    # a safety conditioned on a narrow trigger. The staged path then quietly
    # overwrote EsotericOS-next.exe -- which was the seam-fix build -- and it
    # survived only because a copy had been made by hand hours earlier. That
    # is luck, not structure.
    keep = target + ".prev"
    try:
        shutil.copy2(target, keep)
        print(f"kept the outgoing build as {os.path.basename(keep)}")
    except OSError as exc:
        sys.exit(f"REFUSING to overwrite {target}: could not preserve it first "
                 f"({exc}). Move it aside yourself, then build again.")

shutil.copy2(built, target)
size_mb = os.path.getsize(target) / (1024 * 1024)
print(f"\nOK -> {target}  ({size_mb:.0f} MB)")
if staged:
    print(f"\n{NAME}.exe is RUNNING, so this build was staged beside it.")
    print("When you are ready to swap, with the app closed:")
    # The whole path, not its last component. This printed "cd /d/app" after
    # the tree moved to D:\_EsotericOS\app, because basename() had been right
    # only while the root sat directly on D:. A swap command that silently
    # names the wrong directory is worse than none: it runs, and mv reports
    # nothing useful about a folder you did not mean.
    posix_root = "/" + ROOT[0].lower() + ROOT[2:].replace("\\", "/")
    print(f"    cd {posix_root} && "
          f"mv -f {NAME}.exe {NAME}.exe.prev && "
          f"mv -f {NAME}-next.exe {NAME}.exe && (./{NAME}.exe &)")
print(f"Runs in place ({ROOT} holds the data files it needs).")
