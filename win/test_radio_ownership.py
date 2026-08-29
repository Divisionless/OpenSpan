"""Radios belong to paired devices, and the only radio UI left is a fault.

Doug, 2026-08-28, looking at the Radio options panel while three radios were
live on his desk: *"I am using the three radio system right now. It is not
possible for it to look like that. The radios are not present in the dropdowns.
Honestly this entire section needs redone -- please modernize this -- we don't
need to even have this section because the paired device list should handle all
of this, claiming a radio until it is unpaired."*

He was right twice. The panel was WRONG -- bt_prefs.json on that desk held
``"radio_mode": "single"`` beside three distinct lane MACs, so the app
enumerated no radios at all and printed "Default" in every row -- and it was
the wrong SHAPE: a machine-level settings surface for a device-level fact. What
replaced it:

    * the tree's Radio column is the assignment, and the device's right-click
      menu is the only way to set one. A device HOLDS its radio until it is
      unpaired; forget() releases it.
    * "mode" is not a choice. multi_radio_enabled() reads the layout off the
      assignments themselves, which are the thing that is actually true.
    * Repair radios / Take custody / Use recommended 3-radio layout are fault
      remedies. They live in an inline banner that is packed only while an
      audit has found the fault each one fixes. No fault, nothing on screen.

RUNS HEADLESS AND TOUCHES NOTHING. No Tk root is created: the banner methods
are taken off the real class and run against recorders, the way
test_radio_usb.py already does with the boot-wait methods. threading.Thread is
neutered for the one test that calls forget(), so no guest command is ever
sent, and bt_prefs.json is redirected into a temp directory so the live file on
this desk is never written.

Exit 0 = all pass.
"""
import ast
import inspect
import json
import os
import shutil
import sys
import tempfile
import textwrap
import types

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openspan as A  # noqa: E402

fails = []


def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name
          + ("" if condition or not detail else "\n      " + detail))
    if not condition:
        fails.append(name)


HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = open(os.path.join(HERE, "openspan.py"), encoding="utf-8").read()
TREE = ast.parse(SOURCE)
FUNCS = {node.name: node for node in ast.walk(TREE)
         if isinstance(node, ast.FunctionDef)}


def body(name):
    return ast.unparse(FUNCS[name]) if name in FUNCS else ""


BT_CLASS = next(node for node in ast.walk(TREE)
                if isinstance(node, ast.ClassDef) and node.name == "BtPanel")
BT_INIT_NODE = next(node for node in BT_CLASS.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "__init__")
# NOTE on quoting: ast.unparse renders every string literal single-quoted
# whatever the file says, so a token matched against BT_INIT or a body() uses
# 'single' quotes and a token matched against SOURCE uses "double".
BT_INIT = ast.unparse(BT_INIT_NODE)


# ============================================================================
# 1. the panel is gone
# ============================================================================
print("\n-- the Radio options panel is gone --")

check("no LabelFrame is built inside BtPanel at all",
      "ttk.LabelFrame(self," not in SOURCE and "ttk.LabelFrame(self " not in
      SOURCE, "a Radio options frame is still constructed")
check("the panel's title survives nowhere as widget text",
      'text="Radio options"' not in SOURCE)
for widget in ("mode_combo", "hid_combo", "mac_combo", "scan_combo",
               "radio_note", "radio_usb", "radio_custody_text"):
    check(f"self.{widget} no longer exists", f"self.{widget}" not in SOURCE)
for handler in ("_on_mode_changed", "_on_hid_radio", "_on_mac_radio",
                "_on_scan_radio", "_selected_controller",
                "_set_radio_controls"):
    check(f"{handler} is gone with the widget that called it",
          handler not in FUNCS and f"self.{handler}" not in SOURCE)
check("the Setup combo's two labels are gone from the app entirely",
      "Single radio (recommended)" not in SOURCE
      and "Multiple radios" not in SOURCE)
check("no radio is acted on from a <<ComboboxSelected>> event any more",
      "set-hid-radio.sh" not in BT_INIT
      and "ComboboxSelected" not in BT_INIT)


# ============================================================================
# 2. mode is derived, not chosen -- and the stored answer is still honoured
# ============================================================================
print("\n-- there is no mode to pick --")


def prefs(**over):
    base = {"renames": {}, "blacklist": set(), "radio_mode": "single",
            "radio_assignments": {}, "hid_radio": "", "mac_radio": "",
            "scan_radio": "", "radio_labels": {}}
    base.update(over)
    return base


check("a stored multi is honoured verbatim -- nothing on the multi path can "
      "fall off it",
      A.multi_radio_enabled(prefs(radio_mode="multi")))
check("an empty prefs file is still the single-radio compatibility path",
      not A.multi_radio_enabled(prefs()))
check("one radio, however many slots name it, is one radio",
      not A.multi_radio_enabled(
          prefs(hid_radio="AA:BB:CC:00:00:01",
                mac_radio="AA:BB:CC:00:00:01",
                scan_radio="AA:BB:CC:00:00:01")))
# THE BUG, as a test. Doug's own bt_prefs.json: "single" beside three distinct
# lane MACs. The old reading believed the toggle and enumerated no radios.
check("three distinct lanes IS a three-radio machine, whatever the stale "
      "toggle says",
      A.multi_radio_enabled(
          prefs(hid_radio="58:A0:23:CD:6A:B7",
                mac_radio="AC:A7:F1:29:9F:CB",
                scan_radio="3C:6A:D2:3C:D4:4E")))
check("a per-device claim counts toward the layout too",
      A.multi_radio_enabled(
          prefs(hid_radio="AA:BB:CC:00:00:01",
                radio_assignments={"AA:BB:CC:00:00:20": "AA:BB:CC:00:00:02"})))
check("the derivation is case-insensitive, like every other controller read",
      not A.multi_radio_enabled(
          prefs(hid_radio="aa:bb:cc:00:00:01",
                scan_radio="AA:BB:CC:00:00:01")))
check("radio_mode is still read and still written by the prefs file, so a "
      "machine can be pinned to multi by hand",
      '"radio_mode": prefs.get("radio_mode", "single")' in SOURCE)
check("nothing in the app writes radio_mode any more",
      'self.prefs["radio_mode"] =' not in SOURCE
      and "prefs['radio_mode'] =" not in ast.unparse(TREE))


# ============================================================================
# 3. the Radio column is the assignment path
# ============================================================================
print("\n-- the Radio column, and the right-click that sets it --")

popup = body("_popup")
check("the device tree still declares a Radio column",
      'self.tree.heading("radio", text="Radio")' in SOURCE
      and "'radio'" in BT_INIT)
check("the right-click cascade is still called Assign to radio",
      "m.add_cascade(label='Assign to radio', menu=assign)" in popup)
check("it is gated on the radios actually PRESENT, not on a stored mode",
      "if self._radios:" in popup and "self._multi() and self._radios" not in
      popup)
check("every entry in it goes to the one writer",
      popup.count("self._assign_radio(") >= 2)
check("a radio another device holds is shown, attributed, and not selectable",
      "_radio_holder(controller, ignore=mac)" in popup
      and "held by" in popup
      and "state='disabled' if holder else 'normal'" in popup)
check("the cascade is kept referenced, or Tk collects it out from under the "
      "posted menu", "self.assign_menu = assign" in popup)
rows = body("_apply_rows")
check("the column is painted from the stored claim first, the guest's report "
      "second",
      "assigned = self.prefs['radio_assignments'].get(mac)" in rows
      and "shown_controller = assigned or controller" in rows)
check("_assign_radio is the ONLY writer of an assignment",
      SOURCE.count('self.prefs["radio_assignments"][mac] =') == 1)


# ============================================================================
# 4. assignment persists by controller MAC, and unpairing releases it
# ============================================================================
print("\n-- a device holds its radio until it is unpaired --")

SCRATCH = tempfile.mkdtemp(prefix="esotericos-radio-own-")
_real_prefs_path = A.BT_PREFS
A.BT_PREFS = os.path.join(SCRATCH, "bt_prefs.json")

DEVICE = "AA:BB:CC:00:00:20"
OTHER = "AA:BB:CC:00:00:21"
INTEL = "AA:BB:CC:00:00:01"
TPLINK1 = "AA:BB:CC:00:00:02"
TPLINK2 = "AA:BB:CC:00:00:03"
RADIOS = [{"address": INTEL, "hci": "hci0", "hardware": "Intel Bluetooth"},
          {"address": TPLINK1, "hci": "hci1", "hardware": "TP-Link Bluetooth"},
          {"address": TPLINK2, "hci": "hci2", "hardware": "TP-Link Bluetooth"}]


class NullThread:
    """threading.Thread that never runs its target.

    Used for the one test that calls the real forget(): everything the release
    depends on happens on the UI thread before the worker is started, and the
    worker itself would ssh into the live guest.
    """

    def __init__(self, target=None, daemon=None):
        self.target = target

    def start(self):
        return None


class OwnerPanel:
    """A BtPanel stand-in for the ownership half. No Tk, no window."""

    _radio_display = A.BtPanel._radio_display
    _radio_label = A.BtPanel._radio_label
    _radio_holder = A.BtPanel._radio_holder
    _device_name = A.BtPanel._device_name
    _controller_for = A.BtPanel._controller_for
    _assign_radio = A.BtPanel._assign_radio
    _lanes_laid_out = A.BtPanel._lanes_laid_out
    _multi = A.BtPanel._multi
    _retry_lock = A.BtPanel._retry_lock
    forget = A.BtPanel.forget

    def __init__(self, selected=DEVICE, **over):
        self.app = None
        self.prefs = prefs(**over)
        self._radios = list(RADIOS)
        self._device_radios = {}
        self._seen = {DEVICE: ("Onn buds", "audio-card", TPLINK1),
                      OTHER: ("Keychron", "input-keyboard", "")}
        self._connected = set()
        self._conn_busy = False
        self.logged = []
        self.refreshed = 0
        self._selected = selected

    def _sel_mac(self):
        return self._selected

    def _log(self, message):
        self.logged.append(message)

    def refresh(self, quiet=False):
        self.refreshed += 1


panel = OwnerPanel()
panel._assign_radio(TPLINK1)
check("assigning writes the claim against the DEVICE's MAC",
      panel.prefs["radio_assignments"] == {DEVICE: TPLINK1},
      repr(panel.prefs["radio_assignments"]))
check("and it says the claim lasts until the device is unpaired",
      any("until it is unpaired" in line for line in panel.logged),
      repr(panel.logged))
saved = json.load(open(A.BT_PREFS, encoding="utf-8"))
check("the claim is persisted, keyed by device MAC and holding a stable "
      "CONTROLLER MAC -- never an hci number",
      saved["radio_assignments"] == {DEVICE: TPLINK1},
      repr(saved.get("radio_assignments")))
check("and it survives a reload",
      A.load_bt_prefs()["radio_assignments"] == {DEVICE: TPLINK1})

held = OwnerPanel(selected=OTHER, radio_assignments={DEVICE: TPLINK1})
held._assign_radio(TPLINK1)
check("a second device cannot take a radio the first is holding",
      held.prefs["radio_assignments"] == {DEVICE: TPLINK1},
      repr(held.prefs["radio_assignments"]))
check("...and it is told who has it, by name",
      any("Onn buds" in line for line in held.logged), repr(held.logged))
held._assign_radio(TPLINK2)
check("a free radio is granted",
      held.prefs["radio_assignments"][OTHER] == TPLINK2)

auto = OwnerPanel(radio_assignments={DEVICE: TPLINK1})
auto._assign_radio("")
check("Automatic releases the claim without touching anyone else's",
      DEVICE not in auto.prefs["radio_assignments"])

_real_thread = A.threading.Thread
try:
    A.threading.Thread = NullThread
    gone = OwnerPanel(radio_assignments={DEVICE: TPLINK1, OTHER: TPLINK2})
    gone._device_radios = {DEVICE: TPLINK1}
    gone.forget()
    check("UNPAIRING RELEASES THE RADIO",
          gone.prefs["radio_assignments"] == {OTHER: TPLINK2},
          repr(gone.prefs["radio_assignments"]))
    check("and drops the live mapping with it",
          DEVICE not in gone._device_radios)
    check("...and says so, so a radio never goes quiet",
          any("released its radio" in line for line in gone.logged),
          repr(gone.logged))
    check("the release is persisted, not merely in memory",
          A.load_bt_prefs()["radio_assignments"] == {OTHER: TPLINK2})
    check("a device with no claim unpairs without inventing one",
          OwnerPanel(selected=OTHER).forget() is None)
finally:
    A.threading.Thread = _real_thread

check("the release happens on the UI thread, before the guest is asked -- a "
      "release that waited on the guest would strand the radio whenever the "
      "guest did not answer",
      body("forget").index("radio_assignments") < body("forget").index(
          "def work"))


# ============================================================================
# 5. the banner: only on a fault, and it names it
# ============================================================================
print("\n-- the fault banner --")


class Var:
    def __init__(self):
        self._value = ""

    def set(self, value):
        self._value = value

    def get(self):
        return self._value


class Widget:
    """Records what the packer would have been told. Nothing is drawn."""

    made = 0

    def __init__(self, tag):
        Widget.made += 1
        self._path = f".btpanel.{tag}{Widget.made}"
        self.manager = ""
        self.kwargs = {}
        self.text = ""

    def pack(self, **kwargs):
        self.manager = "pack"
        self.kwargs = kwargs

    def pack_forget(self):
        self.manager = ""

    def config(self, text=None, **_ignored):
        if text is not None:
            self.text = text

    def cget(self, option):
        return self.text if option == "text" else ""

    def state(self, spec=None):
        return ()

    def __str__(self):
        return self._path


class BannerPanel:
    """The banner half of BtPanel, off the real class, with no Tk under it."""

    _build_fault_row = None          # needs real widgets; rows are made here
    _set_fault = A.BtPanel._set_fault
    _clear_fault = A.BtPanel._clear_fault
    _show_faults = A.BtPanel._show_faults
    _radio_usb_apply = A.BtPanel._radio_usb_apply
    _custody_apply_text = A.BtPanel._custody_apply_text
    _audit_radio_layout = A.BtPanel._audit_radio_layout
    _lanes_laid_out = A.BtPanel._lanes_laid_out

    def __init__(self, radios=(), **over):
        self.app = types.SimpleNamespace(ui=lambda fn: fn())
        self.prefs = prefs(**over)
        self._radios = list(radios)
        self._faults = {}
        self._fault_shown = False
        self._fault_rows = {}
        self.fault_box = Widget("faultbox")
        self._info_lbl = Widget("info")
        for key in ("usb", "custody", "layout"):
            self._fault_rows[key] = {
                "row": Widget(key), "text": Var(), "button": Widget(key + "btn")}
        self.reclaim_btn = self._fault_rows["usb"]["button"]
        self.custody_btn = self._fault_rows["custody"]["button"]
        self.recommended_btn = self._fault_rows["layout"]["button"]

    def shown(self):
        return {key for key, row in self._fault_rows.items()
                if row["row"].manager == "pack"}


cold = BannerPanel()
check("a cold boot shows NOTHING -- not a frame, not a reassuring sentence",
      cold.fault_box.manager == "" and cold.shown() == set()
      and cold._fault_shown is False)
cold._radio_usb_apply("The VM is not running, so it holds no radios.", 0)
check("a VM that is not up yet is not a fault",
      cold.fault_box.manager == "" and cold.shown() == set())
cold._radio_usb_apply("All 3 present radios are attached.", 0)
check("a healthy desk is not a fault either",
      cold.fault_box.manager == "" and cold.shown() == set())
check("...and the button still learns the count it would carry",
      cold.reclaim_btn.text == "Repair radios", cold.reclaim_btn.text)

busy_desk = BannerPanel()
busy_desk._radio_usb_apply(
    "Busy on Windows: TP-Link #2. Repair will send exactly one attach "
    "request per radio.", 1)
check("A RADIO THE VM DOES NOT HOLD RAISES THE BANNER",
      busy_desk.fault_box.manager == "pack"
      and busy_desk.shown() == {"usb"})
check("and the banner NAMES the fault rather than describing the app",
      "Busy on Windows: TP-Link #2"
      in busy_desk._fault_rows["usb"]["text"].get())
check("the remedy button is the one action that fixes it, and says how many "
      "radios it covers",
      busy_desk.reclaim_btn.text == "Repair 1 radio", busy_desk.reclaim_btn.text)
busy_desk._radio_usb_apply("Busy on Windows: two of them.", 2)
check("the count is plural when it should be",
      busy_desk.reclaim_btn.text == "Repair 2 radios")
check("the banner packs itself ABOVE the device list, not at the end of the "
      "panel",
      busy_desk.fault_box.kwargs.get("before") is busy_desk._info_lbl)
busy_desk._radio_usb_apply("All 3 present radios are attached.", 0)
check("repairing it takes the banner away again, frame and all",
      busy_desk.fault_box.manager == "" and busy_desk.shown() == set())

# The progress line _reclaim_radios emits mid-repair carries a repairable count
# of 0. Obeying that literally would unpack the banner -- and the button being
# waited on -- halfway through the repair.
mid = BannerPanel()
mid._radio_usb_apply("Busy on Windows: TP-Link #2.", 1)
A.set_button_busy(mid.reclaim_btn, "Repairing radios…")
mid._radio_usb_apply("Auditing ownership; 1 radio(s) permit one attempt…", 0)
check("a remedy that is RUNNING is never unpacked underneath itself",
      mid.shown() == {"usb"} and mid.fault_box.manager == "pack")
A.clear_button_busy(mid.reclaim_btn)
mid._radio_usb_apply("All 3 present radios are attached.", 0)
check("...and it goes when the job that owned it is finished",
      mid.shown() == set())

cust = BannerPanel()
cust._custody_apply_text("")
check("an audit that finds nothing shows nothing", cust.shown() == set())
cust._custody_apply_text(
    "Intel AX211 — PHANTOM: the device node is registered but not enumerated")
check("a phantom radio raises the custody fault, and quotes the audit",
      cust.shown() == {"custody"}
      and "PHANTOM" in cust._fault_rows["custody"]["text"].get())
cust._custody_apply_text("")
check("and a clean re-audit clears it", cust.shown() == set()
      and cust.fault_box.manager == "")

both = BannerPanel()
both._radio_usb_apply("Busy on Windows: TP-Link #2.", 1)
both._custody_apply_text("Intel AX211 — PHANTOM")
check("two faults are two rows in the one banner, each with its own action",
      both.shown() == {"usb", "custody"})
both._clear_fault("usb")
check("clearing one leaves the other standing",
      both.shown() == {"custody"} and both.fault_box.manager == "pack")

laid = BannerPanel(radios=RADIOS, hid_radio=INTEL, mac_radio=TPLINK1,
                   scan_radio=TPLINK2)
laid._audit_radio_layout()
check("three radios on three lanes is not a fault", laid.shown() == set())
sharing = BannerPanel(radios=RADIOS, hid_radio=INTEL, mac_radio=INTEL,
                      scan_radio=TPLINK2)
sharing._audit_radio_layout()
check("THREE RADIOS WITH TWO LANES SHARING ONE IS",
      sharing.shown() == {"layout"})
check("and the sentence says exactly that",
      "sharing a radio" in sharing._fault_rows["layout"]["text"].get(),
      sharing._fault_rows["layout"]["text"].get())
missing = BannerPanel(radios=RADIOS, hid_radio=INTEL, mac_radio=TPLINK1,
                      scan_radio="AA:BB:CC:00:00:09")
missing._audit_radio_layout()
check("a lane pointing at a radio that is not here is not laid out",
      missing.shown() == {"layout"})
two = BannerPanel(radios=RADIOS[:2], hid_radio=INTEL, mac_radio="",
                  scan_radio="")
two._audit_radio_layout()
check("two radios have no three-radio layout to be wrong about",
      two.shown() == set())
none_yet = BannerPanel()
none_yet._audit_radio_layout()
check("and neither does a machine that has reported none",
      none_yet.shown() == set())


# ============================================================================
# 6. law 10, and no windows
# ============================================================================
print("\n-- law 10, and no pop-outs --")

row_src = textwrap.dedent(inspect.getsource(A.BtPanel._build_fault_row))
banner_src = row_src + body("_set_fault") + body("_clear_fault") \
    + body("_show_faults")
for token in ("Scrollbar", "yscrollcommand", "xscrollcommand", "tk.Canvas",
              "tk.Text", "tk.Listbox", "Treeview", "yview", "xview"):
    check(f"the banner introduces no {token}", token not in banner_src)
check("the banner is a Frame of Frames and nothing else",
      "tk.Frame(self, bg=BG)" in BT_INIT and "row = tk.Frame(self.fault_box"
      in row_src)
check("it is packed with fill='x' only -- it never absorbs vertical surplus",
      "expand" not in body("_show_faults"))
check("the sentence wraps to the width it is given rather than to a literal",
      "bind_wraplength(label)" in row_src and "wraplength=" not in row_src)

BANNED = {"Toplevel", "messagebox", "filedialog", "simpledialog"}
offenders = []
for node in ast.walk(BT_CLASS):
    hit = ((isinstance(node, ast.Attribute)
            and (node.attr in BANNED
                 or getattr(node.value, "id", None) in BANNED))
           or (isinstance(node, ast.Name) and node.id in BANNED))
    if hit:
        offenders.append(str(getattr(node, "lineno", "?")))
check("nothing in the Bluetooth panel opens an OS window", not offenders,
      ", ".join(offenders))
check("assignment is a tk.Menu, which is what the app already uses for this",
      "tk.Menu(m, tearoff=0" in popup)


# ============================================================================
# 7. construction is read-only
# ============================================================================
print("\n-- nothing acts without a click --")

called = set()
for node in ast.walk(BT_INIT_NODE):
    if isinstance(node, ast.Call):
        try:
            called.add(ast.unparse(node.func))
        except Exception:  # noqa: BLE001
            pass
for action in ("self._reclaim_radios", "self._take_custody",
               "self._use_recommended_radios", "self.repair_radios"):
    check(f"{action} is wired as a command, never CALLED while building",
          action not in called, repr(sorted(c for c in called
                                            if action in c)))
check("the three remedies are still reached only through their buttons",
      BT_INIT.count("self._reclaim_radios") == 1
      and BT_INIT.count("self._take_custody") == 1
      and BT_INIT.count("self._use_recommended_radios") == 1)
check("the launch audits are still scheduled, not run inline",
      "self.after(1200, self._radio_usb_check)" in SOURCE
      and "self.after(1800, self._custody_check)" in SOURCE)
check("the two-click arm-then-apply on custody is untouched",
      "self._custody_armed = False" in BT_INIT
      and "apply=armed" in body("_take_custody"))


A.BT_PREFS = _real_prefs_path
shutil.rmtree(SCRATCH, ignore_errors=True)

print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
