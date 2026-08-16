"""Live proof that on_desktop.py really sits on the desktop and still types.

NOT a test (the suite globs test_*.py): it opens a real window, launches
Notepad over it, walks the z-order, synthesises typing into an Entry, then
closes both. Run it by hand:

    C:\\Python313\\python.exe win\\probe_on_desktop.py

It prints the EnumWindows walk from the bottom up -- Progman must be last, the
probe window second-from-last -- and the text the Entry received.
"""

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import tkinter as tk

import on_desktop

u = ctypes.windll.user32
ENUM = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)


def visible_stack():
    """Top-level visible windows, TOP first (EnumWindows' own order)."""
    out = []

    def cb(hwnd, _l):
        if u.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(hwnd, buf, 256)
            cls = on_desktop.window_class(hwnd)
            if buf.value or cls in ("Progman", "WorkerW"):
                out.append((hwnd, cls, buf.value))
        return True

    u.EnumWindows(ENUM(cb), 0)
    return out


def main():
    root = tk.Tk()
    root.title("ON-DESKTOP PROBE")
    root.geometry("600x400+200+200")
    entry = tk.Entry(root, font=("Consolas", 16))
    entry.pack(fill="x", padx=20, pady=40)
    root.update()

    hwnd = on_desktop.toplevel_hwnd(root)
    print(f"root.winfo_id() class = {on_desktop.window_class(root.winfo_id())}")
    print(f"GetAncestor(GA_ROOT) class = {on_desktop.window_class(hwnd)}")

    ctl = on_desktop.OnDesktop(
        lambda: hwnd,
        lambda: on_desktop.dock_rect(on_desktop.Win32Bindings().work_area(),
                                     root.winfo_width()))
    print("apply() ->", ctl.apply(), ctl.last_error)
    root.update()

    notepad = subprocess.Popen(["notepad.exe"])
    for _ in range(60):                      # let Notepad show and take the top
        root.update()
        root.after(50)
        u.GetMessageW  # keep the reference; the pump below is Tk's
        root.update_idletasks()
    root.after(1500, root.quit)
    root.mainloop()

    stack = visible_stack()
    print("\n--- z-order, BOTTOM first ---")
    for h, cls, title in reversed(stack[-8:]):
        mark = "  <== PROBE" if h == hwnd else ""
        print(f"  {h:#010x}  {cls:<24} {title[:40]!r}{mark}")

    # typing: click the entry, then send real keystrokes through the OS
    rect = on_desktop.Win32Bindings().window_rect(hwnd)
    ex = entry.winfo_rootx() + 30
    ey = entry.winfo_rooty() + 10
    u.SetCursorPos(int(ex), int(ey))
    u.mouse_event(0x0002, 0, 0, 0, 0)        # LEFTDOWN
    u.mouse_event(0x0004, 0, 0, 0, 0)        # LEFTUP
    root.after(300, root.quit)
    root.mainloop()
    for ch in "ONDESK":
        vk = ord(ch)
        u.keybd_event(vk, 0, 0, 0)
        u.keybd_event(vk, 0, 2, 0)
    root.after(400, root.quit)
    root.mainloop()
    typed = entry.get()
    print(f"\nwindow rect after dock: {rect}")
    print(f"entry received: {typed!r}")
    print("foreground is probe:",
          u.GetForegroundWindow() == hwnd)

    stack2 = visible_stack()
    print("\n--- z-order AFTER typing, BOTTOM first ---")
    for h, cls, title in reversed(stack2[-8:]):
        mark = "  <== PROBE" if h == hwnd else ""
        print(f"  {h:#010x}  {cls:<24} {title[:40]!r}{mark}")

    ctl.release()
    root.destroy()
    notepad.terminate()
    print("\nPROBE DONE")
    return 0 if typed else 1


if __name__ == "__main__":
    sys.exit(main())
