"""Which shell owns this session, and therefore what kind of thing we are.

Doug: "there is never a point when i want to be operating this computer
without that thing, unless i am in windows regular for whatever reason --
that'd be debugging special case ... further solidify it as the surface of the
desktop, remove the minimize, X buttons."

So the app is two things depending on who is the shell:

  SURFACE MODE -- the EsotericOS Shell (a Cairo fork, process CairoDesktop) is
      the shell. The app is part of the desktop surface: no minimize button, no
      close button, WM_CLOSE (which is what Alt+F4 becomes) ignored. There is
      no casual way to be left with a machine that has a shell and no app.

  WINDOW MODE -- Explorer is the shell. That is the deliberate debugging visit,
      and the app behaves exactly as it always has: minimize, X, the close
      dialog.

ONE DECISION, MADE ONCE. `decide_mode()` runs at startup and the answer is
stored; nothing downstream re-probes, so the app cannot be half of each.

Nothing here blocks session end. Windows ends a session with
WM_QUERYENDSESSION / WM_ENDSESSION, not WM_CLOSE -- refusing WM_CLOSE is
invisible to sign-out, restart and shutdown, and that is the whole reason the
refusal is placed there rather than in an exit handler.

Pure standard library (ctypes + winreg). No dependencies.
"""

import os
import sys

SURFACE = "surface"
WINDOW = "window"

# The EsotericOS Shell is a Cairo Shell fork; its image name is what both the
# live probe and the registry string are matched against, lowercased.
CAIRO_IMAGE = "cairodesktop.exe"
EXPLORER_IMAGE = "explorer.exe"

FORCE_WINDOW_FLAG = "--window"
FORCE_SURFACE_FLAG = "--surface"


# ---- pure decision ---------------------------------------------------------

def decide_mode(argv=None, shell_probe=None):
    """SURFACE or WINDOW, from the command line first and the shell second.

    `shell_probe` is a callable returning the image name of the process that is
    acting as this session's shell (lowercased, or "" when unknown); it is a
    parameter so the tests decide the answer without a real session.

    An explicit flag always wins -- that is the escape hatch, and an escape
    hatch that can be overruled by a probe is not one. Otherwise: Cairo means
    surface, and ANYTHING ELSE (Explorer, unknown, a probe that threw) means
    window. The unsafe direction here is being un-closeable by accident, so
    uncertainty resolves to the closeable mode.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if FORCE_WINDOW_FLAG in args:
        return WINDOW
    if FORCE_SURFACE_FLAG in args:
        return SURFACE
    probe = shell_probe or session_shell_image
    try:
        image = (probe() or "").strip().lower()
    except Exception:  # noqa: BLE001 -- a probe fault must not decide anything
        return WINDOW
    return SURFACE if image == CAIRO_IMAGE else WINDOW


def is_surface(mode):
    return mode == SURFACE


# ---- the live probe --------------------------------------------------------
#
# WHY NOT THE REGISTRY FIRST. HKCU\...\Winlogon\Shell says what Winlogon WILL
# start at the next sign-in. It does not say what is running now, and the one
# case that matters is exactly the case where the two disagree: Doug kills the
# Cairo shell and starts Explorer to debug something. The registry still reads
# CairoDesktop, so a registry-led check would lock the app into surface mode
# during the very visit that mode exists to stay out of. The live process is
# the fact; the registry is an intention. So: probe the session, and consult
# the registry only when the probe cannot see anything at all (which is what
# an early-boot start, before the shell has registered, looks like).

def _shell_window_image(user32=None, image_of=None):
    """Image name of the process owning GetShellWindow(), or ""...

    GetShellWindow() is the desktop shell's own window. Under Explorer it is
    Progman and this returns explorer.exe -- a positive identification of a
    debugging visit, not an inference from an absence.
    """
    import ctypes
    import ctypes.wintypes as wt
    u = user32 or ctypes.windll.user32
    hwnd = u.GetShellWindow()
    if not hwnd:
        return ""
    pid = wt.DWORD(0)
    u.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
    if not pid.value:
        return ""
    return (image_of or process_image_name)(pid.value)


def process_image_name(pid):
    """The .exe name for a pid, lowercased, or "" when it cannot be read.

    QueryFullProcessImageNameW under PROCESS_QUERY_LIMITED_INFORMATION is the
    one that works without elevation and across integrity levels -- the shell
    may well be running at a different level than we are.
    """
    import ctypes
    import ctypes.wintypes as wt
    k = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return ""
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not k.QueryFullProcessImageNameW(wt.HANDLE(h), 0, buf,
                                            ctypes.byref(size)):
            return ""
        return os.path.basename(buf.value).lower()
    finally:
        k.CloseHandle(h)


def _running_images():
    """Every process image name in the system, lowercased.

    CreateToolhelp32Snapshot rather than a WMI query or a psutil dependency:
    it is in kernel32, it needs no privileges for the name alone, and it cannot
    hang the way a WMI call can.
    """
    import ctypes
    import ctypes.wintypes as wt

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                    ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                    ("th32ParentProcessID", wt.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k = ctypes.windll.kernel32
    TH32CS_SNAPPROCESS = 0x00000002
    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1 or not snap:
        return []
    out = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out.append(entry.szExeFile.lower())
            ok = k.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k.CloseHandle(snap)
    return out


def registry_shell_image():
    """The image name HKCU (then HKLM) Winlogon\\Shell will start, or "".

    Consulted LAST and only as a tiebreak -- see the note above.
    """
    import winreg
    path = r"Software\Microsoft\Windows NT\CurrentVersion\Winlogon"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, path) as key:
                value, _ = winreg.QueryValueEx(key, "Shell")
        except OSError:
            continue
        value = (value or "").strip().strip('"')
        if value:
            return os.path.basename(value).lower()
    return ""


def session_shell_image(shell_window=None, running=None, registry=None):
    """Who is the shell, as an image name. "" when it cannot be determined.

    The order is live-fact first:
      1. the process owning GetShellWindow() -- decisive when it answers;
      2. a running CairoDesktop with no Explorer shell window -- which is what
         a Cairo session looks like, since Cairo registers no shell window;
      3. the Winlogon Shell value -- intention only, for the pre-shell window
         during boot.
    """
    try:
        image = (shell_window or _shell_window_image)()
    except Exception:  # noqa: BLE001
        image = ""
    if image:
        return image
    try:
        images = (running or _running_images)()
    except Exception:  # noqa: BLE001
        images = []
    if CAIRO_IMAGE in images:
        return CAIRO_IMAGE
    if EXPLORER_IMAGE in images:
        # Explorer is up but owns no shell window yet: still Explorer's session.
        return EXPLORER_IMAGE
    try:
        return (registry or registry_shell_image)()
    except Exception:  # noqa: BLE001
        return ""
