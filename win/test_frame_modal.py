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
check("a requested size sizes the card and fits the window",
      card.winfo_reqwidth() <= root.winfo_width()
      and 0 < int(card.place_info().get("width") or 0) <= 900,
      f"card asked for {card.place_info().get('width')} in a "
      f"{root.winfo_width()}px window")
check("a screen position is discarded rather than obeyed",
      modal.geometry("+400+120") == "" and modal.geometry() == "")

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
offenders = []
for name in ("openspan.py", "openspan_setup.py", "openspan_portal.py",
             "openspan_launcher.py"):
    with open(os.path.join(here, name), encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=name)
    for node in ast.walk(tree):
        hit = ((isinstance(node, ast.Attribute)
                and (node.attr in BANNED
                     or getattr(node.value, "id", None) in BANNED))
               or (isinstance(node, ast.Name) and node.id in BANNED))
        if hit:
            offenders.append(f"{name}:{node.lineno}")
check("no dialog anywhere still opens an OS window",
      not offenders, ", ".join(offenders))

root.destroy()
print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
