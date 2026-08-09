"""A stopped portal and an unconnected device are not the same fact, and a
button that is working must say so.

Three complaints landed in the same two functions, which is why they are tested
together here: _build_device_row writes the device card and _apply_device_rows
paints it, and all three changes are edits to that pair.

---------------------------------------------------------------------------
1. THE COLOUR BUG, LITERALLY
---------------------------------------------------------------------------
Doug: *"currently there is no visual difference between a stopped portal and a
paired but unconnected device. I don't like that."*

He was reading the source correctly. _apply_device_rows contained:

        elif live:
            colour, text = WARN, "portal off"
        elif paired:
            colour, text = WARN, "paired"

Two different states, the same amber, and the same again in the indicator row.
The distinction was never drawn anywhere in the file.

The principle now implemented: a stopped portal is a GLOBAL CAUSE, an
unconnected device is a LOCAL STATE, and they are rendered in different
REGISTERS. While the portal is down nothing in the device area uses a
full-strength alarm colour -- everything drops to ACCENT_SUPPRESSED /
WARN_SUPPRESSED -- and the only full-strength amber left in the window is the
Start portal button, which is the thing that fixes it.

**Section (a) below FAILS against the pre-W3 source.** `device_state_colour`
did not exist, and the two states it separates were both literally `WARN`. That
is the point of the assertion: it is the bug, written down as a check.

---------------------------------------------------------------------------
2. THE PENDING STATE, AND WHY IT COULD NOT SIMPLY BE PAINTED ON
---------------------------------------------------------------------------
Doug: *"when i click a button i need visual indication it has been clicked on
the button itself. it needs to react in some way, even in a pending state while
the action runs."*

The pressed state landed earlier and covers the instant of the click. This is
the wait after it. For most buttons a helper is enough -- App.busy() parks the
resting label, shows a present participle, and restores it in a finally.

The four per-device verbs could not use that, and this is the whole reason the
pending state had to wait for the wave that rewrote these two functions:
_apply_device_rows re-derives all four verbs from _dev_state on every 3-second
poll tick, so a busy label painted onto the widget is stomped within three
seconds. The in-flight fact therefore lives in _dev_state alongside "paired" and
"broadcasting", and _apply_device_rows renders it -- one writer, one source of
truth. Section (e) is that claim: a tick must not clear a busy button.

---------------------------------------------------------------------------
3. THE FAILURE MODE THIS FILE EXISTS TO CATCH
---------------------------------------------------------------------------
_apply_device_rows indexes row["buttons"]["pair"|...] and _build_device_row
writes that dict, three thousand lines apart. When they disagree the failure is
SILENT and does not look like a crash: _poll marshals through ui(), _drain_ui
swallows every exception, so a KeyError aborts _apply_poll mid-function and the
status dots, the readiness banner and the headphones line simply freeze with
nothing in the console and no traceback anywhere. Section (c) drives both sides
off the same two module constants and checks the source for a subscript that is
not in them.

---------------------------------------------------------------------------
WHAT THIS TEST CAN SEE
---------------------------------------------------------------------------
App(root) starts the VM and the audio workers, so it is never constructed here.
The device panel is driven instead through App.__new__ with only the attributes
these two methods actually touch -- which is itself a useful claim, since it
pins how little of the app the device panel is entitled to reach into. The
structural claims (menu master, deferred commands, which keys are indexed) are
read out of the source with ast, because structure is what ast settles.

WHAT IT COULD NOT SEE, AND WHY THAT MATTERED
Section (b) originally walked self._dev_body and nothing else. That scope is
exactly why the colour bug shipped GREEN: the arrangement canvas -- the largest
element in the window -- is not in _dev_body, the indicator row is not in
_dev_body, and a ttk.Button has no -background, so the cget raises TclError and
every verb on every card was silently skipped. The canvas was being handed
`live and portal_on`, with the portal folded into liveness before it could tell
the two states apart, and it painted full-strength WARN for a stopped portal
directly above a card that said the opposite. Every check here passed.

So (b2) drives the REAL MultiArrangeCanvas state methods on the exact arguments
the REAL renderer sends them, (b3) drives the indicator row's own pure token
functions, and both compute what they expect from device_state_colour rather
than from a second table that could drift the same way. _apply_poll itself is
still never called: one of its branches calls toggle_portal(), which would
spawn a real portal process on the machine running the tests.

No Tk window is shown: the root is withdrawn. Nothing here touches a running
OpenSpan, the live config, the VM, or any radio.

Exit 0 = all pass.
"""
import ast
import inspect
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import openspan as A  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (
        "" if cond or not detail else "\n      " + detail))
    if not cond:
        fails.append(name)


with open(os.path.join(HERE, "openspan.py"), encoding="utf-8") as handle:
    SOURCE = handle.read()
MODULE = ast.parse(SOURCE, filename="openspan.py")


def _method(class_name, method_name):
    for node in ast.walk(MODULE):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if (isinstance(item, ast.FunctionDef)
                        and item.name == method_name):
                    return item
    return None


def _function(name):
    """A module-level def, for the pure functions the renderers are built on."""
    for node in MODULE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _dotted(node):
    """`self.root` -> "self.root"; anything that is not a plain name path -> None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return None if base is None else base + "." + node.attr
    return None


# ===========================================================================
# (a) the colour truth table, as a pure function
#
# All five rows. The row that matters most is the pair in the middle: before
# this change they were the same constant.
# ===========================================================================
print("\n---- (a) the truth table ----")

check("device_state_colour exists as a module-level pure function",
      callable(getattr(A, "device_state_colour", None)))

TABLE = [
    # (portal_on, connected, paired) -> (colour, text-contains)
    ((True, True, True), A.ACCENT, "connected"),
    ((True, False, True), A.WARN, "paired"),
    ((False, True, True), A.ACCENT_SUPPRESSED, "connected"),
    ((False, False, True), A.WARN_SUPPRESSED, "paired"),
    ((True, False, False), A.MUTED, "not paired"),
]
for args, want_colour, want_text in TABLE:
    colour, text = A.device_state_colour(*args)
    check(f"portal_on={args[0]}, connected={args[1]}, paired={args[2]}"
          f" -> {want_colour}",
          colour == want_colour and want_text in text,
          f"got {colour!r}, {text!r}")

check("an unpaired device is MUTED whether the portal is up or not",
      A.device_state_colour(False, False, False)
      == A.device_state_colour(True, False, False) == (A.MUTED, "not paired"))

# THE BUG. Against the pre-W3 source both of these were literally WARN.
off_connected = A.device_state_colour(False, True, True)[0]
on_paired = A.device_state_colour(True, False, True)[0]
off_paired = A.device_state_colour(False, False, True)[0]
check("a stopped portal and a paired-but-unconnected device are NOT the same "
      "colour  <-- fails against the pre-W3 code",
      off_connected != on_paired,
      f"portal-off-connected {off_connected} == paired-idle {on_paired}")
check("...and neither is a suppressed 'paired' the same as a live 'paired'",
      off_paired != on_paired, f"{off_paired} == {on_paired}")

# The suppressed pair has to stay readable AND stay chromatic: rendered in
# MUTED they would say "dead", and a bonded device waiting on a stopped portal
# is not dead.
for const in ("ACCENT_SUPPRESSED", "WARN_SUPPRESSED"):
    value = getattr(A, const, None)
    check(f"{const} is a colour", isinstance(value, str)
          and value.startswith("#") and len(value) == 7, repr(value))
    check(f"{const} is distinguishable from MUTED (not just 'grey')",
          value != A.MUTED
          and max(abs(int(value[i:i + 2], 16) - int(A.MUTED[i:i + 2], 16))
                  for i in (1, 3, 5)) >= 24,
          f"{value} vs MUTED {A.MUTED}")
    check(f"{const} is visibly weaker than its full-strength twin",
          value != A.ACCENT and value != A.WARN)


# ===========================================================================
# The device panel, driven without constructing App.
# ===========================================================================
class FakeCanvas:
    """Only what _apply_device_rows and _build_device_row actually reach for."""

    def __init__(self, devices):
        self.config = {"devices": devices}
        self.target_state = {}

    def devices(self):
        return self.config["devices"]

    def set_target_state(self, device_id, live, paired, portal_on=True):
        # Records what the renderer HANDED the canvas, portal state included.
        # Section (b2) then feeds those exact arguments into the real
        # MultiArrangeCanvas methods, so nothing here is a second opinion about
        # what the canvas would have drawn.
        self.target_state[device_id] = (live, paired, portal_on)


class FakeBt:
    _radios = []          # no radio inventory -> radio_missing is never True


class CanvasProbe:
    """The smallest object the REAL MultiArrangeCanvas state methods need.

    set_target_state and _colors reach for target_states, ipad_state and
    redraw() and nothing else, so the shipped methods can be driven unbound
    against this -- no Tk canvas, no config, no monitors, and no stand-in
    reimplementation of the thing under test.
    """

    def __init__(self):
        self.target_states = {}
        self.ipad_state = "off"
        self.redraws = 0

    def redraw(self):
        self.redraws += 1


def canvas_colours(live, paired, portal_on=True):
    """(fill, outline, label) the real canvas would paint for one device."""
    probe = CanvasProbe()
    A.MultiArrangeCanvas.set_target_state(probe, "d1", live, paired, portal_on)
    return A.MultiArrangeCanvas._colors(probe, ("target", "d1"))


def make_devices():
    return [
        {"id": "d1", "name": "Tablet", "port": 7810,
         "radio": "AA:BB:CC:00:00:01", "enabled": True, "displays": []},
        {"id": "d2", "name": "Studio", "port": 7811,
         "radio": "AA:BB:CC:00:00:02", "enabled": True, "displays": []},
        {"id": "d3", "name": "Spare", "port": 7812,
         "radio": "", "enabled": True, "displays": []},
    ]


def make_app(root, body):
    app = A.App.__new__(A.App)          # never App(root): that starts the VM
    app.root = root
    app._dev_body = body
    app._dev_rows = {}
    app._dev_states = {}
    app._dev_status = {}
    app._vm_reachable = True
    app._ui_thread = threading.get_ident()
    app.portal_proc = None
    app.canvas = FakeCanvas(make_devices())
    app.bt_panel = FakeBt()
    app._card_menu = tk.Menu(root, tearoff=0)
    return app


def enabled(button):
    return "disabled" not in button.state()


root = tk.Tk()
root.withdraw()                 # never draw on the desk this is run from
A._theme_startup_buttons()
style = ttk.Style()
# THE APP'S padding, not the startup window's. _theme_startup_buttons uses
# padding=8 and App._theme uses padding=(10, 3) -- deliberately, and the
# difference is 10px of button height per stacked row. Measuring a device card
# under the startup padding overstates it by a third, and this file makes
# height claims.
style.configure("TButton", background=A.CARD, foreground=A.FG,
                bordercolor=A.CARD, focuscolor=A.CARD, relief="flat",
                padding=(10, 3), font=("Segoe UI", 10))
# the style the portal button wears while the portal is down; _theme sets it on
# the real app, and this test needs it to exist before any button uses it
style.configure("Warn.TButton", background=A.WARN, foreground="#2a2205")
style.map("Warn.TButton",
          foreground=[("disabled", "#6E687A")],
          background=[("disabled", A.PANEL), ("pressed", A.PRESS_WARN),
                      ("active", "#f8d276")])

body = tk.Frame(root, bg=A.BG)
body.pack()
app = make_app(root, body)
for device in app.canvas.devices():
    app._build_device_row(device)


# ===========================================================================
# (c) the two sides of the row dict cannot drift
# ===========================================================================
print("\n---- (c) the row dict, from one shared key list ----")

check("DEVICE_ROW_KEYS and DEVICE_VERBS are module constants",
      isinstance(getattr(A, "DEVICE_ROW_KEYS", None), tuple)
      and isinstance(getattr(A, "DEVICE_VERBS", None), tuple))
check("DEVICE_VERBS is derived from DEVICE_VERB_SPEC, not repeated",
      A.DEVICE_VERBS == tuple(k for k, _l, _b in A.DEVICE_VERB_SPEC))
check("the four connection verbs are still four, and still those four",
      A.DEVICE_VERBS == ("pair", "connect", "disconnect", "unpair"),
      str(A.DEVICE_VERBS))

# On its own throwaway app and container: _build_device_row packs into
# self._dev_body and writes self._dev_rows, so probing with the real one would
# leave a fourth card in the panel the height checks below count.
_probe_app = make_app(root, tk.Frame(root, bg=A.BG))
built = _probe_app._build_device_row(_probe_app.canvas.devices()[0])
check("_build_device_row RETURNS the row dict", isinstance(built, dict))
check("the returned row carries exactly DEVICE_ROW_KEYS",
      set(built) == set(A.DEVICE_ROW_KEYS),
      f"{sorted(built)} vs {sorted(A.DEVICE_ROW_KEYS)}")
check("the row's buttons carry exactly DEVICE_VERBS",
      set(built["buttons"]) == set(A.DEVICE_VERBS),
      str(sorted(built["buttons"])))
check("...and it is also stored, so the poll can find it",
      _probe_app._dev_rows["d1"] is built)

# Now the source side: every literal subscript _apply_device_rows takes on a row
# or on its buttons must be a key that exists. This is the KeyError that would
# freeze _apply_poll with no traceback anywhere.
APPLY = _method("App", "_apply_device_rows")
check("_apply_device_rows found in the source", APPLY is not None)
row_subs, button_subs = set(), set()
for node in ast.walk(APPLY):
    if not (isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)):
        continue
    who = _dotted(node.value)
    if who == "row":
        row_subs.add(node.slice.value)
    elif who == "buttons":
        button_subs.add(node.slice.value)
check("_apply_device_rows indexes the row at all", bool(row_subs), str(row_subs))
check("every row[...] it indexes is in DEVICE_ROW_KEYS",
      row_subs <= set(A.DEVICE_ROW_KEYS),
      f"stray: {sorted(row_subs - set(A.DEVICE_ROW_KEYS))}")

# The same walk finds NOTHING on `buttons`, and that is the point: the code
# indexes it with the DEVICE_VERB_SPEC loop variable, so there is no string
# constant to check and the old `button_subs <= DEVICE_VERBS` assertion was
# vacuously true against an empty set. Binding the two sides therefore has to
# happen at RUN time -- swap in a dict that records every key the poll asks for.
check("_apply_device_rows takes no literal subscript on `buttons` (it uses the "
      "loop variable, which is why the run-time check below exists)",
      not button_subs, f"unexpected literals: {sorted(button_subs)}")


class RecordingButtons(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen = set()

    def __getitem__(self, key):
        self.seen.add(key)
        return super().__getitem__(key)


_recorder = RecordingButtons(app._dev_rows["d1"]["buttons"])
app._dev_rows["d1"]["buttons"] = _recorder
app._apply_device_rows(True)
app._dev_rows["d1"]["buttons"] = dict(_recorder)
check("_apply_device_rows consumes EXACTLY the keys _build_device_row wrote",
      _recorder.seen == set(A.DEVICE_VERBS),
      f"consumed {sorted(_recorder.seen)} vs DEVICE_VERBS "
      f"{sorted(A.DEVICE_VERBS)}")

# The other two verb-keyed tables. Both used to be hand-written four-key dict
# LITERALS indexed by that same loop variable -- a fifth verb in the spec was a
# KeyError raised inside the poll, and _drain_ui swallows it.
check("DEVICE_VERB_GATES and DEVICE_VERB_HANDLERS cover exactly DEVICE_VERBS",
      set(A.DEVICE_VERB_GATES) == set(A.DEVICE_VERB_HANDLERS)
      == set(A.DEVICE_VERBS),
      f"gates {sorted(A.DEVICE_VERB_GATES)} / "
      f"handlers {sorted(A.DEVICE_VERB_HANDLERS)}")
_loud = ""
try:
    A._require_verb_coverage({"pair": 1}, "a deliberately short table")
except KeyError as exc:                       # noqa: BLE001
    _loud = str(exc)
check("a verb table that misses a verb fails LOUDLY at import, naming what is "
      "missing -- instead of a KeyError _drain_ui swallows mid-poll",
      "connect" in _loud and "unpair" in _loud, repr(_loud))
check("every App method DEVICE_VERB_HANDLERS names actually exists",
      all(callable(getattr(A.App, name, None))
          for name in A.DEVICE_VERB_HANDLERS.values()),
      str(sorted(A.DEVICE_VERB_HANDLERS.values())))

BUILD = _method("App", "_build_device_row")
build_src = ast.get_source_segment(SOURCE, BUILD) or ""
check("_build_device_row builds the row dict OFF the constant, not off literals",
      "DEVICE_ROW_KEYS" in build_src and "DEVICE_VERB_SPEC" in build_src)
check("the card's command table is built off DEVICE_VERB_HANDLERS, not written "
      "out again",
      "DEVICE_VERB_HANDLERS" in build_src
      and "self._pair_device," not in build_src,
      "a four-key literal here drifts silently from DEVICE_VERB_SPEC")
# The gate moved behind device_verb_offer when the arrangement canvas grew a
# second surface for these same four verbs. It is bound by AST rather than by
# substring now, and deliberately: the poll's source still MENTIONS
# DEVICE_VERB_GATES in a comment, so a substring test would pass over a poll
# that had gone back to a hand-written literal. test_device_verbs.py holds the
# other half -- that device_verb_offer is the only code in the file that reads
# the gate table at all.
check("the verb gate is device_verb_offer -- one call, not a literal inside "
      "the poll",
      any(isinstance(node, ast.Call)
          and _dotted(node.func) == "device_verb_offer"
          for node in ast.walk(APPLY)))
check("...and the poll builds no gate predicate of its own",
      not [node for node in ast.walk(APPLY) if isinstance(node, ast.Lambda)],
      "a lambda in the poll is a second opinion about which verbs are live")
check("the poll reads its facts from the shared producer, not from _dev_state "
      "directly -- the canvas menu reads the same one",
      any(isinstance(node, ast.Call)
          and _dotted(node.func) == "self._device_verb_facts"
          for node in ast.walk(APPLY)))


# ===========================================================================
# (A) the collapse: five permanently-enabled buttons are gone from the card
# ===========================================================================
print("\n---- (A) the card collapsed to one row ----")

for gone in ("Radio…", "Input…", "Rename", "Displays…", "Remove"):
    check(f"the card no longer builds a “{gone}” button",
          f'text="{gone}"' not in build_src, "still in _build_device_row")
check("the four verbs are still built as real, gated buttons",
      "button.state([\"disabled\"])" in build_src,
      "the verbs must stay VISIBLE buttons, not menu entries")
# The card gained a “⋯” button when the five editors moved to a right-click:
# a right-click advertises itself to nobody, and a one-row card has no space
# for the sentence BtPanel uses. It is a second ttk.Button in the source, so
# the old `count("ttk.Button") == 1` no longer says what it meant.
#
# What it MEANT is that no per-object EDITOR is a permanent button -- which is
# the loop above, per editor by name. So the count is replaced by the claim it
# was standing in for: exactly one button in this method is built from the verb
# spec, and exactly one is the menu affordance.
check("exactly one button in the card is built from the verb spec",
      build_src.count("for key, resting, _in_flight in") == 1, build_src[:0])
check("...and the ⋯ affordance opens the SAME menu as the right-click, "
      "rather than being a second path to the editors",
      "_card_menu_from_button" in build_src
      and "_post_card_menu" in inspect.getsource(A.App._card_menu_from_button)
      and "_post_card_menu" in inspect.getsource(A.App._device_card_menu),
      "the ⋯ button and Button-3 must share one poster")

card_widgets = body.winfo_children()
check("one card frame per device", len(card_widgets) == 3, str(card_widgets))
buttons_per_card = [
    len([w for w in card.winfo_children() if isinstance(w, ttk.Button)])
    for card in card_widgets]
# Four verbs plus the ⋯ affordance. It was nine: four verbs and five per-object
# editors that were permanently enabled whether or not the device even had a
# radio. The five are gone as buttons; the fifth control here is the hint that
# they exist, not one of them.
check("each card carries the four verbs plus ⋯, not the old nine",
      buttons_per_card == [5, 5, 5], str(buttons_per_card))
check("...and exactly one of them is the ⋯ affordance",
      all(len([w for w in card.winfo_children()
               if isinstance(w, ttk.Button) and w.cget("text") == "⋯"]) == 1
          for card in card_widgets),
      str([[w.cget("text") for w in c.winfo_children()
            if isinstance(w, ttk.Button)] for c in card_widgets]))
check("each card is ONE row -- no nested verbs frame",
      all(not [w for w in card.winfo_children() if isinstance(w, tk.Frame)]
          for card in card_widgets))

# 66px is a RECORDED HISTORICAL MEASUREMENT of the pre-W3 two-row card -- two
# stacked button rows at padding=(10, 3), plus its pady -- taken while that card
# still existed. It is NOT measured here and cannot be: the shape it describes
# is no longer in the tree, so nothing this file can build would re-derive it.
# Every claim below that uses it says "against the recorded 66px", never
# "measured": the saving is a comparison with a number on the record, and
# stating that plainly is cheaper than a fragile reach into git history.
RECORDED_OLD_CARD_H = 66

root.update_idletasks()
card_h = card_widgets[0].winfo_reqheight() + A.PAD_XS + A.PAD_XS
check("a card is about one button tall (~33px), not two rows (~66px)",
      card_h <= 40, f"measured {card_h}px including its pady")
check("the three cards give back at least 80px of window height, against the "
      "RECORDED (not re-measured) 66px two-row card",
      (RECORDED_OLD_CARD_H - card_h) * 3 >= 80,
      f"saved {(RECORDED_OLD_CARD_H - card_h) * 3}px against the recorded "
      f"{RECORDED_OLD_CARD_H}px -- a historical measurement, not one taken here")

# Width is the risk the collapse introduces: four verbs now share the row with
# the labels. It must not ask for MORE than the shape it replaces (a head of
# five property buttons plus those same labels), or a card that fits today
# stops fitting.
probe = tk.Frame(root, bg=A.BG)
old_w = 0
for text in ("Radio…", "Input…", "Rename", "Displays…", "Remove"):
    old_w += ttk.Button(probe, text=text).winfo_reqwidth() + 4
row0 = app._dev_rows["d1"]
labels_w = sum(row0[k].winfo_reqwidth() for k in ("dot", "name", "radio")) + 12
new_w = sum(row0["buttons"][k].winfo_reqwidth() + A.PAD_SM
            for k in A.DEVICE_VERBS)
check("the four verbs ask for LESS width than the five buttons they replace",
      new_w < old_w, f"verbs {new_w}px vs old head buttons {old_w}px")
# The previous version of this line added labels_w to BOTH sides of the
# comparison above, which carries no information at all. The real claim is
# about the card that actually got built: measure it, and compare it with the
# shape it replaced (those same labels plus the five property buttons).
old_card_w = old_w + labels_w
card_w = card_widgets[0].winfo_reqwidth()
check("the whole one-row card, as BUILT, asks for less width than the shape it "
      "replaced",
      card_w < old_card_w,
      f"the built card measures {card_w}px; head row + labels was "
      f"{old_card_w}px")
probe.destroy()


# ===========================================================================
# (d) the gate: all four device states, against the predicate as written
# ===========================================================================
print("\n---- (d) which verbs are live, in every state ----")


def drive(paired=False, live=False, inflight=False, broadcasting=False,
          up=True, verb="", portal_on=True, device_id="d1"):
    state = app._dev_state(device_id)
    state["paired"] = paired
    state["inflight"] = inflight
    state["broadcasting"] = broadcasting
    state["verb"] = verb
    app._dev_status = {
        d["id"]: ({"kbd_subscribed": live} if d["id"] == device_id
                  else {"kbd_subscribed": False})
        for d in app.canvas.devices() if up or d["id"] != device_id
    }
    app._apply_device_rows(portal_on)
    return {key: enabled(app._dev_rows[device_id]["buttons"][key])
            for key in A.DEVICE_VERBS}


# The predicate, restated independently. usable=True and up=True throughout
# (d1 has a radio and no radio inventory contradicts it).
CASES = {
    "unpaired":    dict(paired=False, live=False, inflight=False),
    "paired-idle": dict(paired=True, live=False, inflight=False),
    "live":        dict(paired=True, live=True, inflight=False),
    "busy":        dict(paired=True, live=False, inflight=True),
}
EXPECTED = {
    # pair: usable and vm_reachable and not busy and not live
    # connect: usable and up and not busy and paired and not live
    # disconnect: live or busy
    # unpair: usable and up and not busy and paired
    "unpaired":    {"pair": True, "connect": False,
                    "disconnect": False, "unpair": False},
    "paired-idle": {"pair": True, "connect": True,
                    "disconnect": False, "unpair": True},
    "live":        {"pair": False, "connect": False,
                    "disconnect": True, "unpair": True},
    "busy":        {"pair": False, "connect": False,
                    "disconnect": True, "unpair": False},
}
for name, kwargs in CASES.items():
    got = drive(**kwargs)
    check(f"state “{name}”: the enabled set matches the predicate exactly",
          got == EXPECTED[name], f"got {got}\n      want {EXPECTED[name]}")

# THE reason a single relabelling button was refused: there is no single
# correct verb in either of the two states this app spends its life in.
#
# The exact counts, because a wrong number is a wrong argument. The W3 brief
# said "TWO live verbs in both the paired-idle and live states"; against the
# predicate as written it is THREE in paired-idle (Pair, Connect, Unpair -- Pair
# is gated only on the VM answering, since pairing is what creates the lane) and
# two in live (Disconnect, Unpair). The conclusion is unchanged and stronger:
# there is no single correct verb, and a button that re-aimed under the cursor
# every three seconds would have Unpair in the rotation both times.
LIVE_VERB_COUNT = {"paired-idle": 3, "live": 2}
for name, want in LIVE_VERB_COUNT.items():
    live_verbs = [k for k, v in EXPECTED[name].items() if v]
    check(f"state “{name}” offers {want} verbs at once, so no single button "
          f"could replace them",
          len(live_verbs) == want and want > 1, str(live_verbs))

check("a device with no radio can never be paired, whatever else is true",
      not drive(paired=True, live=False, device_id="d3")["pair"])


# ===========================================================================
# (e) the busy presentation, and that a poll tick does not clear it
# ===========================================================================
print("\n---- (e) the pending state survives the 3-second tick ----")

BUSY_LABELS = {k: b for k, _l, b in A.DEVICE_VERB_SPEC}
for verb in A.DEVICE_VERBS:
    inflight = verb in ("pair", "connect")
    drive(paired=True, live=(verb == "disconnect"), inflight=inflight,
          verb=verb)
    button = app._dev_rows["d1"]["buttons"][verb]
    check(f"“{verb}” in flight shows its present participle "
          f"({BUSY_LABELS[verb]!r})",
          button.cget("text") == BUSY_LABELS[verb], repr(button.cget("text")))
    check(f"“{verb}” in flight is disabled -- the work is already running",
          not enabled(button))
    # THE poll tick. Nothing changes but the clock.
    app._apply_device_rows(True)
    check(f"a poll tick does NOT clear the “{verb}” busy state",
          app._dev_rows["d1"]["buttons"][verb].cget("text") == BUSY_LABELS[verb]
          and not enabled(app._dev_rows["d1"]["buttons"][verb]),
          repr(app._dev_rows["d1"]["buttons"][verb].cget("text")))

# The other verbs keep their resting labels while one is in flight.
drive(paired=True, inflight=True, verb="pair")
check("only the verb that is working says it is working",
      all(app._dev_rows["d1"]["buttons"][k].cget("text")
          == dict((key, label) for key, label, _b in A.DEVICE_VERB_SPEC)[k]
          for k in A.DEVICE_VERBS if k != "pair"))

# Self-healing, and deliberately only for pair/connect: _pair_device_worker
# clears inflight down half a dozen failure paths, and threading a verb-clear
# through every one of them is how one gets missed and a button says "Pairing…"
# forever. disconnect/unpair clear their own in a finally.
drive(paired=True, inflight=False, verb="pair")
check("a pair whose flight is over stops claiming to be in flight",
      app._dev_state("d1")["verb"] == ""
      and app._dev_rows["d1"]["buttons"]["pair"].cget("text") == "Pair")
drive(paired=True, inflight=False, verb="unpair")
check("an unpair is NOT self-healed away -- it clears its own in a finally",
      app._dev_state("d1")["verb"] == "unpair"
      and app._dev_rows["d1"]["buttons"]["unpair"].cget("text") == "Unpairing…")
app._dev_state("d1")["verb"] = ""

check("busy() exists on App and takes (button, label)",
      callable(getattr(A.App, "busy", None))
      and [a.arg for a in _method("App", "busy").args.args]
      == ["self", "button", "label"])
check("busy() marshals to the Tk thread rather than touching Tk from a worker",
      "_on_ui" in (ast.get_source_segment(SOURCE, _method("App", "busy")) or ""))

# The helper contract itself, on a real widget.
probe_btn = ttk.Button(root, text="Repair radios")
check("a fresh button is not busy", not A.button_is_busy(probe_btn))
A.set_button_busy(probe_btn, "Repairing radios…")
check("set_button_busy shows the participle and disables",
      probe_btn.cget("text") == "Repairing radios…"
      and "disabled" in probe_btn.state())
check("button_is_busy sees it -- this is what stops the 3s tick painting over",
      A.button_is_busy(probe_btn))
check("rebase_button_busy changes what it will be restored TO, not what it "
      "shows now",
      A.rebase_button_busy(probe_btn, "Repair 2 radios")
      and probe_btn.cget("text") == "Repairing radios…")
A.clear_button_busy(probe_btn)
check("clear_button_busy restores the rebased label and re-enables",
      probe_btn.cget("text") == "Repair 2 radios"
      and "disabled" not in probe_btn.state())
check("clearing twice is harmless", A.clear_button_busy(probe_btn) is None)
probe_btn.destroy()

# Every long threaded action a user waits on now says so.
for method, button in (("stop_vm", '_sysbtn["Stop VM"]'),
                       ("cold_restart_vm", '_sysbtn["Cold-restart VM"]'),
                       ("restart_keyboard", '_sysbtn["Restart keyboard"]'),
                       ("restart_everything", "button"),
                       ("toggle_vm", "vm_btn")):
    src = ast.get_source_segment(SOURCE, _method("App", method)) or ""
    check(f"{method} reports its wait on the button",
          "self.busy(" in src, "no busy() call")
    check(f"{method} restores the button in a finally, not on the happy path",
          "finally:" in src and "done()" in src)
src = ast.get_source_segment(SOURCE, _method("BtPanel", "_reclaim_radios")) or ""
check("Repair radios reports its wait too (it was a bare state='disabled')",
      "busy(" in src and "Repairing" in src)


# ===========================================================================
# (b) while the portal is off, no full-strength alarm in the device area
# ===========================================================================
print("\n---- (b) the alarm sits at the cause, not on every card ----")

app._dev_states.clear()
app._dev_state("d1")["paired"] = True
app._dev_state("d2")["paired"] = True
app._dev_status = {"d1": {"kbd_subscribed": True},
                   "d2": {"kbd_subscribed": False},
                   "d3": {"kbd_subscribed": False}}
app._apply_device_rows(False)          # <- the portal is DOWN


def every_widget(parent):
    for child in parent.winfo_children():
        yield child
        yield from every_widget(child)


warned = []
for widget in every_widget(body):
    for option in ("fg", "foreground", "bg", "background"):
        try:
            value = str(widget.cget(option))
        except tk.TclError:
            continue
        if value == A.WARN:
            warned.append(f"{widget} {option}={value}")
check("NO widget in the device area wears full-strength WARN while the portal "
      "is off", not warned, "\n      ".join(warned))
# The sweep above is BLIND to ttk widgets: ttk.Button has no -background, the
# cget raises TclError and every verb on every card is silently skipped. The
# alarm reaches a ttk button by STYLE, so that is what has to be asked.
check("no device-row button wears the alarm style either -- the amber lives on "
      "the portal button alone",
      all(button.cget("style") != "Warn.TButton"
          for row in app._dev_rows.values()
          for button in row["buttons"].values()),
      "a card verb is wearing Warn.TButton")
check("a connected device reads as suppressed green, not as an alarm",
      app._dev_rows["d1"]["dot"].cget("fg") == A.ACCENT_SUPPRESSED,
      app._dev_rows["d1"]["dot"].cget("fg"))
check("a paired-but-idle device reads as suppressed amber",
      app._dev_rows["d2"]["dot"].cget("fg") == A.WARN_SUPPRESSED,
      app._dev_rows["d2"]["dot"].cget("fg"))
check("the cards SAY the portal is the reason, not just show it",
      A.PORTAL_OFF_SUFFIX in app._dev_rows["d1"]["name"].cget("text")
      and A.PORTAL_OFF_SUFFIX in app._dev_rows["d2"]["name"].cget("text"))

# ...and with the portal up the same two devices go back to full strength.
app._apply_device_rows(True)
check("portal up: a connected device is full ACCENT again",
      app._dev_rows["d1"]["dot"].cget("fg") == A.ACCENT)
check("portal up: a paired-but-idle device is full WARN again",
      app._dev_rows["d2"]["dot"].cget("fg") == A.WARN)


# ===========================================================================
# (b2) THE SCOPE THAT LET F1 SHIP GREEN
#
# The sweep above walks self._dev_body and nothing else, so it can see the
# card's dot and cannot see the arrangement canvas -- the LARGEST element in
# the window -- or the indicator row. The canvas kept a private fill/outline
# table, was handed `live and portal_on` (portal folded into liveness before it
# could tell the two states apart) and painted full-strength WARN for a stopped
# portal, directly contradicting the card three inches below it. Every check in
# this file passed.
#
# So this section drives the REAL renderer and the REAL canvas methods across
# the whole truth table, and computes what it expects from device_state_colour
# rather than from a hand-built table that could drift the same way.
# ===========================================================================
print("\n---- (b2) the canvas is the same truth table, at 400x300px ----")


def paint(portal_on, connected, paired, device_id="d1"):
    """Drive _apply_device_rows for real; return (dot colour, canvas args)."""
    state = app._dev_state(device_id)
    state["paired"] = paired
    state["inflight"] = False
    state["broadcasting"] = False
    state["verb"] = ""
    app._dev_status = {
        d["id"]: {"kbd_subscribed": bool(connected) if d["id"] == device_id
                  else False}
        for d in app.canvas.devices()}
    app._apply_device_rows(portal_on)
    return (app._dev_rows[device_id]["dot"].cget("fg"),
            app.canvas.target_state[device_id])


for _portal_on, _connected, _paired in (
        (True, True, True), (True, False, True), (True, False, False),
        (False, True, True), (False, False, True), (False, False, False)):
    _label = (f"portal={'ON ' if _portal_on else 'off'} "
              f"connected={_connected} paired={_paired}")
    want_colour, _want_text = A.device_state_colour(
        _portal_on, _connected, _paired)
    dot, sent = paint(_portal_on, _connected, _paired)

    check(f"{_label}: the CARD's dot is exactly device_state_colour's colour",
          dot == want_colour, f"painted {dot}, function says {want_colour}")
    check(f"{_label}: the canvas is handed the portal state as its OWN "
          f"argument, not folded into liveness",
          sent == (_connected, _paired, _portal_on),
          f"renderer sent {sent}, expected "
          f"{(_connected, _paired, _portal_on)}")

    # ...and now the shipped canvas methods, on the exact arguments the shipped
    # renderer just sent them.
    fill, line, _text = canvas_colours(*sent)
    want_line = A.IPAD_OFF_LINE if want_colour == A.MUTED else want_colour
    check(f"{_label}: the canvas box outline IS the card's dot colour",
          line == want_line, f"box {line} vs dot {dot}")
    check(f"{_label}: no full-strength alarm on the canvas while the portal is "
          f"off",
          _portal_on or (line not in (A.WARN, A.ACCENT)
                         and fill not in (A.IPAD_IDLE_FILL, A.IPAD_FILL)),
          f"fill={fill} line={line}")

check("the canvas's suppressed boxes are DERIVED from the suppressed palette, "
      "not a third hand-picked pair",
      A.TARGET_BOX_COLOURS["live-suppressed"][1] == A.ACCENT_SUPPRESSED
      and A.TARGET_BOX_COLOURS["idle-suppressed"][1] == A.WARN_SUPPRESSED,
      str(A.TARGET_BOX_COLOURS))
check("...and every canvas state token is reachable from device_state_colour",
      set(A.TARGET_BOX_COLOURS) == set(A.TARGET_STATE_BY_COLOUR.values()),
      f"{sorted(A.TARGET_BOX_COLOURS)} vs "
      f"{sorted(set(A.TARGET_STATE_BY_COLOUR.values()))}")
check("target_state_name goes THROUGH device_state_colour",
      "device_state_colour(" in (
          ast.get_source_segment(SOURCE, _function("target_state_name")) or ""))
check("_colors chooses from TARGET_BOX_COLOURS and nowhere else -- the private "
      "IPAD_IDLE_* branch that painted full amber is gone",
      "TARGET_BOX_COLOURS" in (
          ast.get_source_segment(
              SOURCE, _method("MultiArrangeCanvas", "_colors")) or "")
      and "IPAD_IDLE_LINE" not in (
          ast.get_source_segment(
              SOURCE, _method("MultiArrangeCanvas", "_colors")) or ""))

# The widened signature must not have broken the three-argument form.
_probe = CanvasProbe()
A.MultiArrangeCanvas.set_target_state(_probe, "d1", True, False)
check("the 3-arg canvas call still means live -> green",
      _probe.target_states["d1"] == "live", _probe.target_states["d1"])
A.MultiArrangeCanvas.set_target_state(_probe, "d1", False, True)
check("the 3-arg canvas call still means paired-not-live -> amber",
      _probe.target_states["d1"] == "idle", _probe.target_states["d1"])
A.MultiArrangeCanvas.set_target_state(_probe, "d1", False, False)
check("the 3-arg canvas call still means unpaired -> grey",
      _probe.target_states["d1"] == "off", _probe.target_states["d1"])
check("an unchanged state does not force a redraw", _probe.redraws == 3,
      f"{_probe.redraws} redraws for 3 changes")


# ===========================================================================
# (b3) the indicator row's tokens obey the same register
# ===========================================================================
print("\n---- (b3) the indicator row, same register ----")

check("suppressed() is the register rule, as one function",
      A.suppressed(A.WARN, True) == A.WARN
      and A.suppressed(A.WARN, False) == A.WARN_SUPPRESSED
      and A.suppressed(A.ACCENT, True) == A.ACCENT
      and A.suppressed(A.ACCENT, False) == A.ACCENT_SUPPRESSED)
check("...and it leaves non-alarm colours alone in both registers",
      all(A.suppressed(colour, False) == colour
          for colour in (A.MUTED, A.DANGER, A.PORTAL)))
check("device_state_colour is WRITTEN in terms of it, not in parallel with it",
      "suppressed(" in (
          ast.get_source_segment(SOURCE, _function("device_state_colour")) or ""))

for _adv in ("starting", "stopping"):
    on_text, on_colour = A.broadcast_token(_adv, "", True)
    off_text, off_colour = A.broadcast_token(_adv, "", False)
    check(f"broadcast “{_adv}” is full WARN while the portal is live",
          on_colour == A.WARN, on_colour)
    check(f"broadcast “{_adv}” drops to the suppressed register while the "
          f"portal is off  <-- the C4 rule this token missed",
          off_colour == A.WARN_SUPPRESSED, off_colour)
    check("the register changes the colour, never the words",
          on_text == off_text, f"{on_text!r} vs {off_text!r}")
check("an advertising ERROR stays DANGER in both registers -- it is a fault of "
      "its own, not a consequence of the stopped portal",
      A.broadcast_token("off", "boom", True)[1]
      == A.broadcast_token("off", "boom", False)[1] == A.DANGER)
check("a quiet daemon gets no broadcast token at all",
      A.broadcast_token("off", "", False) is None)

# The alarm's new home. This is the ONE place full amber is allowed while the
# portal is down, and it must behave like every other button style in the file.
render = ast.get_source_segment(SOURCE, _method("App", "_render_portal_button"))
check("the portal button switches to Warn.TButton when the portal is down",
      'style="TButton" if on else "Warn.TButton"' in (render or ""),
      "the amber has to move to the cause")

# THE PENDING GUARD, EXERCISED RATHER THAN GREPPED.
#
# The check here used to be `"button_is_busy" in render`. That passed against
# DEAD CODE: no busy() call site targeted portal_btn, so the guard could never
# fire and the assertion measured a substring, not a behaviour. Stopping the
# portal is a genuine wait -- _terminate_role_process runs taskkill /T /F and
# then waits on the handle, two 4-second timeouts -- so the button is now wired
# through busy() and the guard is load-bearing.
#
# The button is REGISTERED rather than assigned: _render_portal_button drives
# the registry now, because there is a second portal control floating on the
# Desk pane. One writer, one builder, one list -- test_panes.py drives the pair
# and proves they cannot disagree; this file keeps exercising the guard.
app.portal_btn = ttk.Button(root, text="Start portal")
app._portal_btns = [app.portal_btn]
app._render_portal_button(False)
check("portal down: the button reads Start portal in the alarm style",
      app.portal_btn.cget("text") == "Start portal"
      and str(app.portal_btn.cget("style")) == "Warn.TButton",
      f"{app.portal_btn.cget('text')!r} / {app.portal_btn.cget('style')!r}")
app._render_portal_button(True)
check("portal up: it reads Stop portal as an ordinary button",
      app.portal_btn.cget("text") == "Stop portal"
      and str(app.portal_btn.cget("style")) == "TButton",
      f"{app.portal_btn.cget('text')!r} / {app.portal_btn.cget('style')!r}")
A.set_button_busy(app.portal_btn, "Stopping portal…")
app._render_portal_button(False)      # the 3-second tick, mid-stop
check("a poll tick does NOT paint over the portal button's pending state",
      app.portal_btn.cget("text") == "Stopping portal…"
      and "disabled" in app.portal_btn.state(),
      repr(app.portal_btn.cget("text")))
A.clear_button_busy(app.portal_btn)
check("clearing hands the portal button back to the renderer",
      app.portal_btn.cget("text") == "Stop portal"
      and "disabled" not in app.portal_btn.state())

toggle_src = ast.get_source_segment(SOURCE, _method("App", "toggle_portal")) or ""
check("toggle_portal really parks a wait on the portal buttons -- the guard "
      "above is REACHABLE, not decoration",
      "self._busy_portal(" in toggle_src, "no busy() call site")
_busy_src = ast.get_source_segment(SOURCE, _method("App", "_busy_portal")) or ""
check("...and that wait covers EVERY registered portal button, not the first "
      "one -- a pair where one says 'Stopping portal…' and the other still "
      "offers 'Stop portal' is the two-surfaces-one-state bug",
      "self._portal_btns" in _busy_src and "self.busy(" in _busy_src,
      _busy_src[:200])
check("...and it stops the portal on a worker, restoring in a finally, rather "
      "than blocking the UI thread under the click",
      "threading.Thread(" in toggle_src and "finally:" in toggle_src
      and "done()" in toggle_src,
      "an ~8s taskkill on the UI thread cannot repaint the label reporting it")
check("clear_button_busy only restores state it actually parked",
      "if text is None:" in (
          ast.get_source_segment(SOURCE, _function("clear_button_busy")) or ""))
_untouched = ttk.Button(root, text="Gated")
_untouched.state(["disabled"])
A.clear_button_busy(_untouched)
check("clearing a button nothing parked leaves it disabled, as some other rule "
      "wanted it",
      "disabled" in _untouched.state(), str(_untouched.state()))
_untouched.destroy()

check("the portal button reads the SAME liveness _apply_poll does",
      "self._render_portal_button(on)" in SOURCE
      and "def _portal_live" in SOURCE)
check("no second opinion about portal liveness is formed anywhere",
      SOURCE.count("self.portal_proc.poll() is None") == 1,
      f"{SOURCE.count('self.portal_proc.poll() is None')} raw polls -- exactly "
      f"one is allowed, inside _portal_live itself")

warn_map = style.map("Warn.TButton", "background")
warn_states = [str(entry) for pair in warn_map for entry in pair]
check("Warn.TButton carries disabled, pressed and active",
      all(s in " ".join(warn_states) for s in ("disabled", "pressed", "active")),
      str(warn_map))
positions = {s: min(i for i, t in enumerate(warn_states) if s in t)
             for s in ("disabled", "pressed", "active")
             if any(s in t for t in warn_states)}
check("Warn.TButton state order is disabled < pressed < active, like every "
      "other button map here",
      positions.get("disabled", 0) < positions.get("pressed", 1)
      < positions.get("active", 2), str(positions))
check("the theme really defines Warn.TButton on the app's own style table",
      'st.configure("Warn.TButton"' in SOURCE and 'st.map("Warn.TButton"' in SOURCE)

# (C4) the indicator row must use the same register, or the top of the window
# and the device area disagree about what is wrong.
POLL = _method("App", "_apply_poll")
poll_src = ast.get_source_segment(SOURCE, POLL) or ""
check("the indicator row reads the same truth table as the cards",
      "device_state_colour(on, _sub, _bonded)" in poll_src)
check("the indicator row no longer paints its own hardcoded WARN for a "
      "stopped portal",
      'text="iPad ◐ portal off", fg=WARN' not in poll_src)

# Bound structurally, because _apply_poll cannot be driven here: one of its
# branches calls toggle_portal(), which would SPAWN A REAL PORTAL PROCESS on
# the machine running the tests. So the claim is made about the call itself.
_dsc_calls = [node for node in ast.walk(POLL)
              if isinstance(node, ast.Call)
              and _dotted(node.func) == "device_state_colour"]
check("the iPad token calls device_state_colour exactly once",
      len(_dsc_calls) == 1, f"{len(_dsc_calls)} calls")
check("...and hands it the PORTAL state as its first argument, so the token "
      "can tell a stopped portal from an idle device",
      bool(_dsc_calls) and _dotted(_dsc_calls[0].args[0]) == "on",
      str([_dotted(a) for a in _dsc_calls[0].args]) if _dsc_calls else "none")
check("the broadcast token is painted through broadcast_token, not a raw "
      "fg=WARN in the middle of a 250-line method",
      "broadcast_token(" in poll_src)
check("_apply_poll hands the canvas the portal state separately too",
      "set_ipad_state(connected, False, on)" in poll_src,
      "'connected and on' collapses the two states again")
check("NO raw full-strength WARN paint survives anywhere in the file outside "
      "the Warn.TButton style",
      SOURCE.count("fg=WARN)") == 0 and SOURCE.count("fg=WARN,") == 0
      and SOURCE.count("fg=WARN\n") == 0,
      "a raw WARN paint is back")


# ===========================================================================
# (f) the card menu is mastered on root
# ===========================================================================
print("\n---- (f) the card menu outlives the card ----")

INIT = _method("App", "__init__")
masters = [_dotted(node.value.args[0])
           for node in ast.walk(INIT)
           if isinstance(node, ast.Assign)
           and _dotted(node.targets[0]) == "self._card_menu"
           and isinstance(node.value, ast.Call) and node.value.args]
check("self._card_menu is built once, in App.__init__", len(masters) == 1,
      str(masters))
check("...mastered on self.root, NOT on a card frame",
      masters == ["self.root"], str(masters))
check("_build_device_row creates no menu of its own",
      "tk.Menu" not in build_src,
      "a per-card menu dies with the card the poll tick destroys")

# The concrete crash class: _rebuild_device_rows destroys every card, and it is
# reached from _apply_device_rows on the poll tick -- which can fire inside
# tk_popup's nested event loop.
for child in list(body.winfo_children()):
    child.destroy()
check("the menu survives every card being destroyed under it",
      app._card_menu.winfo_exists())
check("the shipped menu's master is the ROOT widget, measured off the widget "
      "rather than read out of the source",
      app._card_menu.winfo_parent() == str(root),
      f"{app._card_menu.winfo_parent()!r} vs {str(root)!r}")

# The counterfactual, DEMONSTRATED. This used to be
# `not _dotted(ast.parse("tk.Menu(head)")...) == "self.root"` -- a string
# literal written inside this file, parsed by this file, asserted against by
# this file. It tested the test. So build the doomed menu for real and destroy
# the card out from under it.
doomed_card = tk.Frame(root, bg=A.BG)
doomed_menu = tk.Menu(doomed_card, tearoff=0)
check("a card-mastered menu exists while its card does",
      bool(doomed_menu.winfo_exists()))
doomed_card.destroy()
check("...and a menu mastered on a card would NOT have survived the destroy "
      "that self._card_menu just did survive",
      not doomed_menu.winfo_exists())


# ===========================================================================
# (g) every card-menu entry that opens a modal is deferred
# ===========================================================================
print("\n---- (g) every menu command is deferred ----")

FILL = _method("App", "_fill_card_menu")
check("_fill_card_menu found in the source", FILL is not None)
entries, undeferred = [], []
for node in ast.walk(FILL):
    if not (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_command"):
        continue
    command = next((kw.value for kw in node.keywords if kw.arg == "command"),
                   None)
    if command is None:
        continue          # a disabled title/detail line has no command at all
    entries.append(node.lineno)
    if not (isinstance(command, ast.Call)
            and _dotted(command.func) == "self._deferred"):
        undeferred.append(node.lineno)
check("the card menu offers the five property editors",
      len(entries) == 5, f"{len(entries)} commands found")
check("EVERY card-menu command goes through _deferred",
      not undeferred,
      f"lines {undeferred} open a modal inline from a posted menu -- "
      f"FrameModal.grab_set captures the MENU and leaves the window mouse-dead")

fill_src = ast.get_source_segment(SOURCE, FILL) or ""
for target in ("_rename_device", "_assign_device_radio", "_device_input_dialog",
               "_edit_device_displays", "_remove_device"):
    check(f"the menu still reaches {target}", target in fill_src)

# The bubbling trap: a tk.Frame does not receive its children's events, so
# binding only the frame means right-clicking the device's own NAME does
# nothing at all.
check("Button-3 is bound to the frame AND each label individually",
      "for widget in (head, dot, name, radio):" in build_src
      and "<Button-3>" in build_src)

root.destroy()

print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
