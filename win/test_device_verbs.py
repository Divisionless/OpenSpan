"""The four connection verbs on the arrangement canvas's right-click menu.

Doug: *"it'd be nice if i could have my Pair connect disconnect unpair options
here, only surfacing two at a time based on what is relevant to toggle"*

---------------------------------------------------------------------------
1. TWO IS NOT THE COUNT
---------------------------------------------------------------------------
"Two at a time" is the observation, not the rule. Run DEVICE_VERB_GATES through
the real device states and the answer is 1, 3, 2, 1 -- and 0 on a device with no
radio. Pair stays live on a bonded-but-idle lane because it is NOT gated on
"not paired": re-pairing a bonded device is legal, and it is how a bad bond gets
recovered. A menu built to a fixed count of two would have had to hide one of
those three, and the one it hid would have been a live action the user came to
the menu for.

So the rule implemented is SHOW THE ONES THAT ARE LIVE. Section (b) is that
table, written down as a check -- including the THREE.

---------------------------------------------------------------------------
2. THE FAILURE MODE THIS FILE EXISTS TO CATCH
---------------------------------------------------------------------------
A second surface for an action that already has one, carrying its own copy of
the state. This app has shipped that once already: _build_device_row's own
docstring records five buttons that were permanently enabled because nothing
ever re-derived them, on a card that looked authoritative the whole time.

The verbs now have two surfaces -- the device card's four buttons and this menu
-- and there is exactly one way that stays safe: one producer of the facts
(App._device_verb_facts), one caller of the gates (device_verb_offer), two
renderers. Section (c) drives every state through BOTH renderers and demands
the same answer. Section (f) then breaks the shipped source three ways and
proves (c), (e) and the busy rule each go red, because a check that has never
been made to fail has not been shown to check anything.

---------------------------------------------------------------------------
3. WHY THERE IS NO WINDOW HERE
---------------------------------------------------------------------------
Every Tk root PAINTS on the desk this runs from. Nothing in this file needs
one: _fill_device_verb_entries takes its menu as a PARAMETER, and
_apply_device_rows writes through .config()/.state() on whatever is in
self._dev_rows. Both are lifted off the real App class and bound to a stub, so
what runs is the shipping code and not a copy of it.

Exit 0 = all pass.
"""
import ast
import os
import sys
import textwrap
import types

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


SOURCE = open(os.path.join(HERE, "openspan.py"), encoding="utf-8").read()
TREE = ast.parse(SOURCE)


def _method(class_name, method_name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if (isinstance(item, ast.FunctionDef)
                        and item.name == method_name):
                    return item
    return None


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


def _block(node):
    """The shipped source of one method, dedented so it can be re-compiled."""
    start = min([d.lineno for d in node.decorator_list] or [node.lineno])
    return textwrap.dedent(
        "\n".join(SOURCE.splitlines()[start - 1:node.end_lineno]))


# ===========================================================================
# The stub half: an App-shaped object with no window anywhere in it
# ===========================================================================
RADIO = "AA:BB:CC:DD:EE:FF"
DEVICE_ID = "mac"
DEVICE_NAME = "Managed Mac"


class StubButton:
    """Only what _apply_device_rows and paint_button_busy actually call."""

    def __init__(self, key):
        self.key = key
        self.text = ""
        self.disabled = None

    def config(self, **kwargs):
        if "text" in kwargs:
            self.text = kwargs["text"]

    def state(self, spec):
        for token in spec:
            if token == "disabled":
                self.disabled = True
            elif token == "!disabled":
                self.disabled = False

    def __str__(self):
        return f"stub.button.{self.key}"


class StubLabel:
    def __init__(self):
        self.kwargs = {}

    def config(self, **kwargs):
        self.kwargs.update(kwargs)


class StubCanvas:
    def __init__(self, device):
        self.config = {"devices": [device]}
        self.target_state = {}

    def devices(self):
        return self.config["devices"]

    def target(self, device_id):
        return A.device_by_id(self.config, device_id)

    def set_target_state(self, device_id, live, paired, portal_on):
        self.target_state[device_id] = (live, paired, portal_on)


class StubRoot:
    """root.after, which is the whole of what _deferred needs."""

    def __init__(self):
        self.queued = []

    def after(self, _ms, fn):
        self.queued.append(fn)


class RecordingMenu:
    """A tk.Menu's add_* surface, recorded instead of drawn."""

    def __init__(self):
        self.rows = []

    def delete(self, *_args):
        self.rows = []

    def add_separator(self):
        self.rows.append({"kind": "separator", "label": "",
                          "state": "", "command": None})

    def add_command(self, label="", state="normal", command=None):
        self.rows.append({"kind": "command", "label": label,
                          "state": state, "command": command})

    def add_cascade(self, label="", menu=None):
        self.rows.append({"kind": "cascade", "label": label,
                          "state": "normal", "command": None})


class StubApp:
    """Built WITHOUT App.__init__ -- constructing the real App starts the VM
    and the audio workers. Every method under test is lifted off the real
    class below."""

    def __init__(self, radio=RADIO, present=True):
        device = {"id": DEVICE_ID, "name": DEVICE_NAME, "radio": radio,
                  "port": 9956, "enabled": True, "displays": []}
        self.canvas = StubCanvas(device)
        self.root = StubRoot()
        self.bt_panel = types.SimpleNamespace(
            _radios=([{"address": RADIO}] if present else [{"address":
                                                            "11:22:33:44:55:66"}]))
        self._dev_status = {}
        self._dev_states = {}
        self._vm_reachable = True
        self._dev_rows = {DEVICE_ID: {
            "dot": StubLabel(), "name": StubLabel(), "radio": StubLabel(),
            "buttons": {key: StubButton(key) for key in A.DEVICE_VERBS},
        }}
        self.clicked = []

    def _rebuild_device_rows(self):        # never reached: the ids match
        raise AssertionError("the stub's rows must already match the devices")


# The shipping methods, bound to the stub. Nothing here is re-implemented.
for _name in ("_dev_state", "_device_verb_facts", "_apply_device_rows",
              "_device_verb_entries", "_verb_menu_label",
              "_fill_device_verb_entries", "_deferred", "device_record",
              "device_lane"):
    setattr(StubApp, _name, A.App.__dict__[_name])


# The four handlers are RECORDERS: the real ones ssh to the guest. Bound off
# DEVICE_VERB_HANDLERS so a renamed handler cannot leave a stale stub behind.
def _recorder(verb):
    def handler(self, device_id):
        self.clicked.append((verb, device_id))
    return handler


for _verb, _method_name in A.DEVICE_VERB_HANDLERS.items():
    setattr(StubApp, _method_name, _recorder(_verb))


# ---- the five states, as facts rather than as expected answers -------------
# paired / live / busy / verb. Nothing here says which verbs should come out;
# that is section (b)'s job and it is stated separately on purpose.
STATES = {
    "unpaired":    {"paired": False, "live": False, "busy": False, "verb": ""},
    "paired-idle": {"paired": True,  "live": False, "busy": False, "verb": ""},
    "connected":   {"paired": True,  "live": True,  "busy": False, "verb": ""},
    "busy":        {"paired": False, "live": False, "busy": True,
                    "verb": "pair"},
    # Disconnect in flight. Its gate says live, its own work is running, and
    # both surfaces must let the in-flight presentation win over the gate.
    "disconnecting": {"paired": True, "live": True, "busy": False,
                      "verb": "disconnect"},
}


def drive(app, state):
    """Put one device into one state, through the same _dev_state the app
    writes -- not by handing the renderers a pre-baked facts dict."""
    dev = app._dev_state(DEVICE_ID)
    dev["paired"] = state["paired"]
    dev["inflight"] = state["busy"]
    dev["broadcasting"] = False
    dev["verb"] = state["verb"]
    app._dev_status[DEVICE_ID] = {"kbd_subscribed": state["live"]}


def menu_verbs(app):
    """(enabled verbs, in-flight verbs) as the MENU renders them."""
    rows = app._device_verb_entries(DEVICE_ID)
    return ({key for key, _label, ok in rows if ok},
            {key for key, _label, ok in rows if not ok})


def card_verbs(app):
    """(enabled verbs, in-flight verbs) as the CARD renders them.

    The in-flight set is read off the button's TEXT against
    DEVICE_VERB_SPEC's own present participle -- the shipped constant, not a
    re-typed one -- so a button merely disabled is not mistaken for a busy one.
    """
    app._apply_device_rows(True)
    buttons = app._dev_rows[DEVICE_ID]["buttons"]
    busy_label = {key: participle for key, _r, participle in A.DEVICE_VERB_SPEC}
    return ({key for key, b in buttons.items() if b.disabled is False},
            {key for key, b in buttons.items()
             if b.text == busy_label[key] and b.disabled is True})


def agreement_problems(app):
    """Section (c), as a reusable function so section (f) can run the SAME
    check against a deliberately broken build and watch it go red."""
    out = []
    for name, state in STATES.items():
        drive(app, state)
        menu_on, menu_busy = menu_verbs(app)
        drive(app, state)                      # the card gets the same state
        card_on, card_busy = card_verbs(app)
        if menu_on != card_on:
            out.append(f"{name}: menu offers {sorted(menu_on)}, "
                       f"card offers {sorted(card_on)}")
        if menu_busy != card_busy:
            out.append(f"{name}: menu busy {sorted(menu_busy)}, "
                       f"card busy {sorted(card_busy)}")
    return out


def naming_problems(app):
    """Section (e), likewise reusable."""
    out = []
    for name, state in STATES.items():
        drive(app, state)
        menu = RecordingMenu()
        app._fill_device_verb_entries(menu, DEVICE_ID)
        named = [row for row in menu.rows
                 if row["kind"] == "command" and row["state"] == "disabled"
                 and DEVICE_NAME in row["label"]]
        if not named:
            out.append(f"{name}: no disabled entry names {DEVICE_NAME!r}")
    return out


# ===========================================================================
# (a) the menu is built FROM DEVICE_VERB_SPEC, not from a list retyped here
# ===========================================================================
print("\n---- (a) built from the spec ----")

ENTRIES = _method("App", "_device_verb_entries")
FILLER = _method("App", "_fill_device_verb_entries")
SURFACE = _method("App", "_fill_surface_menu")
LABELLER = _method("App", "_verb_menu_label")
check("_device_verb_entries exists in the source", ENTRIES is not None)
check("_fill_device_verb_entries exists in the source", FILLER is not None)

spec_loops = [node for node in ast.walk(ENTRIES)
              if isinstance(node, ast.For)
              and _dotted(node.iter) == "DEVICE_VERB_SPEC"]
check("(a) the entries are generated by iterating DEVICE_VERB_SPEC itself",
      len(spec_loops) == 1,
      "a hand-written list of verbs here drifts from the card silently")

# ...and nothing in either function names a verb, with ONE stated exception.
verb_literals = {}
for who, node in (("_device_verb_entries", ENTRIES),
                  ("_fill_device_verb_entries", FILLER),
                  ("_verb_menu_label", LABELLER)):
    hits = [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and n.value in A.DEVICE_VERBS]
    if hits:
        verb_literals[who] = hits
check("(a) the entry builder and the filler name no verb at all",
      "_device_verb_entries" not in verb_literals
      and "_fill_device_verb_entries" not in verb_literals,
      str(verb_literals))
check("(a) the only verb named anywhere in the section is disconnect, in the "
      "labeller, which is the stated cancel special case",
      verb_literals.get("_verb_menu_label", []) == ["disconnect"],
      str(verb_literals.get("_verb_menu_label")))

# The label table is the fourth verb-keyed table in the file, so it is checked
# like the other three: a fifth verb with no label would be a KeyError raised
# while a menu is being posted.
check("(a) the menu label table is coverage-checked against DEVICE_VERBS",
      set(A.DEVICE_VERB_MENU_SUFFIX) == set(A.DEVICE_VERBS),
      str(sorted(A.DEVICE_VERB_MENU_SUFFIX)))
_loud = ""
try:
    A._require_verb_coverage({"pair": "x"}, "a deliberately short label table")
except KeyError as exc:                       # noqa: BLE001
    _loud = str(exc)
check("(a) ...and a short one fails LOUDLY at import, naming what is missing",
      "connect" in _loud and "unpair" in _loud, repr(_loud))

# The section is REACHED: a builder nothing calls is a menu nobody sees.
check("(a) _fill_surface_menu calls the verb section",
      any(isinstance(n, ast.Call)
          and _dotted(n.func) == "self._fill_device_verb_entries"
          for n in ast.walk(SURFACE)))
# ...and only on a MANAGED display. A Windows monitor has no device behind it.
check("(a) the local-monitor branch returns before it",
      any(isinstance(n, ast.Return) for n in ast.walk(SURFACE)),
      "_fill_surface_menu must still early-return for key[0] == 'local'")

# Item 6: right-click on EMPTY canvas is an arrangement menu; verbs have no
# referent there and it was to be left alone.
DESK = _method("App", "_fill_desk_menu")
check("(a) the empty-canvas menu is untouched -- no verb section on it",
      not any(isinstance(n, ast.Call)
              and _dotted(n.func) == "self._fill_device_verb_entries"
              for n in ast.walk(DESK)))

# ONE gate caller. This is the structural half of (c): if some future surface
# calls DEVICE_VERB_GATES directly, it is deciding for itself again.
OFFER = next(n for n in TREE.body
             if isinstance(n, ast.FunctionDef)
             and n.name == "device_verb_offer")
DECL = next(n for n in TREE.body
            if isinstance(n, ast.Assign)
            and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id == "DEVICE_VERB_GATES")
outside = sorted({
    node.lineno for node in ast.walk(TREE)
    if isinstance(node, ast.Name) and node.id == "DEVICE_VERB_GATES"
    and not (OFFER.lineno <= node.lineno <= OFFER.end_lineno)
    and not (DECL.lineno <= node.lineno <= DECL.end_lineno)})
check("(a) device_verb_offer is the ONLY code that reads DEVICE_VERB_GATES",
      not outside, f"other readers at line(s) {outside}")


# ===========================================================================
# (b) every state, and the count is whatever is LIVE
# ===========================================================================
print("\n---- (b) the live set, per state ----")

# Written out deliberately: this is the REQUIREMENT, not a copy of the subject.
# If a gate changes, this table must be re-argued rather than re-derived.
EXPECTED_ON = {
    "unpaired": {"pair"},
    "paired-idle": {"pair", "connect", "unpair"},        # THREE, not two
    "connected": {"disconnect", "unpair"},
    "busy": {"disconnect"},                              # which means cancel
    # Disconnect's own work is running, so it is in-flight rather than offered
    # -- but the lane is still bonded and its daemon still answers, so Unpair
    # is live. That is the pre-existing gate, unchanged by this wave, and the
    # card says the same thing (section (c) is what proves the "same").
    "disconnecting": {"unpair"},
}
EXPECTED_BUSY = {
    "unpaired": set(),
    "paired-idle": set(),
    "connected": set(),
    "busy": {"pair"},
    "disconnecting": {"disconnect"},
}

app = StubApp()
for state_name, state in STATES.items():
    drive(app, state)
    on, in_flight = menu_verbs(app)
    check(f"(b) {state_name}: the menu offers exactly "
          f"{sorted(EXPECTED_ON[state_name])}",
          on == EXPECTED_ON[state_name], f"got {sorted(on)}")
    check(f"(b) {state_name}: the in-flight verbs are exactly "
          f"{sorted(EXPECTED_BUSY[state_name])}",
          in_flight == EXPECTED_BUSY[state_name], f"got {sorted(in_flight)}")

check("(b) the paired-and-idle state really does offer THREE -- Pair is not "
      "gated on 'not paired', because re-pairing recovers a bad bond",
      len(EXPECTED_ON["paired-idle"]) == 3
      and "pair" in EXPECTED_ON["paired-idle"])

# The zero case: a device whose radio is assigned but not present.
gone = StubApp(present=False)
drive(gone, STATES["unpaired"])
check("(b) a device whose radio is missing gets NO verbs",
      menu_verbs(gone)[0] == set(), str(menu_verbs(gone)))
_menu = RecordingMenu()
drive(gone, STATES["unpaired"])
gone._fill_device_verb_entries(_menu, DEVICE_ID)
check("(b) ...and the section says why instead of standing empty",
      any("radio is not present" in row["label"] for row in _menu.rows),
      str([row["label"] for row in _menu.rows]))

noradio = StubApp(radio="")
drive(noradio, STATES["unpaired"])
_menu = RecordingMenu()
noradio._fill_device_verb_entries(_menu, DEVICE_ID)
check("(b) a device with no radio at all says THAT",
      any("no radio assigned" in row["label"] for row in _menu.rows),
      str([row["label"] for row in _menu.rows]))

# Labels: the cancel rename, and the measured waits.
drive(app, STATES["busy"])
busy_rows = app._device_verb_entries(DEVICE_ID)
cancel = [label for key, label, ok in busy_rows if key == "disconnect" and ok]
check("(b) mid-pair, Disconnect reads as CANCEL",
      cancel == [A.DEVICE_VERB_CANCEL_LABEL], str(cancel))
drive(app, STATES["connected"])
live_rows = dict((key, label) for key, label, ok in
                 app._device_verb_entries(DEVICE_ID) if ok)
check("(b) ...but on a live link it still reads Disconnect",
      live_rows["disconnect"].startswith("Disconnect"),
      live_rows["disconnect"])
check("(b) Unpair names the ~25s guest command (forget-hid is a 25s ssh)",
      "25s" in live_rows["unpair"], live_rows["unpair"])
every_label, offered_label = [], []
for state in STATES.values():
    drive(app, state)
    for _key, label, ok in app._device_verb_entries(DEVICE_ID):
        every_label.append(label)
        if ok:
            offered_label.append(label)
check("(b) no verb claims to restart input -- none of them writes config, so "
      "none of them changes portal_signature",
      not [label for label in every_label if "restarts input" in label],
      str(every_label))
# The in-flight entries are excluded on purpose: they are a present participle
# reporting a wait already underway, not an offer with a price on it.
check("(b) ...and every OFFERED verb names its wait",
      all("(" in label for label in offered_label), str(offered_label))


# ===========================================================================
# (c) the menu and the card cannot disagree
# ===========================================================================
print("\n---- (c) menu vs card, every state ----")

check("(c) both surfaces read the same facts producer",
      any(isinstance(n, ast.Call)
          and _dotted(n.func) == "self._device_verb_facts"
          for n in ast.walk(_method("App", "_apply_device_rows")))
      and any(isinstance(n, ast.Call)
              and _dotted(n.func) == "self._device_verb_facts"
              for n in ast.walk(ENTRIES)))
check("(c) both surfaces call the same offer function",
      any(isinstance(n, ast.Call) and _dotted(n.func) == "device_verb_offer"
          for n in ast.walk(_method("App", "_apply_device_rows")))
      and any(isinstance(n, ast.Call) and _dotted(n.func) == "device_verb_offer"
              for n in ast.walk(ENTRIES)))

problems = agreement_problems(StubApp())
check("(c) the menu and the card agree in every state",
      not problems, "\n      ".join(problems))


# ===========================================================================
# (d) every entry that can open a modal is deferred
# ===========================================================================
print("\n---- (d) deferral ----")

inline = []
for call in ast.walk(FILLER):
    if not (isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr in ("add_command", "add_cascade")):
        continue
    for keyword in call.keywords:
        if keyword.arg != "command":
            continue
        if not (isinstance(keyword.value, ast.Call)
                and _dotted(keyword.value.func) == "self._deferred"):
            inline.append(call.lineno)
check("(d) every command in the verb section is built through _deferred",
      not inline, f"inline command(s) at line(s) {inline}")

# ...and structure is not enough: INVOKE every entry and prove nothing ran.
ran = []
for state_name, state in STATES.items():
    probe = StubApp()
    drive(probe, state)
    menu = RecordingMenu()
    probe._fill_device_verb_entries(menu, DEVICE_ID)
    live_commands = [row["command"] for row in menu.rows if row["command"]]
    for command in live_commands:
        command()
    if probe.clicked:
        ran.append(f"{state_name}: {probe.clicked} ran INLINE")
    if len(probe.root.queued) != len(live_commands):
        ran.append(f"{state_name}: {len(live_commands)} commands, "
                   f"{len(probe.root.queued)} queued on root.after")
    for queued in probe.root.queued:
        queued()
    if len(probe.clicked) != len(live_commands):
        ran.append(f"{state_name}: after() did not reach the handler")
check("(d) invoking every entry queues on root.after and runs NOTHING inline",
      not ran, "\n      ".join(ran))

# The command really is the handler DEVICE_VERB_HANDLERS names, for the right
# device -- an entry wired to the wrong verb is silent until it fires.
probe = StubApp()
drive(probe, STATES["paired-idle"])
menu = RecordingMenu()
rows = probe._fill_device_verb_entries(menu, DEVICE_ID)
live_labels = [row["label"] for row in menu.rows if row["command"]]
for row in menu.rows:
    if row["command"]:
        row["command"]()
for queued in probe.root.queued:
    queued()
check("(d) each entry calls its OWN verb's handler, on THIS device",
      probe.clicked == [(key, DEVICE_ID) for key, _l, ok in rows if ok],
      f"{probe.clicked} vs {[(k, DEVICE_ID) for k, _l, ok in rows if ok]}")
check("(d) the menu is written FROM the returned rows -- same labels, "
      "same order",
      live_labels == [f"   {label}" for _k, label, ok in rows if ok],
      f"{live_labels}")


# ===========================================================================
# (e) the device is named -- the menu is per-DISPLAY, the verbs are per-DEVICE
# ===========================================================================
print("\n---- (e) the device is named ----")

named = naming_problems(StubApp())
check("(e) every state's verb section carries a disabled entry naming the "
      "device -- right-clicking one of the Mac's three panels and choosing "
      "Unpair unpairs the Mac",
      not named, "\n      ".join(named))

probe = StubApp()
drive(probe, STATES["paired-idle"])
menu = RecordingMenu()
probe._fill_device_verb_entries(menu, DEVICE_ID)
check("(e) the section is separated from the display entries above it",
      menu.rows and menu.rows[0]["kind"] == "separator",
      str(menu.rows[:1]))
check("(e) the name comes FIRST in the section, before any verb",
      menu.rows[1]["state"] == "disabled"
      and DEVICE_NAME in menu.rows[1]["label"],
      str(menu.rows[1]))


# ===========================================================================
# (f) MUTATION -- break the shipped source and watch the checks go red
# ===========================================================================
print("\n---- (f) mutation ----")


def mutant(node, old, new, extra=None):
    """Recompile ONE shipped method with a single edit, bound to a fresh stub
    class. The base is the real source, so this is the shipping code with a
    defect in it rather than a hand-written imitation of one."""
    src = _block(node)
    assert old in src, f"mutation anchor missing: {old!r}"
    namespace = dict(A.__dict__)
    namespace.update(extra or {})
    exec(compile(src.replace(old, new, 1), "<mutant>", "exec"), namespace)

    class Mutant(StubApp):
        pass
    setattr(Mutant, node.name, namespace[node.name])
    return Mutant


# 1. The menu carries its OWN copy of the gate. This is the defect the whole
#    one-builder rule exists to prevent, and (c) must catch it.
M1 = mutant(
    ENTRIES, "offered = device_verb_offer(facts)",
    "offered = {k: bool(_COPY[k](facts)) for k in DEVICE_VERBS}",
    extra={"_COPY": dict(A.DEVICE_VERB_GATES, unpair=lambda f: False)})
caught = agreement_problems(M1())
check("(f) a menu with its own gate copy is CAUGHT by (c)",
      bool(caught), "the agreement check passed against a broken menu")

# 2. The menu drops the in-flight override -- the surface you right-click to
#    check on a pair attempt becomes the one surface that never mentions it.
M2 = mutant(ENTRIES, "if key == facts[\"verb\"]:", "if False:")
caught = agreement_problems(M2())
check("(f) dropping the in-flight override is CAUGHT by (c)",
      bool(caught), "the agreement check passed with no busy state in the menu")

# 3. The header stops naming the device.
M3 = mutant(
    FILLER, "label=f\"{record.get('name', device_id)} — connection\"",
    "label=\"Connection\"")
caught = naming_problems(M3())
check("(f) an unnamed verb section is CAUGHT by (e)",
      bool(caught), "the naming check passed against an unnamed section")

# And the harness itself is honest: the UNMUTATED method, recompiled the same
# way, still passes. Otherwise the three results above would only prove that
# recompiling breaks things.
CONTROL = mutant(ENTRIES, "offered = device_verb_offer(facts)",
                 "offered = device_verb_offer(facts)  # unchanged")
check("(f) the harness's control -- the same method, recompiled unmutated, "
      "still agrees",
      not agreement_problems(CONTROL()))


print()
if fails:
    print(f"{len(fails)} FAILED:")
    for name in fails:
        print("  - " + name)
    sys.exit(1)
print("all checks passed")
