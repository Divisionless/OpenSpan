# SPDX-License-Identifier: AGPL-3.0-or-later

"""Deterministic inventory tests -- the Phase 1 gate of docs/CONTROL-CENTER.md.

NOTHING HERE TOUCHES THE MACHINE, AND THAT IS ENFORCED RATHER THAN INTENDED.
After the fixtures are read off disk, `winreg`, `glob` and every capture_*
function in the module under test are replaced with tripwires that raise. A
test suite that merely happens not to call the registry today is one refactor
away from being a different suite on every machine; these tests fail loudly
instead, and the failure names the call.

The fixtures under win/fixtures/control_catalog were captured from a real
Windows 10 Home 22H2 installation by control_catalog.capture_snapshot(), which
only reads. They are the input; the catalog is a pure function of them, so the
same fixture must produce byte-identical output on any machine, twice in a row,
forever. Two of the assertions below check exactly that, because "deterministic"
is the gate and a gate you do not test is a wish.

Run it the way every other suite in win/ runs:

    python win\\test_control_catalog.py
"""

import copy
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import control_catalog as C                                    # noqa: E402
import control_catalog_data as D                               # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                              # noqa: BLE001
    pass

fails = []


def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        fails.append(name)
        if detail:
            print("      " + str(detail)[:400])


FIXTURES = C.default_fixture_dir()
SNAPSHOT = C.load_snapshot(FIXTURES)
FIXTURE_TEXT = {name: open(os.path.join(FIXTURES, name), encoding="utf-8").read()
                for name in sorted(os.listdir(FIXTURES))
                if name.endswith(".json")}


# =========================================================================
# THE TRIPWIRES. Everything below this line runs offline or fails saying so.
# =========================================================================

class _Tripwire:
    def __init__(self, label):
        self._label = label

    def __getattr__(self, name):
        raise AssertionError(
            f"a test reached the live machine: {self._label}.{name}")

    def __call__(self, *args, **kwargs):
        raise AssertionError(
            f"a test reached the live machine: {self._label}()")


def _forbidden(label):
    def refuse(*args, **kwargs):
        raise AssertionError(f"a test called the capture path: {label}()")
    return refuse


sys.modules["winreg"] = _Tripwire("winreg")
C.glob = _Tripwire("glob")
for _name in ("capture_snapshot", "capture_environment",
              "capture_system_settings_index",
              "capture_control_panel_namespace", "capture_cpl_modules",
              "capture_mmc_consoles", "build_live_catalog", "write_fixtures",
              "_load_indirect_string_resolver", "_immersive_control_panel_dir"):
    setattr(C, _name, _forbidden("control_catalog." + _name))

CATALOG = C.build_catalog(copy.deepcopy(SNAPSHOT))
BY_ID = CATALOG.by_id


def variant(**changes):
    """A snapshot with one thing different. Pure dict surgery, no I/O."""
    data = copy.deepcopy(SNAPSHOT)
    for key, value in changes.items():
        if key == "environment":
            data["environment"] = dict(data["environment"], **value)
        else:
            data[key] = value
    return data


# =========================================================================
# 1. THE FIXTURE ITSELF -- captured numbers, and nothing that names a machine.
# =========================================================================

print("\n---- the captured fixture ----")

check("the fixture directory holds one file per discovery source",
      sorted(FIXTURE_TEXT) == sorted(C.FIXTURE_FILES.values()),
      sorted(FIXTURE_TEXT))
check("the settings index fixture carries the captured 1,131 rows",
      len(SNAPSHOT["systemSettingsIndex"]["entries"]) == 1131,
      len(SNAPSHOT["systemSettingsIndex"]["entries"]))
check("the Control Panel namespace fixture carries 44 registered items",
      len(SNAPSHOT["controlPanelNamespace"]) == 44,
      len(SNAPSHOT["controlPanelNamespace"]))
check("the .cpl fixture carries 18 modules",
      len(SNAPSHOT["cplModules"]) == 18, SNAPSHOT["cplModules"])
check("the MMC fixture carries 20 consoles",
      len(SNAPSHOT["mmcConsoles"]) == 20, SNAPSHOT["mmcConsoles"])
check("the fixture records the edition the resolver gates on",
      SNAPSHOT["environment"]["editionId"] == "Core"
      and SNAPSHOT["environment"]["build"] == 19045,
      SNAPSHOT["environment"])

# Committed fixtures are published material. These patterns are the three
# things that leak a person out of a Windows registry read.
_SID = re.compile(r"S-1-(?:5|12)-21-\d+")
_USER_PATH = re.compile(r"[A-Za-z]:\\\\Users\\\\(?!%)", re.I)
_UNC = re.compile(r"\\\\\\\\(?!%)[A-Za-z0-9_.-]+\\\\")
for _filename, _text in FIXTURE_TEXT.items():
    check(f"no account SID in {_filename}", not _SID.search(_text),
          (_SID.search(_text) or [""])[0] if _SID.search(_text) else "")
    check(f"no user profile path in {_filename}",
          not _USER_PATH.search(_text))
    check(f"no UNC machine name in {_filename}", not _UNC.search(_text))


# =========================================================================
# 2. ADAPTER NORMALISATION -- every source yields the shape the spec names.
# =========================================================================

print("\n---- adapter normalisation ----")

EVIDENCE = C.read_system_settings_index(SNAPSHOT["systemSettingsIndex"])
ENV = C.WindowsEnvironment.from_dict(SNAPSHOT["environment"])

check("the settings-index adapter reports presence and row count",
      EVIDENCE.present and EVIDENCE.entry_count == 1131,
      (EVIDENCE.present, EVIDENCE.entry_count))
check("the settings-index adapter is evidence, not destinations",
      len(EVIDENCE.page_ids) == 181
      and "SettingsPagePCSystemDisplay" in EVIDENCE.page_ids,
      len(EVIDENCE.page_ids))
check("page vocabulary is harvested per page id",
      "narrator" in EVIDENCE.page_terms["SettingsPageEaseOfAccessNarrator"])
check("deep-link vocabulary is harvested per classic target",
      "mmc:lusrmgr.msc" in EVIDENCE.target_terms
      and "cpl:irprops.cpl" in EVIDENCE.target_terms,
      sorted(k for k in EVIDENCE.target_terms if k.startswith("mmc:"))[:6])

_ms = C.ms_settings_records(ENV, EVIDENCE)
_cp = C.control_panel_records(SNAPSHOT["controlPanelNamespace"], ENV, EVIDENCE)
_cpl, _cpl_rejected = C.cpl_records(SNAPSHOT["cplModules"], ENV, EVIDENCE)
_mmc, _mmc_rejected = C.mmc_records(SNAPSHOT["mmcConsoles"], ENV, EVIDENCE)

check("adapter 1 yields one record per catalogued ms-settings page",
      len(_ms) == len(D.MS_SETTINGS_PAGES), (len(_ms), len(D.MS_SETTINGS_PAGES)))
check("adapter 3 yields one record per registered namespace item",
      len(_cp) == 44, len(_cp))
check("adapter 4a yields one record per capability rule, not per file",
      len(_cpl) == len(D.CPL_MODULES) and len(_cpl) != len(SNAPSHOT["cplModules"]),
      (len(_cpl), len(D.CPL_MODULES), len(SNAPSHOT["cplModules"])))
check("adapter 4b yields one record per capability rule, not per file",
      len(_mmc) == len(D.MMC_CONSOLES),
      (len(_mmc), len(D.MMC_CONSOLES)))

_shape_problems = []
for _record in _ms + _cp + _cpl + _mmc:
    if not _record.id or not _record.title:
        _shape_problems.append(f"{_record.id}: id/title")
    if _record.category not in D.GROUP_IDS:
        _shape_problems.append(f"{_record.id}: category {_record.category}")
    if _record.availability not in C.AVAILABILITY_STATES:
        _shape_problems.append(f"{_record.id}: state {_record.availability}")
    if not _record.availability_reason:
        _shape_problems.append(f"{_record.id}: no reason")
    if _record.integrity not in C.INTEGRITY_LEVELS:
        _shape_problems.append(f"{_record.id}: integrity {_record.integrity}")
    if _record.source not in C.SOURCES:
        _shape_problems.append(f"{_record.id}: source {_record.source}")
    if _record.destination_kind not in C.DESTINATION_KINDS \
            or not _record.destination_target:
        _shape_problems.append(f"{_record.id}: destination")
check("every adapter's records carry the full spec shape",
      not _shape_problems, "; ".join(_shape_problems[:5]))

check("Windows Settings destinations are always Medium integrity",
      all(r.integrity == C.INTEGRITY_MEDIUM for r in _ms),
      [r.id for r in _ms if r.integrity != C.INTEGRITY_MEDIUM][:5])
check("ms-settings records name Microsoft's catalog as their source",
      all(r.source == C.SOURCE_MICROSOFT for r in _ms))
check("an administrative console declares High integrity",
      BY_ID["eos.mmc.services"].integrity == C.INTEGRITY_HIGH)
check("a console that genuinely runs Medium is not marked High",
      BY_ID["eos.mmc.eventvwr"].integrity == C.INTEGRITY_MEDIUM)

check("a third-party registered control appears without a rule",
      BY_ID["eos.control.rst"].source == C.SOURCE_THIRD_PARTY
      and BY_ID["eos.control.rst"].category == D.GROUP_ADMINISTRATION,
      BY_ID.get("eos.control.rst"))
check("an out-of-box control is not accused of being third-party",
      BY_ID["eos.control.microsoft.system"].source == C.SOURCE_LOCAL)
check("a namespace item with no canonical name says so, and stays",
      BY_ID["eos.control.clsid.a0d4cd32-5d5d-4f72-baaa-767a7ad6bac5"]
      .availability == C.CONDITIONAL
      and "canonical name" in BY_ID[
          "eos.control.clsid.a0d4cd32-5d5d-4f72-baaa-767a7ad6bac5"]
      .availability_reason)

# Acceptance criterion: every locally registered item is represented OR carries
# an exclusion reason. On this machine every .cpl and .msc on disk has a rule,
# so the correct answer is zero rejections -- not "some".
check("every .cpl and .msc on this machine is represented, none excluded",
      _cpl_rejected == () and _mmc_rejected == (),
      [r.what for r in _cpl_rejected + _mmc_rejected])
_no_rule = C.cpl_records(SNAPSHOT["cplModules"] + ["vendorpanel.cpl"],
                         ENV, EVIDENCE)[1]
check("an unknown .cpl is never silently listed nor silently dropped",
      any(r.what.endswith("vendorpanel.cpl") and "no capability rule" in r.reason
          for r in _no_rule), [r.what for r in _no_rule])
check("an unknown .msc is rejected the same way",
      any(r.what.endswith("vendor.msc") for r in C.mmc_records(
          SNAPSHOT["mmcConsoles"] + ["vendor.msc"], ENV, EVIDENCE)[1]))
check("nothing on this machine's disk was admitted without a rule",
      all(r.destination_target.lower() in D.CPL_BY_FILENAME
          for r in _cpl))

print("\n---- vocabulary normalisation ----")
check("CamelCase identifiers become search terms",
      C.split_terms("AAA_SettingsGroupAutoplayDefaults")
      == ("autoplay", "defaults"),
      C.split_terms("AAA_SettingsGroupAutoplayDefaults"))
check("a GUID contributes no vocabulary",
      C.split_terms("Classic_{028DE9F5-65F3-4A06-A048-421056F3E421}") == (),
      C.split_terms("Classic_{028DE9F5-65F3-4A06-A048-421056F3E421}"))
check("a deep link reduces to the destination it names",
      (C.normalize_deep_link(
          r"%windir%\system32\mmc.exe %windir%\system32\lusrmgr.msc"),
       C.normalize_deep_link(
           r"%windir%\system32\rundll32.exe shell32.dll,Control_RunDLL "
           r"irprops.cpl"),
       C.normalize_deep_link("Microsoft.WorkFolders"))
      == ("mmc:lusrmgr.msc", "cpl:irprops.cpl", "canonical:microsoft.workfolders"))
check("a deep link that names nothing we hold yields nothing",
      C.normalize_deep_link("") == ""
      and C.normalize_deep_link("SystemSettings_Display") == "")


# =========================================================================
# 3. DEDUPLICATION -- modern preferred, classic retained only on merit.
# =========================================================================

print("\n---- deduplication ----")

check("no two records share one destination",
      len({(r.destination_kind, r.destination_target.lower())
           for r in CATALOG.records}) == len(CATALOG.records))
_topic_era = {}
_collisions = []
for _record in CATALOG.records:
    _key = (_record.topic, C._ERA[_record.destination_kind])
    if _key in _topic_era:
        _collisions.append(f"{_topic_era[_key]} vs {_record.id} on {_key}")
    _topic_era[_key] = _record.id
check("one topic yields at most one record per era",
      not _collisions, "; ".join(_collisions[:4]))

check("the modern page wins its topic",
      BY_ID["eos.settings.display"].superseded_by is None
      and BY_ID["eos.settings.sound"].superseded_by is None)
check("a classic control with no distinct capability is dropped as duplicate",
      "eos.cpl.desk" not in BY_ID)
check("that drop is recorded with the page that replaced it",
      any(r.what == "eos.cpl.desk" and r.kind == "duplicate-page"
          and "eos.settings.display" in r.reason
          for r in CATALOG.rejections),
      [r.reason for r in CATALOG.rejections if r.what == "eos.cpl.desk"])
_classic = BY_ID["eos.control.microsoft.sound"]
check("a materially different classic control is retained and flagged",
      C.FLAG_CLASSIC in _classic.flags
      and _classic.superseded_by == "eos.settings.sound"
      and "exclusive mode" in _classic.retained_reason.lower(),
      (_classic.flags, _classic.superseded_by, _classic.retained_reason))
check("the retained classic keeps its OWN reason, not a folded route's",
      "MSI products" in
      BY_ID["eos.control.microsoft.programsandfeatures"].retained_reason,
      BY_ID["eos.control.microsoft.programsandfeatures"].retained_reason)
check("a second route to one window folds in rather than listing twice",
      "eos.cpl.appwiz" not in BY_ID
      and BY_ID["eos.control.microsoft.programsandfeatures"].alternate_routes
      == ("eos.cpl.appwiz",))
check("the folded route's vocabulary survives the fold",
      "windows features"
      in BY_ID["eos.control.microsoft.programsandfeatures"].aliases)
check("the console and the Control Panel item for one thing are one record",
      "eos.mmc.devmgmt" not in BY_ID
      and "eos.mmc.devmgmt"
      in BY_ID["eos.control.microsoft.devicemanager"].alternate_routes)
check("a classic control with no modern counterpart stays as the primary",
      BY_ID["eos.control.microsoft.windowsfirewall"].superseded_by is None
      and C.FLAG_CLASSIC
      not in BY_ID["eos.control.microsoft.windowsfirewall"].flags)
check("every dropped record left a rejection naming why",
      len(CATALOG.rejections) >= 20
      and all(r.reason for r in CATALOG.rejections))

# The dedup rule must be readable off the data table, not off the outcome.
_undeclared = [item.canonical for item in D.CONTROL_PANEL_ITEMS
               if not item.classic_reason
               and any(page.topic == item.topic
                       for page in D.MS_SETTINGS_PAGES)]
check("every classic control dropped as a duplicate declared no reason",
      all("eos.control." + canonical.lower() not in BY_ID
          for canonical in _undeclared), _undeclared)


# =========================================================================
# 4. AVAILABILITY -- a state and a concrete reason, never a silent omission.
# =========================================================================

print("\n---- availability and its reasons ----")

check("every record carries a non-empty reason in every state",
      all(r.availability_reason for r in CATALOG.records))
check("every state is one of the four declared",
      {r.availability for r in CATALOG.records} <= set(C.AVAILABILITY_STATES),
      {r.availability for r in CATALOG.records})

_gp = BY_ID["eos.mmc.gpedit"]
check("an edition gate beats file presence, and says the edition",
      _gp.availability == C.UNAVAILABLE
      and "Pro" in _gp.availability_reason
      and "Core" in _gp.availability_reason,
      _gp.availability_reason)
check("that control is still IN the catalog, not omitted",
      "eos.mmc.gpedit" in BY_ID
      and "gpedit.msc" in SNAPSHOT["mmcConsoles"])

_pro = C.build_catalog(variant(environment={"editionId": "Professional",
                                            "productName": "Windows 10 Pro"}))
check("the same fixture on Pro makes the same control available",
      _pro.by_id["eos.mmc.gpedit"].availability == C.AVAILABLE
      and _pro.by_id["eos.mmc.secpol"].availability == C.AVAILABLE,
      _pro.by_id["eos.mmc.gpedit"].availability_reason)
check("a Pro-only control absent from disk is unavailable for the right reason",
      _pro.by_id["eos.mmc.printmanagement"].availability == C.UNAVAILABLE
      and "not installed"
      in _pro.by_id["eos.mmc.printmanagement"].availability_reason,
      _pro.by_id["eos.mmc.printmanagement"].availability_reason)

_old = C.build_catalog(variant(environment={"build": 10240,
                                            "displayVersion": "1507"}))
check("a page introduced later is unavailable and names the build",
      _old.by_id["eos.settings.fonts"].availability == C.UNAVAILABLE
      and "introduced in Windows build 17763"
      in _old.by_id["eos.settings.fonts"].availability_reason,
      _old.by_id["eos.settings.fonts"].availability_reason)
check("a page that predates the gate is unaffected by it",
      _old.by_id["eos.settings.display"].availability == C.AVAILABLE)

_new = C.build_catalog(variant(environment={"build": 22631,
                                            "displayVersion": "23H2"}))
check("a page removed in a later build is unavailable and names the build",
      _new.by_id["eos.settings.tabletmode"].availability == C.UNAVAILABLE
      and "removed after Windows build"
      in _new.by_id["eos.settings.tabletmode"].availability_reason,
      _new.by_id["eos.settings.tabletmode"].availability_reason)

_no_bt = C.build_catalog(variant(
    cplModules=[n for n in SNAPSHOT["cplModules"] if n != "bthprops.cpl"]))
check("a rule whose file is absent is unavailable, and says which file",
      _no_bt.by_id["eos.cpl.bthprops"].availability == C.UNAVAILABLE
      and "bthprops.cpl is not installed"
      in _no_bt.by_id["eos.cpl.bthprops"].availability_reason,
      _no_bt.by_id["eos.cpl.bthprops"].availability_reason)

_index = copy.deepcopy(SNAPSHOT["systemSettingsIndex"])
_index["entries"] = [e for e in _index["entries"]
                     if e.get("pageId") != "SettingsPagePCSystemDisplay"]
_gone = C.build_catalog(variant(systemSettingsIndex=_index))
check("a catalogued page absent from the local index is unavailable",
      _gone.by_id["eos.settings.display"].availability == C.UNAVAILABLE
      and "SettingsPagePCSystemDisplay"
      in _gone.by_id["eos.settings.display"].availability_reason,
      _gone.by_id["eos.settings.display"].availability_reason)

_absent = C.build_catalog(variant(systemSettingsIndex={
    "present": False, "indexName": "", "entries": [],
    "absenceReason": "no ImmersiveControlPanel on this installation"}))
check("a missing index disproves nothing and is not treated as proof",
      _absent.by_id["eos.settings.display"].availability == C.AVAILABLE
      and "could not corroborate"
      in _absent.by_id["eos.settings.display"].availability_reason,
      _absent.by_id["eos.settings.display"].availability_reason)
check("a missing index still produces the whole catalog",
      len(_absent.records) == len(CATALOG.records))

_bt = BY_ID["eos.settings.bluetooth"]
check("a hardware requirement is conditional, and names the hardware",
      _bt.availability == C.CONDITIONAL
      and "Bluetooth radio" in _bt.availability_reason,
      _bt.availability_reason)
check("a hardware requirement is never guessed at",
      "not guessed" in _bt.availability_reason)

check("an available record still explains itself",
      BY_ID["eos.mmc.services"].availability == C.AVAILABLE
      and "services.msc is installed"
      in BY_ID["eos.mmc.services"].availability_reason,
      BY_ID["eos.mmc.services"].availability_reason)
check("a registered Control Panel item cites its registration",
      "Control Panel namespace"
      in BY_ID["eos.control.microsoft.system"].availability_reason)


# =========================================================================
# 5. CATEGORY TAXONOMY -- the twelve groups from the spec, and only those.
# =========================================================================

print("\n---- the twelve logical groups ----")

SPEC_GROUPS = ("Display and sound", "Devices and input", "Network and sharing",
               "Apps and defaults", "Accounts and sign-in", "Personalization",
               "Accessibility", "Privacy and security", "Time and language",
               "Storage and recovery", "Updates and diagnostics",
               "Administration")
check("the taxonomy is the spec's twelve groups, in the spec's order",
      tuple(title for _id, title in D.GROUPS) == SPEC_GROUPS,
      tuple(title for _id, title in D.GROUPS))
check("every record lands in one of the twelve",
      {r.category for r in CATALOG.records} <= set(D.GROUP_IDS),
      {r.category for r in CATALOG.records} - set(D.GROUP_IDS))
_empty = [title for _id, title, members in CATALOG.groups() if not members]
check("no group is empty on this machine", not _empty, _empty)
check("groups() returns the taxonomy in declared order",
      tuple(gid for gid, _t, _m in CATALOG.groups()) == D.GROUP_IDS)

for _record_id, _group in (
        ("eos.settings.display", D.GROUP_DISPLAY_SOUND),
        ("eos.settings.bluetooth", D.GROUP_DEVICES_INPUT),
        ("eos.settings.appsfeatures", D.GROUP_APPS_DEFAULTS),
        ("eos.control.microsoft.windowsfirewall", D.GROUP_PRIVACY_SECURITY),
        ("eos.mmc.services", D.GROUP_ADMINISTRATION),
        ("eos.mmc.diskmgmt", D.GROUP_STORAGE_RECOVERY),
        ("eos.settings.windowsupdate", D.GROUP_UPDATES_DIAGNOSTICS),
        ("eos.settings.easeofaccess-narrator", D.GROUP_ACCESSIBILITY),
        ("eos.settings.dateandtime", D.GROUP_TIME_LANGUAGE),
        ("eos.settings.signinoptions", D.GROUP_ACCOUNTS_SIGNIN),
        ("eos.settings.themes", D.GROUP_PERSONALIZATION),
        ("eos.settings.network-status", D.GROUP_NETWORK_SHARING)):
    check(f"{_record_id} is filed under {_group}",
          BY_ID[_record_id].category == _group,
          BY_ID[_record_id].category)


# =========================================================================
# 6. ID STABILITY AND DETERMINISM -- the same fixture, always the same answer.
# =========================================================================

print("\n---- id stability and determinism ----")

ANCHORS = (
    "eos.settings.display", "eos.settings.sound", "eos.settings.bluetooth",
    "eos.settings.appsfeatures", "eos.settings.windowsupdate",
    "eos.settings.windowsdefender", "eos.settings.startupapps",
    "eos.control.microsoft.programsandfeatures",
    "eos.control.microsoft.windowsfirewall", "eos.control.microsoft.system",
    "eos.mmc.services", "eos.mmc.eventvwr", "eos.cpl.ncpa",
)
_missing = [anchor for anchor in ANCHORS if anchor not in BY_ID]
check("every published id the GUI will store still resolves",
      not _missing, _missing)
check("ids are unique", len({r.id for r in CATALOG.records})
      == len(CATALOG.records))
check("an id says what kind of destination it is",
      all(r.id.startswith(("eos.settings.", "eos.control.", "eos.cpl.",
                           "eos.mmc."))
          for r in CATALOG.records),
      [r.id for r in CATALOG.records
       if not r.id.startswith(("eos.settings.", "eos.control.", "eos.cpl.",
                               "eos.mmc."))][:5])
check("an ms-settings id is derived from the URI, not from a counter",
      BY_ID["eos.settings.easeofaccess-narrator"].destination_target
      == "ms-settings:easeofaccess-narrator")

_again = C.build_catalog(copy.deepcopy(SNAPSHOT))
check("the same fixture built twice is byte-identical",
      json.dumps(CATALOG.as_dict(), sort_keys=True)
      == json.dumps(_again.as_dict(), sort_keys=True))
check("record order is stable and sorted by id",
      [r.id for r in CATALOG.records] == sorted(r.id for r in CATALOG.records))
check("rejection order is stable",
      [(r.kind, r.what) for r in CATALOG.rejections]
      == [(r.kind, r.what) for r in _again.rejections])
check("a shuffled snapshot yields the same catalog",
      json.dumps(C.build_catalog(variant(
          cplModules=list(reversed(SNAPSHOT["cplModules"])),
          mmcConsoles=list(reversed(SNAPSHOT["mmcConsoles"])),
          controlPanelNamespace=list(reversed(
              SNAPSHOT["controlPanelNamespace"])))).as_dict(),
          sort_keys=True)
      == json.dumps(CATALOG.as_dict(), sort_keys=True))


# =========================================================================
# 7. SEARCH -- the spec's own words reach the spec's own destinations.
# =========================================================================

print("\n---- the search-alias path ----")


def first(term, **kwargs):
    hits = CATALOG.search(term, **kwargs)
    return hits[0].id if hits else None


def reaches(term, record_id, within=3):
    return record_id in [r.id for r in CATALOG.search(term)[:within]]


for _term, _expected in (("uninstall", "eos.settings.appsfeatures"),
                         ("bluetooth", "eos.settings.bluetooth"),
                         ("firewall", "eos.control.microsoft.windowsfirewall"),
                         ("monitor", "eos.settings.display"),
                         ("startup", "eos.settings.startupapps"),
                         ("display", "eos.settings.display"),
                         ("sound", "eos.settings.sound")):
    check(f'"{_term}" reaches {_expected} first',
          first(_term) == _expected,
          [r.id for r in CATALOG.search(_term)[:4]])

check('"uninstall" also reaches the classic control that does more',
      reaches("uninstall", "eos.control.microsoft.programsandfeatures"),
      [r.id for r in CATALOG.search("uninstall")[:4]])
check('"firewall" also reaches Windows Security and the advanced console',
      reaches("firewall", "eos.settings.windowsdefender")
      and reaches("firewall", "eos.mmc.wf"),
      [r.id for r in CATALOG.search("firewall")[:4]])
check('"bluetooth" reaches the modern page before the classic module',
      [r.id for r in CATALOG.search("bluetooth")]
      == ["eos.settings.bluetooth", "eos.cpl.bthprops"],
      [r.id for r in CATALOG.search("bluetooth")])

check("a curated alias outranks a term harvested from the registry",
      first("startup") == "eos.settings.startupapps"
      and "startup" in BY_ID[
          "eos.control.microsoft.bitlockerdriveencryption"].search_terms,
      [r.id for r in CATALOG.search("startup")[:3]])
_display_page = next(page for page in D.MS_SETTINGS_PAGES
                     if page.uri == "ms-settings:display")
check("curated aliases stay exactly what the data table declared",
      BY_ID["eos.settings.display"].aliases == _display_page.aliases,
      BY_ID["eos.settings.display"].aliases)
check("harvested vocabulary is a separate field, and is not curated",
      "gpu" in BY_ID["eos.settings.display"].search_terms
      and "gpu" not in BY_ID["eos.settings.display"].aliases,
      BY_ID["eos.settings.display"].search_terms[:8])
check("harvested vocabulary still finds things aliases missed",
      any(r.id == "eos.settings.easeofaccess-narrator"
          for r in CATALOG.search("braille")),
      [r.id for r in CATALOG.search("braille")[:4]])

check("an unavailable control is still reachable by search",
      reaches("group policy", "eos.mmc.gpedit", within=2)
      and BY_ID["eos.mmc.gpedit"].availability == C.UNAVAILABLE,
      [r.id for r in CATALOG.search("group policy")[:3]])
check("hiding unavailable is opt-in and removes exactly those",
      "eos.mmc.gpedit" not in [r.id for r in CATALOG.search(
          "group policy", include_unavailable=False)])
check("searching for nothing returns nothing rather than everything",
      CATALOG.search("") == () and CATALOG.search("   ") == ())
check("a term nobody registered finds nothing",
      CATALOG.search("zzzqqx") == ())
check("search is case-insensitive",
      [r.id for r in CATALOG.search("BlueTooth")]
      == [r.id for r in CATALOG.search("bluetooth")])
check("the canonical launch contract is itself searchable",
      reaches("Microsoft.ProgramsAndFeatures",
              "eos.control.microsoft.programsandfeatures", within=1))
check("a third-party control nobody wrote a rule for is still findable",
      reaches("rapid storage", "eos.control.rst", within=1),
      [r.id for r in CATALOG.search("rapid storage")[:3]])


# =========================================================================
# 8. THE STRUCTURAL GUARD IS NOT DECORATION.
# =========================================================================

print("\n---- the well-formedness guard ----")


def raises(records):
    try:
        C._assert_well_formed(records)
    except ValueError:
        return True
    return False


_ok = C.CatalogRecord(id="eos.test.one", title="One",
                      destination_target="ms-settings:x",
                      availability_reason="because")
check("a well-formed record passes", not raises((_ok,)))
check("a duplicate id is refused", raises((_ok, _ok)))
check("a category outside the taxonomy is refused",
      raises((C.replace(_ok, category="invented"),)))
check("an availability with no reason is refused",
      raises((C.replace(_ok, availability_reason=""),)))
check("an unknown integrity level is refused",
      raises((C.replace(_ok, integrity="low"),)))
check("a destination with no target is refused",
      raises((C.replace(_ok, destination_target=""),)))
check("an unknown source is refused",
      raises((C.replace(_ok, source="rumour"),)))

check("the tripwires were armed for the whole run",
      isinstance(sys.modules["winreg"], _Tripwire))


print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
