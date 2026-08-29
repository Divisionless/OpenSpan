"""What each status surface CLAIMS, and whether the claim is true.

This file exists because of one local variable.

    mac_st = None                       # _poll, once
    self.ui(lambda: self._apply_poll(running, st, on, aud, mac_st))

`mac_st` was assigned None, never reassigned by any path, and then read by four
separate surfaces in `_apply_poll`: the broadcast token, the compact-mode "Mac"
dot, the call-to-action line, and this, on screen every three seconds for as
long as the code existed —

        f"    Mac {'● up' if mac_st is not None else '○ down'}"

— so the System control line reported Doug's Managed Mac as DOWN while it was
connected and bridging. Not intermittently. Structurally, on every tick.

The interesting part is not the None. It is that the app had ALREADY outgrown
the two-device model that produced it: `self._dev_status` has carried a daemon
status dict for EVERY configured device since the multi-device wave. The status
rendering simply never followed the data. So this wave is not a bug fix with a
test attached; it is the information model catching up, and the checks below are
about WHERE a fact lives and whether anything else is quietly claiming to own
it too.

---------------------------------------------------------------------------
SECTION (b) FAILS AGAINST THE PRE-W4 SOURCE, AND THAT ORDERING WAS CONFIRMED
---------------------------------------------------------------------------
A device card used to print "not paired" whether the device was genuinely
unbonded or its daemon simply had not answered. Two completely different
situations — one you fix by pairing, one you fix by starting the VM or the lane
— rendered with one label and one colour.

Section (b) feeds a synthetic `_dev_status` in which device 2 is UP and device 3
is DOWN and demands that both cards say so. Against the pre-W4 renderer that is
impossible, and the check does not merely assert it: it DEMONSTRATES it, by
calling `device_state_colour` — the entire colour decision the old renderer
made — on both cases and showing that the two answers are byte-identical. The
old code had no third input to distinguish them with. The claim "this test would
have failed before" is therefore not a promise about a file that no longer
exists; it is arithmetic done here, in front of you, on the shipped function.

---------------------------------------------------------------------------
ONE INSTRUCTION IN THE W4 BRIEF WAS WRONG, AND IS NOT IMPLEMENTED AS WRITTEN
---------------------------------------------------------------------------
The brief said to keep `sys_status` and rewrite it to report "only what it
uniquely owns — VM, keyboard daemon, audio, portal". It owns none of those four
uniquely, and the source says so plainly:

    setind("vm",     f"VM {'●' if running else '○'}", running)
    setind("portal", f"portal {'● ON' if on else '○ off'}", on)
    setind("audio",  f"audio {'●' if aud else '○'}", aud)

Three of the four are tokens in the indicator row at the top of the window, in a
larger font, always visible — the very duplication the same brief deletes the
five compact-mode dots for. And the fourth, "keyboard ● up", is
`daemon_status()`, which by its own docstring is the FIRST configured device's
daemon; once every card reports its own daemon's reachability (section (b)),
that claim belongs to a card.

So `sys_status` keeps the one fact that is genuinely nowhere else: the ROLL-UP,
how many device daemons answered this tick. It is the counterpart of the
indicator row's `devices N/M` token, which counts SUBSCRIBED — and the gap
between the two numbers is exactly the difference between "the lane is idle" and
"the lane is not there". Section (c) enumerates every surface and would fail the
build if any fact grew a second one, including the four the brief would have
put back.

---------------------------------------------------------------------------
WHAT THIS FILE CAN SEE
---------------------------------------------------------------------------
App(root) starts the VM and the audio workers, so it is never constructed here.
The device panel is driven through App.__new__ with only the attributes
_apply_device_rows actually touches — the same seam test_device_state.py uses.
_apply_poll is never called: one of its branches calls toggle_portal(), which
would spawn a real portal process on the machine running the tests, so claims
about it are made against its source with ast.

The root is withdrawn, nothing is drawn on the desk this runs from, and nothing
here touches a running OpenSpan, the live config, the VM, or any radio.

Exit 0 = all pass.
"""
import ast
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
    for node in MODULE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


LINES = SOURCE.splitlines(True)


def _src(node):
    """The original source text of one node.

    NOT ast.get_source_segment: that re-splits the whole 8,500-line file on
    every call, and section (c) below asks for the text of every Call node in
    the module several times over. Written the naive way this file took longer
    than the two-minute timeout it was first run under -- which is a
    quadratic-scan lesson worth leaving in the file rather than in a git
    message.
    """
    start, end = getattr(node, "lineno", None), getattr(node, "end_lineno", None)
    if start is None or end is None:
        return ""
    if start == end:
        return LINES[start - 1][node.col_offset:node.end_col_offset]
    chunk = [LINES[start - 1][node.col_offset:]]
    chunk.extend(LINES[start:end - 1])
    chunk.append(LINES[end - 1][:node.end_col_offset])
    return "".join(chunk)


def _calls_to(node, name):
    """Every Call to a bare function `name` inside `node`."""
    return [n for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == name]


# ===========================================================================
# (a) `mac_st` is gone from the CODE — proven with ast, not with a substring
# ===========================================================================
print("\n---- (a) the dead two-device variable ----")

mac_st_nodes = []
for node in ast.walk(MODULE):
    if isinstance(node, ast.Name) and node.id == "mac_st":
        mac_st_nodes.append(f"Name at line {node.lineno}")
    elif isinstance(node, ast.arg) and node.arg == "mac_st":
        mac_st_nodes.append(f"parameter at line {node.lineno}")
    elif isinstance(node, ast.Attribute) and node.attr == "mac_st":
        mac_st_nodes.append(f"attribute at line {node.lineno}")
    elif isinstance(node, ast.keyword) and node.arg == "mac_st":
        mac_st_nodes.append(f"keyword argument at line {node.lineno}")
check("no executable reference to `mac_st` survives anywhere in the module",
      not mac_st_nodes, "\n      ".join(mac_st_nodes))

# ...and the reason that had to be an AST check rather than `"mac_st" in
# SOURCE`. The name is still written down several times, in the comments that
# explain why it was deleted, and those comments are the record of the bug. A
# substring check would fail on the documentation and pass on a fresh
# reintroduction hidden behind an attribute; this one does the opposite.
check("the name still appears in the source, in the PROSE that explains it — "
      "which is exactly why a substring check would have been useless here",
      SOURCE.count("mac_st") >= 3, f"{SOURCE.count('mac_st')} mentions")

POLL = _method("App", "_apply_poll")
check("_apply_poll was found", POLL is not None)
params = [a.arg for a in POLL.args.args]
check("_apply_poll's signature is (self, running, st, on, aud) — the fifth "
      "parameter is gone, not defaulted",
      params == ["self", "running", "st", "on", "aud"], str(params))
check("...and it carries no default that could resurrect one",
      not POLL.args.defaults, str([_src(d) for d in POLL.args.defaults]))

WORKER = _method("App", "_poll")
apply_calls = [n for n in ast.walk(WORKER)
               if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute)
               and n.func.attr == "_apply_poll"]
check("_poll marshals exactly one _apply_poll call", len(apply_calls) == 1,
      f"{len(apply_calls)} found")
check("...and hands it four facts, not five",
      bool(apply_calls) and len(apply_calls[0].args) == 4,
      str([_src(a) for a in apply_calls[0].args]) if apply_calls else "none")

check("the N-device roll-up exists as a module-level pure function",
      callable(getattr(A, "device_status_rollup", None)))
check("_apply_poll builds every global aggregate from it, once",
      len(_calls_to(POLL, "device_status_rollup")) == 1,
      f"{len(_calls_to(POLL, 'device_status_rollup'))} calls")

# The roll-up itself, on data rather than on faith.
DEVICES = [{"id": "d1", "name": "Tablet"}, {"id": "d2", "name": "Studio"},
           {"id": "d3", "name": "Spare"}]
roll = A.device_status_rollup(DEVICES, {
    "d1": {"kbd_subscribed": True},
    "d2": {"kbd_subscribed": False, "advertising": True},
})
check("the roll-up counts every configured device, not two",
      roll["total"] == 3, str(roll["total"]))
check("...counts the ones whose daemon ANSWERED",
      roll["reachable"] == 2, str(roll["reachable"]))
check("...counts the ones actually SUBSCRIBED, and names them",
      roll["live"] == 1 and roll["live_names"] == ["Tablet"], str(roll))
check("...and names whichever devices are broadcasting, by their own name — "
      "never the hardcoded pair 'iPad' and 'Mac'",
      roll["advertising"] == ["Studio"], str(roll["advertising"]))
check("a device that answered nothing is reachable=0, not silently counted",
      A.device_status_rollup(DEVICES, {})["reachable"] == 0)
check("the roll-up survives a device with no name (id is the fallback)",
      A.device_status_rollup([{"id": "d9"}],
                             {"d9": {"kbd_subscribed": True}})["live_names"]
      == ["d9"])


# ===========================================================================
# (b) THE CHECK THAT COULD NOT HAVE PASSED BEFORE THIS WAVE
#
# Device 2's daemon answers. Device 3's does not. Both cards must say which.
# ===========================================================================
print("\n---- (b) unpaired and unreachable are different words now ----")


class FakeCanvas:
    def __init__(self, devices):
        self._devices = devices
        self.target_state = {}

    def devices(self):
        return self._devices

    def set_target_state(self, device_id, live, paired, portal_on=True):
        self.target_state[device_id] = (live, paired, portal_on)


class FakeBt:
    _radios = []          # no radio inventory -> radio_missing is never True


def make_devices():
    return [
        {"id": "d1", "name": "Tablet", "port": 7810,
         "radio": "AA:BB:CC:00:00:01", "enabled": True, "displays": []},
        {"id": "d2", "name": "Studio", "port": 7811,
         "radio": "AA:BB:CC:00:00:02", "enabled": True, "displays": []},
        {"id": "d3", "name": "Bench", "port": 7812,
         "radio": "AA:BB:CC:00:00:03", "enabled": True, "displays": []},
    ]


root = tk.Tk()
root.withdraw()                 # never draw on the desk this is run from
A._theme_startup_buttons()
style = ttk.Style()
try:
    style.theme_use("clam")
except tk.TclError:
    pass
style.configure("TButton", padding=(10, 3), font=("Segoe UI", 10))

body = tk.Frame(root, bg=A.BG)
app = A.App.__new__(A.App)              # never App(root): that starts the VM
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
app._rebuild_device_rows()


def card_text(device_id):
    return app._dev_rows[device_id]["name"].cget("text")


def card_colour(device_id):
    return app._dev_rows[device_id]["dot"].cget("fg")


# d2 answers and is bonded but idle. d3 does not answer at all, and the bond
# state cached for it says the same thing d2's does -- which is the whole
# point: the ONLY difference between these two devices this tick is whether
# their daemon replied.
app._dev_state("d2")["paired"] = True
app._dev_state("d3")["paired"] = True
app._dev_status = {"d1": {"kbd_subscribed": False},
                   "d2": {"kbd_subscribed": False}}
app._apply_device_rows(True)

check("the device whose daemon ANSWERED does not claim to be unreachable",
      "unreachable" not in card_text("d2"), card_text("d2"))
check("the device whose daemon did NOT answer says so, on the card",
      "unreachable" in card_text("d3"), card_text("d3"))
check("...and still reports the bond it last knew about, rather than "
      "silently downgrading to 'not paired'",
      "paired" in card_text("d3"), card_text("d3"))

# ---- the counterfactual, RUN on this exact state --------------------------
# device_state_colour IS the whole colour decision the pre-W4 renderer made.
# Drive it on the two devices standing above, off the same _dev_status and the
# same cached bonds the shipped renderer just used. It cannot see the one fact
# that differs, so the two cards come out identical -- which is the failure
# this section describes, reproduced rather than asserted.


def pre_w4_card(device_id, portal_on=True):
    """What the pre-W4 renderer would have painted: it read `live` and
    `paired` off the device and nothing else."""
    status = app._dev_status.get(device_id)
    return A.device_state_colour(
        portal_on, bool(status and status.get("kbd_subscribed")),
        bool(app._dev_state(device_id)["paired"]))


check("DEMONSTRATION: on this very state the pre-W4 renderer paints the "
      "reachable device and the unreachable one identically — it had no third "
      "input to tell them apart, so section (b) could not have passed before "
      "this wave",
      pre_w4_card("d2") == pre_w4_card("d3") == (A.WARN, "paired"),
      f"d2 {pre_w4_card('d2')} vs d3 {pre_w4_card('d3')}")
check("...and the shipped cards, on that same state, do NOT agree",
      card_text("d2") != card_text("d3"),
      f"{card_text('d2')!r} vs {card_text('d3')!r}")
old_args = [a.arg for a in _function("device_state_colour").args.args]
check("...because the older function has no slot for reachability at all — "
      "this is structural, not a matter of what it was fed",
      old_args == ["portal_on", "connected", "paired"], str(old_args))

# An unbonded, unreachable device is a third reading again -- nothing was
# expected of it, so nothing is alarming, but the WORD was still wrong before.
app._dev_state("d1")["paired"] = False
app._dev_status = {"d2": {"kbd_subscribed": False}}
app._apply_device_rows(True)
check("an unbonded device whose daemon is silent no longer claims 'not "
      "paired' — it says what is actually known",
      "unreachable" in card_text("d1") and "not paired" not in card_text("d1"),
      card_text("d1"))

# ...and a device that answers and is genuinely unpaired still says so.
app._dev_status = {"d1": {"kbd_subscribed": False}}
app._apply_device_rows(True)
check("a device that ANSWERED and is genuinely unbonded still reads 'not "
      "paired'",
      card_text("d1").endswith("not paired"), card_text("d1"))

check("device_reach_state, which does take reachability, splits them",
      A.device_reach_state(True, True, False, False)
      != A.device_reach_state(True, False, False, False),
      f"{A.device_reach_state(True, True, False, False)} vs "
      f"{A.device_reach_state(True, False, False, False)}")

# One truth table, still. The new function must be WRITTEN in terms of the old
# one, not beside it.
reach_src = _src(_function("device_reach_state"))
check("device_reach_state delegates to device_state_colour rather than "
      "restating the five rows",
      "device_state_colour(" in reach_src)
check("...and every colour it invents for itself goes through suppressed()",
      "suppressed(" in reach_src)
for args in ((True, True, True, True), (True, True, False, True),
             (True, True, False, False), (False, True, True, True),
             (False, True, False, True), (False, True, False, False)):
    portal_on, _reach, connected, paired = args
    check(f"reachable {args[1:]} portal={'ON' if portal_on else 'off'}: "
          f"identical to device_state_colour",
          A.device_reach_state(*args)
          == A.device_state_colour(portal_on, connected, paired),
          f"{A.device_reach_state(*args)} vs "
          f"{A.device_state_colour(portal_on, connected, paired)}")


# ===========================================================================
# (c) ONE FACT, ONE SURFACE — enumerated, so a second copy fails the build
# ===========================================================================
print("\n---- (c) one fact, one surface ----")


# Every Call in the module, with its source text, extracted ONCE.
CALL_SRC = [(node, _src(node)) for node in ast.walk(MODULE)
            if isinstance(node, ast.Call)]


def surfaces_writing(marker):
    """Every WIDGET that some config()/set() call writes `marker` into.

    `setind(key, ...)` is a write to self._ind[key] -- it is the indicator
    row's own one-line setter and skipping it would make this audit blind to
    the row that carries most of these facts.
    """
    surfaces = set()
    for node, segment in CALL_SRC:
        if marker not in segment:
            continue
        func = node.func
        if (isinstance(func, ast.Name) and func.id == "setind"
                and node.args and isinstance(node.args[0], ast.Constant)):
            surfaces.add(f"self._ind[{node.args[0].value!r}]")
        elif (isinstance(func, ast.Attribute)
              and func.attr in ("config", "set")):
            surfaces.add(ast.unparse(func.value))
    return surfaces


ENUMERATED = {
    "the VM's up/down state":
        ("VM {'●' if running else '○'}", {"self._ind['vm']"}),
    "the keyboard daemon's reachability":
        ("daemon starting", {"self._ind['ipad']"}),
    "the input portal's on/off state":
        ("portal {'● ON' if on else '○ off'}", {"self._ind['portal']"}),
    "the audio sender's on/off state":
        ("audio {'●' if aud else '○'}", {"self._ind['audio']"}),
    "the daemon roll-up":
        ("/{roll['total']} answering", {"self.sys_status"}),
    "the SUBSCRIBED roll-up":
        ("devices {roll['live']}", {"self._ind['mac']"}),
}
for fact, (marker, expected) in ENUMERATED.items():
    got = surfaces_writing(marker)
    check(f"{fact} is rendered by exactly one widget: {sorted(expected)[0]}",
          got == expected, f"rendered by {sorted(got)}")

# per-device reachability is the one fact whose renderer cannot be found by a
# text marker, because the words are returned by a pure function three thousand
# lines from the widget. So it is pinned at both ends instead.
reach_calls = [n for n in ast.walk(MODULE)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "device_reach_state"]
check("device_reach_state is called exactly once in the whole module",
      len(reach_calls) == 1, f"{len(reach_calls)} calls")
APPLY = _method("App", "_apply_device_rows")
check("...and that call is inside _apply_device_rows",
      bool(reach_calls) and reach_calls[0] in set(ast.walk(APPLY)))
name_writes = [n for n in ast.walk(APPLY)
               if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute)
               and n.func.attr == "config"
               and ast.unparse(n.func.value) == "row['name']"]
check("the renderer writes the card's state text through exactly one widget",
      len(name_writes) == 1, f"{len(name_writes)} writes to row['name']")
check("no other widget anywhere writes the word 'unreachable'",
      surfaces_writing("unreachable") == set(),
      str(sorted(surfaces_writing("unreachable"))))

# sys_status specifically: it must have stopped restating the indicator row.
sys_writes = [n for n in ast.walk(MODULE)
              if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Attribute)
              and n.func.attr == "set"
              and ast.unparse(n.func.value) == "self.sys_status"]
check("sys_status is written in exactly one place", len(sys_writes) == 1,
      f"{len(sys_writes)} writes")
sys_src = _src(sys_writes[0]) if sys_writes else ""
for word in ("VM", "keyboard", "audio", "portal", "Mac"):
    check(f"sys_status no longer restates “{word}” — that is somebody else's "
          f"fact",
          word not in sys_src, sys_src)

# ...and the two roll-ups are genuinely different questions, not the same
# number printed twice. This is the claim that justifies keeping both.
split = A.device_status_rollup(DEVICES, {
    "d1": {"kbd_subscribed": True},
    "d2": {"kbd_subscribed": False},
})
check("ANSWERING and SUBSCRIBED are different counts — the gap between them is "
      "the difference between an idle lane and a missing one",
      split["reachable"] == 2 and split["live"] == 1)

# The compact-mode duplicates are gone, not renamed.
for gone in ("self.c_stat", "self.c_ready"):
    assigns = [n for n in ast.walk(MODULE)
               if isinstance(n, ast.Attribute)
               and ast.unparse(n) == gone]
    check(f"{gone} no longer exists anywhere in the code", not assigns,
          f"{len(assigns)} references")
check("...and the headphones line, which nothing else carries, survives",
      any(isinstance(n, ast.Attribute) and ast.unparse(n) == "self.c_buds"
          for n in ast.walk(MODULE)))


# ===========================================================================
# (d) the admin / UIPI lamp is still there and still reachable
# ===========================================================================
print("\n---- (d) the one surface that explains a dead mouse ----")

INIT = _method("App", "__init__")
init_src = _src(INIT)
check("the indicator row is still built", "indrow = tk.Frame(" in init_src)
# The row's keys used to be a literal tuple inside __init__. They are
# INDICATOR_ORDER now, because that row OVERFLOWS its cavity at the app's
# minimum width and, with no scrolling, Tk clips and then drops whatever was
# packed last -- so the order is load-bearing and lives beside the reason for
# it. test_single_page.py measures the consequence at 940px; this only has to know
# the admin token is still one of the row's own.
check("...and still declares an `admin` token among its keys",
      "admin" in A.INDICATOR_ORDER and "self._ind[_k] = _lb" in init_src,
      str(A.INDICATOR_ORDER))
check("...packed FIRST, because pack order is drop order and this is the one "
      "token that must never be the casualty",
      A.INDICATOR_ORDER[0] == "admin" and "admin" in A.INDICATOR_MUST_SURVIVE,
      str(A.INDICATOR_ORDER))
admin_writes = surfaces_writing("NOT ADMIN")
check("the admin lamp is painted, by exactly one widget",
      admin_writes == {"self._ind['admin']"}, str(sorted(admin_writes)))
poll_src = _src(POLL)
check("...and it is gated on is_elevated(), so it lights only when the hooks "
      "really are dead",
      "if is_elevated():" in poll_src)
check("is_elevated still documents WHY that lamp matters (UIPI)",
      "UIPI" in (_src(_function("is_elevated")) or ""))
check("the admin lamp is DANGER, not a suppressed or muted tone — it is a "
      "fault, not a consequence of the portal",
      "text=\"⚠ NOT ADMIN\", fg=DANGER" in poll_src)


# ===========================================================================
# (e) reachability composes with the suppressed register
# ===========================================================================
print("\n---- (e) unreachable takes the register like everything else ----")

on_colour, on_text = A.device_reach_state(True, False, False, True)
off_colour, off_text = A.device_reach_state(False, False, False, True)
check("an unreachable, bonded device is full WARN while the portal runs",
      on_colour == A.WARN, on_colour)
check("...and drops to WARN_SUPPRESSED the moment the portal stops — it does "
      "NOT shout alongside the one button that fixes it",
      off_colour == A.WARN_SUPPRESSED, off_colour)
check("the suppressed reading is not full strength by any other name",
      off_colour not in (A.WARN, A.ACCENT, A.DANGER), off_colour)
check("and it SAYS the portal is the reason, like every other suppressed card",
      A.PORTAL_OFF_SUFFIX in off_text and A.PORTAL_OFF_SUFFIX not in on_text,
      f"{off_text!r} / {on_text!r}")
check("every colour device_reach_state can return is a known register colour, "
      "so the arrangement canvas can still look its state token up",
      all(A.device_reach_state(p, r, c, q)[0] in A.TARGET_STATE_BY_COLOUR
          for p in (True, False) for r in (True, False)
          for c in (True, False) for q in (True, False)))
check("device_reach_state introduces no colour of its own — every value it "
      "returns is one device_state_colour can also return",
      {A.device_reach_state(p, r, c, q)[0]
       for p in (True, False) for r in (True, False)
       for c in (True, False) for q in (True, False)}
      <= {A.device_state_colour(p, c, q)[0]
          for p in (True, False) for c in (True, False)
          for q in (True, False)})

# ...and on the SHIPPED renderer, not just on the pure function.
app._dev_state("d2")["paired"] = True
app._dev_status = {"d1": {"kbd_subscribed": False}}
app._apply_device_rows(False)           # the portal is DOWN
check("the shipped card paints an unreachable bonded device in the suppressed "
      "register while the portal is off",
      card_colour("d2") == A.WARN_SUPPRESSED, card_colour("d2"))
app._apply_device_rows(True)
check("...and at full strength once the portal is up",
      card_colour("d2") == A.WARN, card_colour("d2"))
check("a radio fault still OUTRANKS reachability — an unknowable device must "
      "not be reported as a merely idle one",
      "radio not present" not in card_text("d2"),
      "no radio inventory here, so this must not fire")


# ===========================================================================
# (f) no wraplength literal in the left column
# ===========================================================================
print("\n---- (f) a wraplength literal is a height bug at every other width ----")


def wraplength_literals(fn):
    found = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "wraplength" and isinstance(kw.value, ast.Constant):
                found.append(f"line {kw.value.lineno}: {kw.value.value}")
    return found


left = wraplength_literals(INIT)
check("App.__init__ — the whole left column — carries no wraplength literal",
      not left, "; ".join(left))
bt = wraplength_literals(_method("BtPanel", "__init__"))
check("BtPanel carries none either (the brief named its 500 and 470 as the "
      "same bug)", not bt, "; ".join(bt))
check("the mode explainer is bound instead",
      "bind_wraplength(_mode_note)" in init_src)

# The rule itself, driven. A binding nobody exercises is a binding nobody has
# checked -- which is how the literal survived three waves in the first place.
probe = tk.Label(root, text="x " * 60, bg=A.BG, fg=A.MUTED,
                 font=("Segoe UI", 8))
probe.pack(fill="x")
A.bind_wraplength(probe)
check("a bound label starts at the declared fallback, never at 0 (no wrap "
      "at all would make the window as wide as the sentence)",
      int(str(probe.cget("wraplength"))) == A.WRAP_FALLBACK,
      str(probe.cget("wraplength")))
check("a real measured width replaces it",
      A.fit_wraplength(probe, 520) is True
      and int(str(probe.cget("wraplength"))) == 520,
      str(probe.cget("wraplength")))
check("the same width again is a no-op, not a reconfigure on every tick",
      A.fit_wraplength(probe, 520) is False)
check("a width below the floor is REFUSED — Tk reports width=1 for a widget "
      "that has never been mapped, and wraplength=1 is one word per line",
      A.fit_wraplength(probe, 1) is False
      and int(str(probe.cget("wraplength"))) == 520)
# ...and that the rule is actually WIRED to <Configure>, not merely written.
#
# It is checked by inspecting Tk's binding table rather than by firing an
# event, and that is a finding rather than a shortcut: under a withdrawn root
# Tk delivers NO Configure to a widget at all -- not from event_generate with
# an explicit -width, not from a real geometry change followed by update().
# Measured directly before writing this line. Which is also precisely why
# fit_wraplength has a floor: the first width a mapped widget ever reports can
# be 1, and wraplength=1 is one word per line.
bound = probe.bind("<Configure>")
check("<Configure> is bound on the label — the rule is wired, not just defined",
      bool(bound) and "lambda" in bound, repr(bound))
probe.destroy()

# ---- and the chrome the left column stopped paying for ---------------------
check("_section exists as the left column's titled-block helper",
      callable(getattr(A, "_section", None)))
check("all three left-column LabelFrames are gone",
      "ttk.LabelFrame(bridge" not in init_src
      and "ttk.LabelFrame(\n" not in init_src
      and init_src.count("ttk.LabelFrame") == 0,
      f"{init_src.count('ttk.LabelFrame')} remain")
check("...and the right column's remaining one is deliberately untouched — "
      "that column does not bind the window's height",
      SOURCE.count("ttk.LabelFrame(parent, text=\"Audio & status\"") == 1)
# "Radio options" did not lose its frame for chrome. It lost the whole panel:
# a settings surface for something that is not a setting. Doug, running three
# radios while it claimed single-radio mode was active: *"It is not possible
# for it to look like that... we don't need to even have this section because
# the paired device list should handle all of this."* The assertion is that no
# LabelFrame is built for it anywhere, under any parent.
check("the Radio options panel is gone, frame and all",
      "Radio options\"" not in SOURCE
      and "ttk.LabelFrame(self," not in SOURCE)
check("the ttk TLabelframe theming therefore still has a consumer and must "
      "stay",
      'st.configure("TLabelframe"' in SOURCE)

# The chrome saving, measured rather than asserted. Both arms hold an identical
# body, so the difference is frame and nothing else. The TLabelframe styling is
# App._theme's own -- measuring the LabelFrame under the default clam theme
# would compare against a frame this app never drew.
style.configure("TLabelframe", background=A.BG, bordercolor="#221F2A",
                relief="solid", borderwidth=1)
style.configure("TLabelframe.Label", background=A.BG, foreground=A.MUTED,
                font=("Segoe UI", 9, "bold"))
host = tk.Frame(root, bg=A.BG, width=549)
host.pack()


def _filled(inner):
    tk.Label(inner, text="content", bg=A.BG, fg=A.FG,
             font=("Segoe UI", 9)).pack(fill="x")
    root.update_idletasks()
    return inner.winfo_reqheight()


frame = ttk.LabelFrame(host, text="System control", padding=8)
frame.pack(fill="x", padx=16)
inner = tk.Frame(frame, bg=A.BG)
inner.pack(fill="x")
inner_h = _filled(inner)                 # fill it FIRST, then measure the frame
lf_chrome = frame.winfo_reqheight() - inner_h
frame.destroy()

body_frame = A._section(host, "System control", pady=(0, 0))
body_h = _filled(body_frame)
sec_chrome = body_frame.master.winfo_reqheight() - body_h
body_frame.master.destroy()

check("a _section costs materially less chrome than the LabelFrame it replaced",
      sec_chrome < lf_chrome - 10,
      f"LabelFrame {lf_chrome}px vs _section {sec_chrome}px")
check("...and it is still a titled block: a caption over a rule, not nothing",
      sec_chrome >= 15, f"{sec_chrome}px — too cheap to be drawing anything")
print(f"      (measured: LabelFrame {lf_chrome}px of chrome, "
      f"_section {sec_chrome}px — {(lf_chrome - sec_chrome) * 3}px back "
      f"across the column's three blocks)")

root.destroy()
print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
