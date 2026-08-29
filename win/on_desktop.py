"""The app lives ON THE DESKTOP -- and since 2026-08-29, so does its dock.

This is not a mode. Docked to the right edge of the chosen EsotericOS Desktop
monitor's work area, full height, no caption, and pinned to the BOTTOM of the
z-order is what this application *is*; `float_window` is the escape hatch that
gives it back an ordinary frame, and it is off unless someone asks for it.

The whole trick is one message. Windows lets a window refuse to be raised:
on WM_WINDOWPOSCHANGING we rewrite the WINDOWPOS Windows is about to act on so
it says "insert after HWND_BOTTOM", and clear SWP_NOZORDER so that instruction
is honoured. Every raise the shell, Tk, or the user attempts still *activates*
the window -- it takes focus, it types, its menus work -- it simply never comes
up the stack. That is exactly how Rainmeter's "on desktop" behaves, and why
this needs no always-on-bottom timer and no second window to mirror.

TWO WINDOWS USE THIS NOW, and the second is why `pin` is a parameter rather
than the constant HWND_BOTTOM it was until 2026-08-29. The dock rail left the
app's window that day and became a strip of its own on the same edge, and a
dock that sinks behind Chrome is a dock nobody can click -- it is the only way
back to an app window that is hidden. So the strip is pinned to HWND_TOPMOST
and the app to HWND_BOTTOM, through the SAME rewrite, the same refusal of every
move that is not ours, and the same re-dock on a display or work-area change.
A second placement path would be a second thing to keep in step with the first.

The strip also needs two things a full-height window does not: a HEIGHT that is
its content's rather than the work area's (`height=`), and a promise that it
will not grow down into the app-icons dock the shell puts on the same edge
(`gap=`). And the app window needs the mirror of that: `reserve=`, the band on
the right edge it must NOT take, because the strip is standing in it.

Everything here is either a pure function or a call through a bindings object,
so the tests drive the controller with fakes and never touch a real HWND.
"""

import ctypes
import ctypes.wintypes as wt


# ---- Win32 constants (named once, so no bare hex sits in the logic) ---------

GWL_STYLE = -16
GWL_EXSTYLE = -20
GWLP_WNDPROC = -4

WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080     # no taskbar button -- the tray is the way in
WS_EX_NOACTIVATE = 0x08000000     # deliberately NOT set: it must take focus

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020

HWND_BOTTOM = 1
HWND_TOPMOST = -1                 # ...and the other direction: the dock strip

SW_HIDE = 0
SW_SHOWNA = 8                     # show WITHOUT activating (startup shouldn't steal)

WM_WINDOWPOSCHANGING = 0x0046
WM_DISPLAYCHANGE = 0x007E
WM_SETTINGCHANGE = 0x001A         # a shell bar appearing changes the work area

SPI_GETWORKAREA = 0x0030

INSET = 16                        # breathing room on all four sides
MIN_WIDTH = 560                   # narrower than this and the panes stop working
# ...and the floor under THAT floor. MIN_WIDTH protects a window that still has
# a pane in it; it is the wrong number for a window that deliberately has none.
# The DOCK STRIP is exactly that case -- a rail and nothing else -- and before
# 2026-08-29 a window this thin could not be placed on the desktop at all,
# because MIN_WIDTH was applied whether or not there was anything left to be
# too narrow for. So the floor is a per-controller value that a caller may
# LOWER to this absolute one, which is not a taste: below roughly this a rail
# entry no longer draws and there is nothing left on screen to click the app
# back open with. Nothing may be placed narrower, in any state, ever.
COLLAPSED_MIN_WIDTH = 48
# CLEAR SPACE KEPT BELOW A CONTENT-HEIGHT WINDOW, and it exists because this
# edge has TWO docks on it. The upper one is ours -- the EsotericOS strip, as
# tall as its own entries and no taller. The lower one is the shell's running
# app icons, and it is not this process's to place. Neither knows the other's
# size, so the contract between them is this band: a strip may never be placed
# so tall that less than this is left underneath it. Everything below
# `y + h + STRIP_GAP` belongs to the app-icons dock.
STRIP_GAP = 24


# ---- pure geometry ---------------------------------------------------------

def dock_rect(work_area, width, inset=INSET, min_width=MIN_WIDTH,
              height=None, reserve=0, gap=STRIP_GAP):
    """Where a window sits, from the work area and the size it has now.

    `work_area` is (left, top, right, bottom). The result is (x, y, w, h):
    flush to the RIGHT edge inset by `inset`, `inset` down from the top, and
    `2 * inset` shorter than the work area so it clears the bottom too.

    `min_width` is a PARAMETER rather than the constant, because the right
    floor depends on what the window is currently holding: MIN_WIDTH for a
    window with panes in it, the rail's own width for the dock strip. Callers
    that do not care get the ordinary floor; the strip is the one that asks for
    less, and never gets less than COLLAPSED_MIN_WIDTH -- see
    OnDesktop.set_min_width, which is where that clamp lives.

    `height` is None for a window whose height IS the work area -- that is the
    app, and it must follow a shell bar appearing. A number means the window is
    as tall as its own content instead, top-aligned, and it is clamped so that
    at least `gap` is left below it for the shell's app-icons dock. A strip
    that quietly grew over the lower dock would be the failure this codebase
    calls "presents as working": still placed, still visible, silently on top
    of something else.

    `reserve` is the band on the right edge this window may NOT take, because
    the strip is standing in it. It moves the window left AND takes the same
    pixels out of the width cap, so a window wide enough to fill the screen
    still stops where the strip begins rather than sliding under it.
    """
    left, top, right, bottom = work_area
    reserve = max(0, int(reserve))
    w = max(int(width), min_width)
    w = min(w, max(min_width, (right - left) - 2 * inset - reserve))
    span = max(1, (bottom - top) - 2 * inset)
    if height is None:
        h = span
    else:
        h = max(1, min(int(height), span - max(0, int(gap))))
    return (right - inset - reserve - w, top + inset, w, h)


def pin_z_order(pos, pin=HWND_BOTTOM):
    """Rewrite a WINDOWPOS so Windows puts the window at `pin` instead.

    Mutates in place (that is the contract of WM_WINDOWPOSCHANGING) and returns
    the same object so callers can assert on it. Clearing SWP_NOZORDER is the
    half people forget: without it Windows ignores hwndInsertAfter entirely and
    the rewrite goes through untouched.

    ONE function for both directions on purpose. It was `refuse_raise` while
    only the app used it, and the name stopped being true the day the dock
    strip needed the opposite pin: what it does is not "refuse a raise", it is
    "this window's place in the stack is not up for negotiation". HWND_BOTTOM
    refuses every raise; HWND_TOPMOST refuses every drop.
    """
    pos.hwndInsertAfter = pin
    pos.flags = pos.flags & ~SWP_NOZORDER
    return pos


def refuse_move(pos):
    """Rewrite a WINDOWPOS so the window stays exactly where it is.

    Built in means built in: a header drag, a `geometry()` from anywhere in the
    app, a Win+Arrow, an Aero snap -- every one arrives here as a
    WM_WINDOWPOSCHANGING and leaves with SWP_NOMOVE | SWP_NOSIZE set, so Windows
    keeps the old rectangle. Doug: "i shouldn't be able to drag it around,
    build it in." Our own dock() and redock are the only movers, and they raise
    the controller's `_placing` flag around their SetWindowPos so this is
    skipped for exactly that call.
    """
    pos.flags = pos.flags | SWP_NOMOVE | SWP_NOSIZE
    return pos


# ---- the real Win32 edge ---------------------------------------------------

class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", wt.DWORD),
                ("szDevice", ctypes.c_wchar * 32)]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(_RECT), wt.LPARAM)


class WINDOWPOS(ctypes.Structure):
    _fields_ = [("hwnd", wt.HWND), ("hwndInsertAfter", wt.HWND),
                ("x", ctypes.c_int), ("y", ctypes.c_int),
                ("cx", ctypes.c_int), ("cy", ctypes.c_int),
                ("flags", ctypes.c_uint)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, ctypes.c_uint,
                             wt.WPARAM, wt.LPARAM)


class Win32Bindings:
    """Every OS call the controller makes, in one swappable object."""

    def __init__(self):
        u = ctypes.windll.user32
        u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
        u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_void_p]
        u.CallWindowProcW.restype = ctypes.c_ssize_t
        u.CallWindowProcW.argtypes = [ctypes.c_void_p, wt.HWND, ctypes.c_uint,
                                      wt.WPARAM, wt.LPARAM]
        self.user32 = u

        # A private handle for monitor enumeration. Prototyping functions on
        # ctypes.windll.user32 mutates the process-wide cached object; this app
        # has several Win32 modules, and one stale prototype already made a
        # large HMONITOR disappear from the arrangement. Keep this surface
        # private and fully typed.
        monitors = ctypes.WinDLL("user32", use_last_error=True)
        monitors.EnumDisplayMonitors.restype = wt.BOOL
        monitors.EnumDisplayMonitors.argtypes = [
            wt.HDC, ctypes.c_void_p, _MONITORENUMPROC, wt.LPARAM]
        monitors.GetMonitorInfoW.restype = wt.BOOL
        monitors.GetMonitorInfoW.argtypes = [
            wt.HMONITOR, ctypes.POINTER(_MONITORINFOEXW)]
        self._monitor_user32 = monitors

    def work_area(self, device_name=None):
        """Usable bounds for one GDI display name, primary as fallback.

        ``SPI_GETWORKAREA`` can only describe Windows' primary display. The
        EsotericOS Desktop is an independent role, so a chosen secondary needs
        its own ``rcWork`` from ``GetMonitorInfoW``. A detached or renamed
        choice falls back to Windows' current primary without rewriting the
        preference; reconnecting the panel therefore restores it.
        """
        wanted = str(device_name or "").casefold()
        if wanted:
            selected = []
            primary = []
            callback_errors = []

            def collect(hmonitor, _hdc, _rect, _data):
                try:
                    info = _MONITORINFOEXW()
                    info.cbSize = ctypes.sizeof(info)
                    if not self._monitor_user32.GetMonitorInfoW(
                            hmonitor, ctypes.byref(info)):
                        return True
                    work = info.rcWork
                    bounds = (int(work.left), int(work.top),
                              int(work.right), int(work.bottom))
                    if str(info.szDevice).casefold() == wanted:
                        selected.append(bounds)
                    if info.dwFlags & 1:
                        primary.append(bounds)
                    return True
                except BaseException as exc:  # never escape a native callback
                    callback_errors.append(exc)
                    return False

            callback = _MONITORENUMPROC(collect)
            ok = self._monitor_user32.EnumDisplayMonitors(
                None, None, callback, 0)
            if callback_errors:
                raise callback_errors[0]
            if ok and selected:
                return selected[0]
            if ok and primary:
                return primary[0]

        rect = _RECT()
        self.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0,
                                          ctypes.byref(rect), 0)
        return (rect.left, rect.top, rect.right, rect.bottom)

    def window_rect(self, hwnd):
        rect = _RECT()
        self.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return (rect.left, rect.top, rect.right, rect.bottom)

    def make_wndproc(self, fn):
        return WNDPROC(fn)

    def windowpos(self, lparam):
        return ctypes.cast(lparam, ctypes.POINTER(WINDOWPOS)).contents


def toplevel_hwnd(widget):
    """The HWND Windows knows about for a Tk widget.

    `winfo_id()` is the Tk *child* window; the framed top-level is its parent,
    and that is the one carrying the style bits and the window procedure.
    GetAncestor(GA_ROOT=2) walks there in one call and is right even when Tk
    changes how many levels it nests.
    """
    try:
        u = ctypes.windll.user32
        return u.GetAncestor(widget.winfo_id(), 2) or widget.winfo_id()
    except Exception:  # noqa: BLE001
        return widget.winfo_id()


def window_class(hwnd):
    """GetClassName for the HWND -- used to confirm we grabbed the TkTopLevel."""
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


# ---- the controller --------------------------------------------------------

class OnDesktop:
    """Puts one window on the desktop, and takes it back off again.

    `hwnd_provider()` returns the top-level HWND; `geometry_provider()` returns
    the (x, y, w, h) it should occupy. Both are callables so the caller owns
    every Tk touch -- nothing in this class reads a widget, which is what makes
    it safe to call parts of it from inside a window procedure.

    `pin` is where in the z-order this window is nailed: HWND_BOTTOM for the
    app, HWND_TOPMOST for the dock strip that shows and hides it.
    `fixed_height` says the window is as tall as its own content rather than as
    tall as the work area, so a re-dock keeps the cached height instead of
    stretching to the new one.
    """

    def __init__(self, hwnd_provider, geometry_provider, bindings=None,
                 monitor_name=None, pin=HWND_BOTTOM, fixed_height=False):
        self._hwnd_provider = hwnd_provider
        self._geometry_provider = geometry_provider
        self._bindings = bindings or Win32Bindings()
        self._hwnd = None
        self._old_proc = None
        self._proc = None          # the ctypes thunk: MUST outlive the window
        self._old_style = None
        self._old_exstyle = None
        self._old_rect = None      # the framed geometry, to come back to
        self._docked_width = None  # what a re-dock re-uses; no Tk read needed
        self._docked_height = None    # ...and the same for a content height
        self._min_width = MIN_WIDTH   # the floor for the state it is in now
        self._reserve = 0          # right-edge band another window is standing in
        self._pin = int(pin)
        self._fixed_height = bool(fixed_height)
        self._placing = False      # True only inside our own SetWindowPos
        self._monitor_name = str(monitor_name or "")
        self.active = False
        self.last_error = ""

    # -- state ------------------------------------------------------------
    @property
    def saved_rect(self):
        return self._old_rect

    @property
    def monitor_name(self):
        return self._monitor_name

    @property
    def min_width(self):
        return self._min_width

    @property
    def docked_width(self):
        return self._docked_width

    @property
    def docked_height(self):
        return self._docked_height

    @property
    def pin(self):
        return self._pin

    @property
    def reserve(self):
        return self._reserve

    def set_monitor(self, device_name):
        """Choose the EsotericOS Desktop monitor and re-dock if it is live.

        This changes no Windows display setting. Windows primary and the
        EsotericOS Desktop remain two independent roles.
        """
        self._monitor_name = str(device_name or "")
        return self.redock_from_work_area() if self.active else True

    # -- apply / release ---------------------------------------------------
    def apply(self):
        """Frameless + tool-window + docked + refused a raise. Idempotent."""
        if self.active:
            return True
        b = self._bindings
        try:
            hwnd = self._hwnd_provider()
            if not hwnd:
                return False
            self._hwnd = hwnd
            self._old_rect = b.window_rect(hwnd)

            # A STYLE STRIP, not overrideredirect(True). This app already
            # frames itself that way (App._frameless_safe) after
            # overrideredirect cost it focus and a native drag; keeping one
            # mechanism means the header drag, minimize and Alt-Tab behave the
            # same whether the window is on the desktop or floating.
            self._old_style = b.user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
            b.user32.SetWindowLongPtrW(
                hwnd, GWL_STYLE,
                self._old_style & ~(WS_CAPTION | WS_THICKFRAME))

            # WS_EX_TOOLWINDOW only takes the taskbar button away across a
            # hide/show -- the shell reads it when the window is (re)shown.
            # NOT WS_EX_NOACTIVATE: this window must take keyboard focus.
            self._old_exstyle = b.user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            b.user32.ShowWindow(hwnd, SW_HIDE)
            b.user32.SetWindowLongPtrW(
                hwnd, GWL_EXSTYLE,
                (self._old_exstyle | WS_EX_TOOLWINDOW) & ~WS_EX_NOACTIVATE)
            b.user32.ShowWindow(hwnd, SW_SHOWNA)

            # Only the top-level is subclassed. The identify card and every Tk
            # menu are their OWN HWNDs, so they never inherit the refusal and
            # still pop above everything, which is the whole point of them.
            self._proc = b.make_wndproc(self._wndproc)
            # The thunk is held on self for the window's whole life; if it were
            # collected Windows would call freed memory on the next message.
            self._old_proc = b.user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC,
                                                        self._proc)

            self.active = True
            self.dock()
            return True
        except Exception as exc:  # noqa: BLE001 -- a framed window beats a crash
            self.last_error = str(exc)
            self.release()
            return False

    def release(self):
        """Back to an ordinary framed window, at the geometry it had before."""
        b = self._bindings
        hwnd = self._hwnd
        self.active = False
        if hwnd is None:
            return False
        try:
            if self._old_proc is not None:
                b.user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, self._old_proc)
            if self._old_exstyle is not None:
                b.user32.ShowWindow(hwnd, SW_HIDE)
                b.user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE,
                                           self._old_exstyle)
                b.user32.ShowWindow(hwnd, SW_SHOWNA)
            if self._old_style is not None:
                b.user32.SetWindowLongPtrW(hwnd, GWL_STYLE, self._old_style)
            if self._old_rect:
                left, top, right, bottom = self._old_rect
                b.user32.SetWindowPos(hwnd, 0, left, top, right - left,
                                      bottom - top,
                                      SWP_FRAMECHANGED | SWP_NOZORDER)
            ok = True
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            ok = False
        # The thunk is dropped only after the window procedure is back to the
        # original one -- freeing it while Windows can still call it is a fault.
        self._old_proc = None
        self._proc = None
        self._old_style = self._old_exstyle = None
        self._hwnd = None
        return ok

    # -- docking -----------------------------------------------------------
    def dock(self):
        """Move to the work-area right edge and sink to the bottom."""
        if not self.active or self._hwnd is None:
            return False
        x, y, w, h = self._geometry_provider()
        self._docked_width = w
        self._docked_height = h
        self._placing = True
        try:
            self._bindings.user32.SetWindowPos(
                self._hwnd, self._pin, x, y, w, h,
                SWP_FRAMECHANGED | SWP_NOACTIVATE)
        finally:
            self._placing = False
        return True

    def set_min_width(self, width):
        """Lower (or restore) the floor this dock may be placed at. CACHE ONLY.

        Nothing moves here. It is separate from set_width because the floor has
        to be known in a place that must not read Tk: the window procedure
        re-docks on a display or work-area change, and if the floor were asked
        for at that moment a collapsed dock would be snapped back up to
        MIN_WIDTH by the next shell bar that appeared. So the caller pushes it
        from the Tk thread, exactly as it pushes the docked width.

        COLLAPSED_MIN_WIDTH is the clamp, and it is what stops "the dock may be
        thin" from becoming "any window may be unusable".
        """
        try:
            wanted = int(width)
        except (TypeError, ValueError):
            return self._min_width
        self._min_width = max(wanted, COLLAPSED_MIN_WIDTH)
        return self._min_width

    def set_reserve(self, width):
        """The right-edge band this window may not take. CACHE ONLY.

        Pushed from the Tk thread for the same reason the floor is: the strip's
        width is a widget measurement, and the re-dock that has to respect it
        runs inside the window procedure, where a Tk read is the reentrancy
        fault this module exists to avoid. Nothing moves here.
        """
        try:
            self._reserve = max(0, int(width))
        except (TypeError, ValueError):
            pass
        return self._reserve

    def set_width(self, width):
        """Re-place the dock at a new width, right edge kept. THE RESIZER.

        `geometry()` cannot do this job: every move that is not our own is
        refused in _wndproc, which is the whole point of the refusal, so a
        window on the desktop is resized by asking the controller and in no
        other way. The result is clamped to the CURRENT floor, so a caller
        cannot get a narrow window without having said, through set_min_width,
        why it is allowed to be narrow.
        """
        try:
            self._docked_width = max(int(width), self._min_width)
        except (TypeError, ValueError):
            return False
        return self.redock_from_work_area()

    def redock_from_work_area(self):
        """Re-dock using only Win32 -- safe to call from inside the wndproc.

        The width, the height, the floor and the reserved band all come from
        cached values, never from a Tk read: the window procedure runs inside
        Windows' message dispatch, where touching Tk is the reentrancy fault
        this codebase has already paid for twice.
        """
        if not self.active or self._hwnd is None:
            return False
        width = self._docked_width or self._min_width
        x, y, w, h = dock_rect(
            self._bindings.work_area(self._monitor_name), width,
            min_width=self._min_width,
            height=self._docked_height if self._fixed_height else None,
            reserve=self._reserve)
        self._docked_width = w
        self._docked_height = h
        self._placing = True
        try:
            self._bindings.user32.SetWindowPos(
                self._hwnd, self._pin, x, y, w, h, SWP_NOACTIVATE)
        finally:
            self._placing = False
        return True

    def show_at_dock(self):
        """Visible and focused, still at its pin.

        Two callers, one act: the tray's "Open EsotericOS", and the dock strip
        invoking a surface on a window it had hidden. Both want the same thing
        -- the window back where it belongs and holding the keyboard -- and a
        window pinned to HWND_BOTTOM cannot be brought forward by lift(),
        because our own wndproc sinks it again. This IS "show window" here.
        """
        if not self.active or self._hwnd is None:
            return False
        self.dock()
        self._bindings.user32.SetForegroundWindow(self._hwnd)
        return True

    # -- the window procedure ---------------------------------------------
    def _wndproc(self, hwnd, msg, wparam, lparam):
        """PURE Win32 only. Never a Tk call, never an allocation that can raise
        -- this runs inside Windows' dispatch, where an exception becomes a
        native fault rather than a traceback."""
        try:
            if msg == WM_WINDOWPOSCHANGING and lparam:
                pos = self._bindings.windowpos(lparam)
                pin_z_order(pos, self._pin)
                if not self._placing:
                    refuse_move(pos)
            elif msg in (WM_DISPLAYCHANGE, WM_SETTINGCHANGE):
                self.redock_from_work_area()
        except Exception:  # noqa: BLE001
            pass
        return self._bindings.user32.CallWindowProcW(self._old_proc, hwnd, msg,
                                                     wparam, lparam)
