"""The dialogs must never become windows again.

Doug, 28 July: *"i don't want this program to generate popouts -- if i click
into something i want it right there, this just popped up on another screen."*

On a desk with five screens across two machines, a new OS window is placed by
Windows, not by the user. The dialog he asked for appeared somewhere he was not
looking. So every dialog now lives inside the app window, and `FrameModal` is
what lets that happen without rewriting each one.

What is checked here is the part that is easy to get wrong and impossible to see
in a code review: a Frame is NOT a Toplevel, and four of its inherited
behaviours differ in ways that would break a dialog silently rather than loudly.

    * a Toplevel sees events from its children; a frame does not, so a
      `<Return>` binding would stop firing the moment focus entered the dialog's
      own entry box -- which is the only place focus ever is
    * bindings moved onto the window must come back off, or the dialog keeps
      answering Enter after it is gone
    * a modal opened over a modal must hand the grab back, or the first one
      quietly stops being modal
    * `wait_window` must return, or the caller hangs forever

Runs headless: the root is withdrawn, so nothing appears on screen and a running
OpenSpan is untouched.

Exit 0 = all pass.
"""
import ast
import os
import sys
import tkinter as tk

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openspan as A  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (
        "" if cond or not detail else "\n      " + detail))
    if not cond:
        fails.append(name)


root = tk.Tk()
root.withdraw()            # never draw on the desk this is being run from
root.geometry("1120x930")
root.update_idletasks()

# ---- it is not a window ----------------------------------------------------
modal = A.FrameModal(root)
check("a modal is not a top-level window",
      not isinstance(modal, tk.Toplevel)
      and modal.winfo_toplevel() is root,
      f"toplevel was {modal.winfo_toplevel()}")
check("it lives inside the app's own window",
      str(modal).startswith(str(root)) and str(modal) != str(root), str(modal))

# ---- the window-manager calls a dialog makes -------------------------------
modal.title("Screen sizes")
check("title is remembered, not drawn twice",
      modal.title() == "Screen sizes"
      and not [w for w in modal.winfo_children()], modal.title())
for call, args in (("transient", (root,)), ("resizable", (False, False)),
                   ("minsize", (820, 340)), ("withdraw", ()),
                   ("deiconify", ()), ("protocol", ("WM_DELETE_WINDOW",
                                                    modal.destroy))):
    try:
        getattr(modal, call)(*args)
    except Exception as exc:  # noqa: BLE001
        check(f"{call}() is accepted", False, repr(exc))
        break
else:
    check("every window-manager call a dialog makes is accepted", True)

modal.geometry("900x420")
root.update_idletasks()
card = modal.master
check("a requested size is recorded, not applied on the spot",
      modal._want == (900, 420)
      and not card.place_info().get("width"),
      f"want={modal._want} placed width={card.place_info().get('width')!r}")
check("a screen position is discarded rather than obeyed",
      modal.geometry("+400+120") == "" and modal._want == (900, 420))

# ---- binding: the failure that would have been silent ----------------------
seen = []
modal.bind("<Return>", lambda _e: seen.append("modal"))
entry = tk.Entry(modal)
entry.pack()
entry.focus_set()
root.update()
root.event_generate("<Return>", when="now")
check("Enter reaches the dialog while focus is in its own entry box",
      seen == ["modal"], f"handler fired {len(seen)} times")

# ---- a stray click must not throw away what was typed ----------------------
# The regression this closes: the scrim covers the whole window, and binding it
# to close meant one click in the dimmed area destroyed the display editor with
# a table of resolutions half filled in. The Toplevels it replaced had no
# click-outside gesture at all.
gone = []
modal.protocol("WM_DELETE_WINDOW", lambda: gone.append("closed"))
scrim_probe = card.master
scrim_probe.event_generate("<Button-1>", x=5, y=5, when="now")
root.update()
check("clicking the dimmed area does not discard the dialog",
      gone == [] and modal.winfo_exists(), f"{gone}")

# ---- it is as big as its contents need, never bigger than the window --------
tall = A.FrameModal(root)
tall.geometry("900x420")
for i in range(24):                      # a device with a lot of screens
    tk.Label(tall, text=f"row {i}", bg="#070A0F", fg="#ddd").pack(fill="x")
foot = tk.Frame(tall, bg="#070A0F", height=39)
foot.pack(side="bottom", fill="x")
tall.grab_set()                          # every dialog grabs last; that fits it
root.update_idletasks()
tall_card = tall.master
check("a dialog taller than its requested size is not pinned to it",
      int(tall_card.place_info()["height"]) > 420,
      f"card height {tall_card.place_info().get('height')}")
check("and is still not taller than the window it lives in",
      int(tall_card.place_info()["height"]) <= root.winfo_height(),
      f"card {tall_card.place_info().get('height')} vs "
      f"window {root.winfo_height()}")
check("a requested width is a floor, not a ceiling",
      int(tall_card.place_info()["width"]) >= min(900, root.winfo_width() - 40),
      str(tall_card.place_info().get("width")))
tall.destroy()

# ---- closing puts the window back exactly as it was ------------------------
modal.grab_set()
scrim = card.master
modal.destroy()
root.update()
seen.clear()
root.event_generate("<Return>", when="now")
check("its bindings come off the window when it closes", seen == [],
      f"handler still fired {len(seen)} times")
check("nothing of it is left behind",
      not scrim.winfo_exists() and not modal.winfo_exists())
check("the grab is released", not root.grab_current())
check("closing twice is harmless", modal.destroy() is None)

# ---- a modal over a modal --------------------------------------------------
first = A.FrameModal(root)
first.grab_set()
second = A.FrameModal(first)
second.grab_set()
root.update()
check("a second modal takes the grab",
      root.grab_current() is second.master.master)
second.destroy()
root.update()
check("and hands it back, so the first is still modal",
      root.grab_current() is first.master.master,
      f"grab is now {root.grab_current()}")
first.destroy()

# ---- a confirm opened OVER a modal ----------------------------------------
# dark_confirm is not a FrameModal -- it is the older in-frame overlay -- and it
# is opened BY the display editor. Releasing without restoring left nothing
# grabbing, so the editor underneath stopped being modal to the keyboard.
host = A.FrameModal(root)
host.grab_set()
host_scrim = host.master.master
root.update()
root.after(120, lambda: root.event_generate("<Escape>", when="now"))
A.dark_alert(root, "Heads up", "Something happened.")
root.update()
check("a confirm opened over a modal hands the grab back when it closes",
      root.grab_current() is host_scrim,
      f"grab is {root.grab_current()}, expected {host_scrim}")
host.destroy()

# ---- the caller must not hang ----------------------------------------------
done = []
waiter = A.FrameModal(root)
root.after(50, waiter.destroy)
root.after(4000, lambda: done.append("timeout"))
root.wait_window(waiter)
done.append("returned")
check("wait_window returns when the modal closes", done[0] == "returned",
      "wait_window did not return")

# ---- and no window source is left in the app -------------------------------
# Parsed, not grepped: the docstrings on dark_confirm/dark_alert name the native
# dialogs they replaced, and a text search cannot tell that from a call.
here = os.path.dirname(os.path.abspath(__file__))
BANNED = {"Toplevel", "messagebox", "filedialog", "simpledialog"}
# THE EXEMPTIONS, BY NAME, AND THERE ARE TWO SINCE 2026-08-29.
#
# A DIALOG IS BANNED. A SURFACE IS NOT, AND A SURFACE IS NOT A DIALOG. This is
# the distinction Doug drew on 2026-08-28: *"no pop out but we can replace
# surfaces and invoke entire new ones in the side."* The line is WHO PLACES IT.
# A dialog is an OS window that WINDOWS places, at a moment of its choosing, on
# a screen he was not looking at -- that is the 28 July complaint, verbatim:
# *"if i click into something i want it right there, this just popped up on
# another screen."* A surface is placed by this process, on a screen this
# process chose, in the same place every time, and it asks nothing.
#
# Almost every surface is therefore a Frame inside the app's window and needs no
# exemption at all -- it is registered in openspan.DOCK_SPEC and switched by
# App._render_dock, and a new one must never ask for a line here. The two below
# are the cases where what the thing HAS TO BE is something one window cannot
# contain, and in both the app still owns the placement to the pixel:
#
#   _identify_card (2026-08-15) puts a number on each real monitor for a
#     moment, the way Windows Settings does. It takes no focus, answers no
#     input, destroys itself -- and it cannot be drawn from inside the frame
#     because its whole point is to appear on the OTHER screens.
#
#   _build_dock_strip (2026-08-29) is the dock. Doug: *"The column doesn't need
#     to be that high for the sidebar, it needs to be just to Scripts and then
#     below it our new vertical dock."* The rail left the app's window that day
#     and became a strip on the Desktop screen's right edge, above the shell's
#     app-icons dock. It cannot be inside the app's window because its job is to
#     SHOW AND HIDE that window -- a dock drawn inside the thing it hides is
#     gone at exactly the moment it is needed. It is placed by
#     on_desktop.OnDesktop, the same controller and the same right edge the app
#     window uses, refusing every move that is not its own; nothing about it is
#     left to Windows. It is built once, at startup, and never opened again.
#
# _open_console_window was a third exemption for exactly one day (2026-08-27 to
# 2026-08-28). The console is a dock surface now -- law 10 asks only that a
# scroller not be inside another scroller on the same axis, and a sibling
# surface is inside nothing -- so the Toplevel went, and so did the exemption.
# Nothing about the rule changed; the layout stopped needing it. That is the
# shape a name on this list is meant to have: it comes off again.
#
# Each exemption lives in exactly one method, so it is exactly one method wide;
# a Toplevel anywhere else in these files is still an offender.
EXEMPT_FUNCTIONS = {"_identify_card", "_build_dock_strip"}
offenders = []
for name in ("openspan.py", "openspan_setup.py", "openspan_portal.py",
             "openspan_launcher.py"):
    with open(os.path.join(here, name), encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=name)
    exempt_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in EXEMPT_FUNCTIONS:
            exempt_lines.update(range(node.lineno, node.end_lineno + 1))
    for node in ast.walk(tree):
        hit = ((isinstance(node, ast.Attribute)
                and (node.attr in BANNED
                     or getattr(node.value, "id", None) in BANNED))
               or (isinstance(node, ast.Name) and node.id in BANNED))
        if hit and getattr(node, "lineno", 0) not in exempt_lines:
            offenders.append(f"{name}:{node.lineno}")
check("no dialog anywhere still opens an OS window",
      not offenders, ", ".join(offenders))
_app_source = open(os.path.join(here, "openspan.py"), encoding="utf-8").read()
_app_tree = ast.parse(_app_source)
_app_funcs = [n for n in ast.walk(_app_tree) if isinstance(n, ast.FunctionDef)]
for _exempt in sorted(EXEMPT_FUNCTIONS):
    check(f"the {_exempt} exemption exists and is one method wide",
          len([n for n in _app_funcs if n.name == _exempt]) == 1)
check("the exemption list is exactly these two -- the console still needs none",
      EXEMPT_FUNCTIONS == {"_identify_card", "_build_dock_strip"},
      repr(sorted(EXEMPT_FUNCTIONS)))
# AN EXEMPTION IS NOT A LICENCE. Each is one Toplevel, and the widening on
# 2026-08-29 has to be provably a widening by one: a method that quietly opened
# two windows would satisfy the name list and break the rule behind it.
_toplevels = [n for n in ast.walk(_app_tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "Toplevel"]
check("there are exactly as many Toplevel calls in the app as there are names "
      "on the list, so no exempt method opens a second window",
      len(_toplevels) == len(EXEMPT_FUNCTIONS), f"{len(_toplevels)} calls")
# ...and the dock is a surface by the test that decides it: THIS process places
# it, at a rect it computed, and refuses every move it did not make. A dialog is
# a window Windows places. Doug, 28 July: *"this just popped up on another
# screen."* Nothing about the strip can pop up anywhere.
_strip = next((n for n in _app_funcs if n.name == "_build_dock_strip"), None)
check("the dock strip exists and is built exactly once, at startup",
      _strip is not None
      and _app_source.count("self._build_dock_strip()") == 1)
if _strip is not None:
    _strip_stmts = (_strip.body[1:] if (_strip.body
                                        and isinstance(_strip.body[0], ast.Expr))
                    else _strip.body)
    _strip_src = "\n".join(ast.unparse(n) for n in _strip_stmts)
    check("the builder itself places nothing: no geometry, no overrideredirect, "
          "no topmost attribute -- on_desktop owns where it goes",
          not any(token in _strip_src for token in
                  ("geometry(", "overrideredirect", "-topmost", "attributes(")),
          _strip_src)
    check("...and it does not raise, focus or grab, because a dock that steals "
          "focus is a dialog wearing a dock's shape",
          not any(token in _strip_src for token in
                  ("focus_force", "grab_set", "lift(", "wait_window")),
          _strip_src)
check("the app window's placement is what shows and hides it, and the strip is "
      "not touched by that: one method withdraws or deiconifies the root",
      len([n for n in _app_funcs
           if "self.root.withdraw()" in ast.unparse(n)]) == 1
      and "_dock_place" in [n.name for n in _app_funcs
                            if "self.root.withdraw()" in ast.unparse(n)])
# THE WINDOW IS GONE, NOT MERELY UNUSED. A console Toplevel that still exists
# behind a dead call site is a pop-out waiting to be re-wired, so the two
# methods that owned it must be absent by name.
for _dead in ("_open_console_window", "_close_console_window"):
    check(f"{_dead} no longer exists anywhere in the app",
          not [n for n in _app_funcs if n.name == _dead]
          and _dead not in _app_source)
# ...and what replaced them is a surface: a Frame in the surface region, built
# on first invoke, with no window-manager call anywhere in it.
_mount = next((n for n in _app_funcs if n.name == "_console_mount"), None)
check("the console is built by _console_mount instead", _mount is not None)
if _mount is not None:
    _stmts = (_mount.body[1:] if (_mount.body
                                  and isinstance(_mount.body[0], ast.Expr))
              else _mount.body)
    _mount_src = "\n".join(ast.unparse(n) for n in _stmts)
    check("the console surface makes no window-manager call at all",
          not any(token in _mount_src for token in
                  ("Toplevel", "protocol(", "geometry(", "iconbitmap",
                   "deiconify", "focus_force", "overrideredirect")),
          _mount_src)
    check("it builds into the surface registered in the dock",
          "self._dock_surfaces" in _mount_src, _mount_src)

root.destroy()
print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
