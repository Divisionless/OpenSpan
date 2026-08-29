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
    """The banner half of BtPanel, off the real class, with no Tk under it.

    after / after_cancel / winfo_exists are recorders, exactly the way
    test_radio_usb.py's FakePanel drives the boot wait, so the CONFIRMATION
    timer can be stepped by hand with no Tk root anywhere. `audits` is the
    script of findings the next re-audits will report, so fire() drives a real
    second observation through the real gate instead of simulating one.
    """

    FAULT_CONFIRM_MS = A.BtPanel.FAULT_CONFIRM_MS
    FAULT_CONFIRM_TRIES = A.BtPanel.FAULT_CONFIRM_TRIES
    _build_fault_row = None          # needs real widgets; rows are made here
    _set_fault = A.BtPanel._set_fault
    _clear_fault = A.BtPanel._clear_fault
    _show_faults = A.BtPanel._show_faults
    _radio_usb_apply = A.BtPanel._radio_usb_apply
    _custody_apply_text = A.BtPanel._custody_apply_text
    _custody_report = A.BtPanel._custody_report
    _confirm_faults = A.BtPanel._confirm_faults
    _confirm_cancel = A.BtPanel._confirm_cancel
    _confirm_retry = A.BtPanel._confirm_retry
    _confirm_fire = A.BtPanel._confirm_fire
    _audit_radio_layout = A.BtPanel._audit_radio_layout
    _lanes_laid_out = A.BtPanel._lanes_laid_out

    def __init__(self, radios=(), audits=(), alive=True, app=True, **over):
        self.app = (types.SimpleNamespace(ui=lambda fn: fn())
                    if app else None)
        self.prefs = prefs(**over)
        self._radios = list(radios)
        self._faults = {}
        self._fault_shown = False
        self._fault_rows = {}
        self._fault_seen = {}
        self._fault_provisional = {}
        self._confirm_job = None
        self._confirm_left = self.FAULT_CONFIRM_TRIES
        self.audits = list(audits)   # findings the scripted re-audits report
        self.custody = 0             # times the custody audit was re-taken
        self.scheduled = []          # live jobs: (ms, callback, job)
        self.history = []            # every after() ever made
        self.cancelled = []
        self.alive = alive
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

    def _custody_check(self):
        self.custody += 1
        if self.audits:
            self._custody_report(self.audits.pop(0))

    def after(self, ms, callback):
        job = "after#%d" % (len(self.history) + 1)
        self.history.append((ms, job))
        self.scheduled.append((ms, callback, job))
        return job

    def after_cancel(self, job):
        self.scheduled = [row for row in self.scheduled if row[2] != job]
        self.cancelled.append(job)

    def winfo_exists(self):
        if self.alive == "destroyed":
            raise A.tk.TclError('bad window path name ".!btpanel"')
        return 1 if self.alive else 0

    def fire(self):
        """Run the pending re-audit the way Tk's after() would."""
        _ms, callback, _job = self.scheduled.pop(0)
        callback()


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
laid._audit_radio_layout()
check("three radios on three lanes is not a fault", laid.shown() == set())
sharing = BannerPanel(radios=RADIOS, hid_radio=INTEL, mac_radio=INTEL,
                      scan_radio=TPLINK2)
sharing._audit_radio_layout()
sharing._audit_radio_layout()
check("THREE RADIOS WITH TWO LANES SHARING ONE IS",
      sharing.shown() == {"layout"})
check("and the sentence says exactly that",
      "sharing a radio" in sharing._fault_rows["layout"]["text"].get(),
      sharing._fault_rows["layout"]["text"].get())
missing = BannerPanel(radios=RADIOS, hid_radio=INTEL, mac_radio=TPLINK1,
                      scan_radio="AA:BB:CC:00:00:09")
missing._audit_radio_layout()
missing._audit_radio_layout()
check("a lane pointing at a radio that is not here is not laid out",
      missing.shown() == {"layout"})
two = BannerPanel(radios=RADIOS[:2], hid_radio=INTEL, mac_radio="",
                  scan_radio="")
two._audit_radio_layout()
two._audit_radio_layout()
check("two radios have no three-radio layout to be wrong about",
      two.shown() == set())
none_yet = BannerPanel()
none_yet._audit_radio_layout()
none_yet._audit_radio_layout()
check("and neither does a machine that has reported none",
      none_yet.shown() == set())


# ============================================================================
# 5b. A FAULT IS CONFIRMED BEFORE IT IS SHOWN
# ============================================================================
# Doug restarted on 2026-08-28 and the banner said two working dongles were
# PHANTOM. Measured minutes later, all three read verdict=WINDOWS-OWNED,
# present=False, proxy_present=True, vbox_state='Captured', vm_holds=True --
# the normal runtime capture custody_fault() lets past. The audit fires 1.8s
# after launch; at that instant the two VBoxUSB proxies had not stood up, so
# verdict() fell through to its last branch and PHANTOM was frozen into a red
# banner. Same class as v3.156: the VM being UP is not the USB captures having
# LANDED, so the vm_running() guard cannot catch it.
#
# The fix is not a better instant: it is refusing to believe one photograph.
print("\n-- a fault seen once is shown to nobody --")

PHANTOM_ID = ("USB\\VID_2357&PID_0604\\3C6AD23CD44E", "PHANTOM")
PHANTOM_LINE = ("Managed Mac’s dongle — PHANTOM: the device node is registered "
                "but not enumerated")
OTHER_ID = ("USB\\VID_2357&PID_0604\\ACA7F1299FCB", "PHANTOM")
OTHER_LINE = "Managed Laptop’s dongle — PHANTOM: the device node is registered"
REBIND_ID = ("USB\\VID_8087&PID_0032\\5&2F3A1B1&0&14", "WINDOWS-OWNED")
REBIND_LINE = "Intel AX211 — Windows-owned (bthusb)"

boot = BannerPanel()
boot._custody_report({PHANTOM_ID: PHANTOM_LINE, OTHER_ID: OTHER_LINE})
check("THE COLD-BOOT SNAPSHOT RAISES NOTHING -- one sighting is provisional",
      boot.shown() == set() and boot.fault_box.manager == ""
      and boot._fault_rows["custody"]["text"].get() == "",
      repr(boot._fault_rows["custody"]["text"].get()))
check("but it is remembered, both radios of it",
      boot._fault_provisional["custody"] == {PHANTOM_ID, OTHER_ID},
      repr(boot._fault_provisional.get("custody")))
check("and a re-audit is scheduled without anyone clicking anything",
      len(boot.scheduled) == 1
      and boot.scheduled[0][0] == A.BtPanel.FAULT_CONFIRM_MS,
      repr(boot.history))

# the settled reading, six seconds later: all three radios healthy
boot.audits = [{}]
boot.fire()
check("A HEALTHY RE-AUDIT CLEARS THE PROVISIONAL STATE, and still shows "
      "nothing -- this is the false positive, gone",
      boot.custody == 1 and boot.shown() == set()
      and boot._fault_provisional["custody"] == set()
      and boot._fault_seen["custody"] == {}, repr(boot._fault_seen))
check("and with nothing outstanding the re-audit chain stops",
      boot.scheduled == [] and boot._confirm_job is None,
      repr(boot.scheduled))

real = BannerPanel(audits=[{PHANTOM_ID: PHANTOM_LINE}])
real._custody_report({PHANTOM_ID: PHANTOM_LINE})
check("a real fault is still silent on its first sighting",
      real.shown() == set())
real.fire()
check("THE SAME FAULT ON THE SAME RADIO TWICE IS THE REAL ONE, and it is "
      "raised", real.shown() == {"custody"}
      and PHANTOM_LINE in real._fault_rows["custody"]["text"].get(),
      repr(real._fault_rows["custody"]["text"].get()))
check("confirming costs exactly one interval, so a real fault is still prompt",
      real.history == [(A.BtPanel.FAULT_CONFIRM_MS, "after#1")],
      repr(real.history))
check("the interval is seconds apart, not a busy loop",
      A.BtPanel.FAULT_CONFIRM_MS >= 3000, str(A.BtPanel.FAULT_CONFIRM_MS))
check("and it is not so long that a real fault sits invisible -- one "
      "confirmation inside a quarter of the boot wait",
      A.BtPanel.FAULT_CONFIRM_MS * 2
      <= A.BtPanel.RADIO_WAIT_MS * A.BtPanel.RADIO_WAIT_TRIES / 4,
      f"{A.BtPanel.FAULT_CONFIRM_MS}ms")
real._custody_report({})
check("and one healthy audit afterwards takes it away again",
      real.shown() == set())

flap = BannerPanel(audits=[{}])
flap._custody_report({PHANTOM_ID: PHANTOM_LINE})   # once
flap.fire()                                        # healthy: forgotten
flap._custody_report({PHANTOM_ID: PHANTOM_LINE})   # faulted again: once more
check("A HEALTHY READING BETWEEN THE TWO IS NOT A CONFIRMATION -- the count "
      "starts over rather than accumulating across a gap",
      flap.shown() == set()
      and flap._fault_provisional["custody"] == {PHANTOM_ID},
      repr(flap._fault_provisional.get("custody")))

mixed = BannerPanel(audits=[{PHANTOM_ID: PHANTOM_LINE, REBIND_ID: REBIND_LINE}])
mixed._custody_report({PHANTOM_ID: PHANTOM_LINE})
mixed.fire()
check("a second radio appearing on the confirming audit is itself only "
      "provisional -- only the twice-seen one reaches the banner",
      mixed.shown() == {"custody"}
      and mixed._fault_rows["custody"]["text"].get() == PHANTOM_LINE,
      repr(mixed._fault_rows["custody"]["text"].get()))

changed = BannerPanel(audits=[{(PHANTOM_ID[0], "WINDOWS-OWNED"): REBIND_LINE}])
changed._custody_report({PHANTOM_ID: PHANTOM_LINE})
changed.fire()
check("a radio whose FAULT changed between audits starts over instead of "
      "inheriting the other fault's confirmation", changed.shown() == set(),
      repr(changed._fault_seen.get("custody")))

# The Windows-Update re-bind is not transient, so confirming it costs one
# interval and removes a whole class of false alarm.
rebind = BannerPanel(audits=[{REBIND_ID: REBIND_LINE}])
rebind._custody_report({REBIND_ID: REBIND_LINE})
check("the WINDOWS-OWNED re-bind fault goes through the same gate",
      rebind.shown() == set())
rebind.fire()
check("...and is raised on the second audit like any other",
      rebind.shown() == {"custody"})

# The layout fault reads only local state, so its second look costs the
# interval and nothing else -- and it catches a half-landed refresh.
half = BannerPanel(radios=RADIOS, hid_radio=INTEL, mac_radio=INTEL,
                   scan_radio=TPLINK2)
half._audit_radio_layout()
check("the layout fault is provisional on its first audit too",
      half.shown() == set()
      and half._fault_provisional["layout"] == {"lanes"},
      repr(half._fault_provisional.get("layout")))
check("and it schedules its own re-audit",
      len(half.scheduled) == 1
      and half.scheduled[0][0] == A.BtPanel.FAULT_CONFIRM_MS)
half.fire()
check("which raises it, because a layout that is wrong stays wrong",
      half.shown() == {"layout"} and half.custody == 0,
      f"{half.shown()} / custody={half.custody}")
half.prefs["mac_radio"] = TPLINK1
half._audit_radio_layout()
check("laying the lanes out clears it with no second opinion needed",
      half.shown() == set())

# The re-audit only re-runs what is actually outstanding.
only_custody = BannerPanel(radios=RADIOS, hid_radio=INTEL, mac_radio=TPLINK1,
                           scan_radio=TPLINK2)
only_custody._custody_report({PHANTOM_ID: PHANTOM_LINE})
only_custody.audits = [{}]
only_custody.fire()
check("a re-audit re-runs the audit that is waiting and no other",
      only_custody.custody == 1 and only_custody._faults == {},
      str(only_custody.custody))


# ============================================================================
# 5c. the re-audit is BOUNDED, exactly the way the boot wait is
# ============================================================================
print("\n-- the confirmation cannot become a poller --")

budget = A.BtPanel.FAULT_CONFIRM_MS * A.BtPanel.FAULT_CONFIRM_TRIES
check("the whole confirmation budget fits inside the boot wait, so a "
      "confirmation that starts when the VM answers still finishes",
      budget <= A.BtPanel.RADIO_WAIT_MS * A.BtPanel.RADIO_WAIT_TRIES,
      f"{budget}ms")
check("and it is a countdown of tries, not a deadline that can be refilled",
      "self._confirm_left -= 1" in SOURCE
      and SOURCE.count("self._confirm_left = self.FAULT_CONFIRM_TRIES") == 1,
      str(SOURCE.count("self._confirm_left = self.FAULT_CONFIRM_TRIES")))

# A fault that flaps -- seen, gone, seen, gone -- is never confirmed and would
# re-audit for the whole session if the budget could be refilled.
forever = BannerPanel(audits=[{OTHER_ID: OTHER_LINE},
                              {PHANTOM_ID: PHANTOM_LINE}] * 40)
forever._custody_report({PHANTOM_ID: PHANTOM_LINE})
for _ in range(A.BtPanel.FAULT_CONFIRM_TRIES + 5):
    if not forever.scheduled:
        break
    forever.fire()
check("A FLAPPING FAULT SPENDS ITS BUDGET AND STOPS, instead of re-auditing "
      "for the session",
      forever.scheduled == [] and forever._confirm_job is None
      and len(forever.history) == A.BtPanel.FAULT_CONFIRM_TRIES,
      f"{len(forever.history)} re-audits")
check("THE BUDGET EXPIRING LEAVES NOTHING SHOWN -- an unconfirmed fault is "
      "never promoted just because the panel stopped looking",
      forever.shown() == set() and forever.fault_box.manager == "",
      repr(forever.shown()))
check("and the countdown is spent, not merely paused",
      forever._confirm_left == 0, str(forever._confirm_left))
# Clicking Repair or Take custody re-audits. Neither may buy more looks.
forever._custody_report({PHANTOM_ID: PHANTOM_LINE})
forever._custody_report({PHANTOM_ID: PHANTOM_LINE})
check("clicking a remedy cannot refill the budget",
      forever.scheduled == []
      and len(forever.history) == A.BtPanel.FAULT_CONFIRM_TRIES,
      f"{len(forever.history)} re-audits")
check("...though two audits it drove itself still confirm the fault, because "
      "the memory outlives the timer",
      forever.shown() == {"custody"}, repr(forever.shown()))

one_chain = BannerPanel()
one_chain._custody_report({PHANTOM_ID: PHANTOM_LINE})
pending = one_chain._confirm_job
one_chain._custody_report({PHANTOM_ID: PHANTOM_LINE, OTHER_ID: OTHER_LINE})
check("a second provisional finding does not stack a second pending re-audit",
      len(one_chain.history) == 1 and one_chain._confirm_job == pending,
      repr(one_chain.history))
one_chain._custody_report({})
check("an audit with nothing outstanding cancels the pending re-audit",
      one_chain.cancelled == [pending] and one_chain._confirm_job is None
      and one_chain.scheduled == [], repr(one_chain.cancelled))


# ============================================================================
# 5d. threading, and a panel that is not there any more
# ============================================================================
print("\n-- workers never touch Tk, and a dead panel is not an error --")

confirm_src = {name: textwrap.dedent(inspect.getsource(getattr(A.BtPanel, name)))
               for name in ("_custody_report", "_custody_check",
                            "_confirm_faults", "_confirm_cancel",
                            "_confirm_retry", "_confirm_fire")}
check("the custody audit still does its work on a worker thread",
      "threading.Thread(target=work, daemon=True).start()"
      in confirm_src["_custody_check"])
check("and the worker itself neither schedules nor cancels anything",
      "self.after(" not in confirm_src["_custody_check"]
      and "after_cancel" not in confirm_src["_custody_check"])
check("the worker reaches the banner only through app.ui",
      "self.app.ui(apply)" in confirm_src["_custody_report"]
      and "_set_fault" not in confirm_src["_custody_check"])


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return None if base is None else base + "." + node.attr
    return None


for _name, _call in (("_confirm_cancel", "after_cancel"),
                     ("_confirm_retry", "after")):
    _outer = ast.parse(confirm_src[_name]).body[0]
    _closure = next((node.args[0].id for node in ast.walk(_outer)
                     if isinstance(node, ast.Call)
                     and _dotted(node.func) == "self.app.ui"
                     and node.args and isinstance(node.args[0], ast.Name)),
                    None)
    _inner = next((node for node in _outer.body
                   if isinstance(node, ast.FunctionDef)
                   and node.name == _closure), None)
    check(f"{_name} makes its self.{_call}() call on the UI side, inside the "
          "closure app.ui runs -- a background after() racing the UI thread is "
          "the interpreter-level crash App.ui exists to prevent",
          _inner is not None
          and any(_dotted(node.func) == "self." + _call
                  for node in ast.walk(_inner)
                  if isinstance(node, ast.Call)), str(_closure))
for _name in ("_confirm_cancel", "_confirm_retry"):
    check(f"{_name} guards the TclError of a panel that is already gone",
          "except tk.TclError:" in confirm_src[_name])
check("_confirm_fire asks winfo_exists before it audits anything, and "
      "survives the TclError of a widget Tk has forgotten",
      "self.winfo_exists()" in confirm_src["_confirm_fire"]
      and "except tk.TclError:" in confirm_src["_confirm_fire"])

destroyed = BannerPanel(alive="destroyed", audits=[{PHANTOM_ID: PHANTOM_LINE}])
destroyed._custody_report({PHANTOM_ID: PHANTOM_LINE})
destroyed._confirm_job = "after#9"
destroyed._confirm_fire()
check("A DESTROYED PANEL ends the confirmation instead of raising out of "
      "after()",
      destroyed.custody == 0 and destroyed._confirm_job is None
      and destroyed.shown() == set())
absent_panel = BannerPanel(alive=False, audits=[{PHANTOM_ID: PHANTOM_LINE}])
absent_panel._custody_report({PHANTOM_ID: PHANTOM_LINE})
absent_panel._confirm_fire()
check("a panel that no longer exists starts no further audit",
      absent_panel.custody == 0)
forgotten = BannerPanel()
forgotten._confirm_job = "after#1"
forgotten.after_cancel = lambda job: (_ for _ in ()).throw(
    A.tk.TclError('bad window path name ".!btpanel"'))
forgotten._confirm_cancel()
check("cancelling a job Tk has already forgotten is not an error",
      forgotten._confirm_job is None)
racing = BannerPanel()
racing.after = lambda ms, callback: (_ for _ in ()).throw(
    A.tk.TclError('bad window path name ".!btpanel"'))
racing._custody_report({PHANTOM_ID: PHANTOM_LINE})
check("a panel destroyed AS the re-audit is scheduled leaves no dangling job",
      racing._confirm_job is None)
unmarshalled = BannerPanel(app=False)
unmarshalled._custody_report({PHANTOM_ID: PHANTOM_LINE})
check("with no app.ui to marshal through, the confirmation touches no Tk at "
      "all", unmarshalled.history == [] and unmarshalled.shown() == set()
      and unmarshalled._fault_seen == {}, repr(unmarshalled.history))


# ============================================================================
# 5e. the guest-side evidence outranks the host-side node read
# ============================================================================
# The Repair check and the custody audit disagreed on screen: Repair asked what
# the VM holds and reported nothing wrong, while custody called two of the same
# radios PHANTOM. They read the SAME sweep -- RadioCustody.radio_state() is
# read_radio_state() -- so the contradiction was internal, and one side of it is
# strictly better evidence: VirtualBox cannot be delivering a device to the
# guest that is "registered but not enumerated".
print("\n-- a radio the VM is holding is not a phantom --")

import radio_custody as RC  # noqa: E402

check("A RADIO THE VM HOLDS IS NEVER A FAULT, whatever the node read says",
      not A.custody_fault(
          {"verdict": RC.PHANTOM, "present": False, "proxy_present": False,
           "vm_holds": True}, RC))
check("...and that is the exact cold-boot row that produced the false banner",
      not A.custody_fault(
          {"verdict": RC.PHANTOM, "present": False, "proxy_present": False,
           "vbox_state": "", "vm_holds": True}, RC))
check("a phantom the VM does NOT hold is still a fault, so nothing real was "
      "suppressed",
      A.custody_fault({"verdict": RC.PHANTOM, "vm_holds": False}, RC))
check("and a phantom with no VM opinion at all is still a fault",
      A.custody_fault({"verdict": RC.PHANTOM}, RC))
check("the re-bind fault already required the same thing, so PHANTOM now "
      "agrees with it instead of being the exception",
      A.custody_fault({"verdict": RC.WINDOWS_OWNED, "present": True,
                       "vm_holds": False}, RC)
      and not A.custody_fault({"verdict": RC.WINDOWS_OWNED, "present": True,
                               "vm_holds": True}, RC))
check("the evidence is the one already gathered -- no new guest I/O was "
      "invented for it",
      "vm_holds" in body("custody_fault")
      and "ssh_guest" not in body("custody_fault")
      and "ssh_guest" not in body("_custody_check"))


# ============================================================================
# 5f. the console keeps its own counsel
# ============================================================================
# The audit used to print custody_line() for every configured radio on every
# run -- three multi-line paragraphs at launch and three more after every
# custody action. With the audit now re-running to confirm, that would only
# multiply. Doug does not want the spam; the banner says it in the same words.
print("\n-- the audit no longer narrates itself --")

audit_src = body("_custody_check")
check("the per-radio custody paragraph is gone from the console",
      "custody: " + "' + radio_custody.custody_line(row)" not in audit_src
      and "for row in rows:" not in audit_src, audit_src[:0])
check("nothing else in the app prints a custody_line either",
      "self._log('custody: ' + radio_custody.custody_line(row))"
      not in ast.unparse(TREE))
check("an audit that could not RUN still says so, because that has no banner "
      "row to appear in",
      "custody: audit unavailable" in audit_src)
check("custody_line is still what the BANNER quotes, so the wording a fault "
      "is reported in did not change",
      "radio_custody.custody_line(row)" in audit_src)


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
