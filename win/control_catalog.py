# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Control Center catalog: four discovery sources, one immutable inventory.

Phase 1 of docs/CONTROL-CENTER.md. This module knows what management
destinations this Windows has and what is true about each one. It knows nothing
about windows, buttons, pipes or brokers, and IT LAUNCHES NOTHING -- there is no
CreateProcess, no ShellExecute, no LaunchUriAsync, and no control.exe anywhere
below. Phase 2 owns activation; a catalog that could also activate would be a
catalog nobody could test without a live desktop.

CAPTURE IS SEPARATE FROM NORMALISE, AND THAT IS THE WHOLE DESIGN.

    capture_snapshot()            reads the machine   (registry, %WINDIR%)
    build_catalog(snapshot)       reads nothing       (pure)

A snapshot is plain JSON: strings, numbers, lists. Every adapter, the
deduplicator, the availability resolver and the search index consume a snapshot
and never the machine, so the entire inventory is testable from a fixture and
two runs on the same fixture cannot disagree. The gate for this phase is
"deterministic inventory tests"; that is only achievable if determinism is
structural rather than a habit.

THE FOUR SOURCES (spec section "Catalog architecture").

  1. ms-settings: -- Microsoft's published, versioned URI inventory, held as a
     data table in control_catalog_data. Build-gated.
  2. AllSystemSettings_*.xml -- the installed Windows search index. The spec
     assigns it exactly two jobs: "local search vocabulary and page-presence
     evidence". It is therefore NOT a destination source, and its adapter
     returns evidence rather than records. Making 1,131 keyword rows into 1,131
     destinations is precisely the duplication the spec excludes.
  3. Registered Control Panel namespace entries, read from the registry.
     Canonical names (System.ApplicationName) are the launch contract. A
     third-party control that registers itself appears here with no code change,
     which is an acceptance criterion.
  4. .cpl modules and MMC consoles, admitted only by the capability rules in
     control_catalog_data. Presence on disk is necessary and not sufficient.

AVAILABILITY IS A STATE PLUS A REASON, ALWAYS BOTH. A control that this edition
cannot run stays in the catalog and says why. Silent omission is the failure
mode the spec names twice, and it is the reason `_resolve_availability` returns
a reason string in the AVAILABLE case too: a resolver that only explains itself
when the answer is no is a resolver that can drop a record without noticing.

Pure standard library (winreg + ctypes at capture time only). No dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any
import glob
import json
import os
import re
import xml.etree.ElementTree as ET

from control_catalog_data import (
    CATALOG_REVISION,
    CONTROL_PANEL_BY_CANONICAL,
    CONTROL_PANEL_CLSID_NOTES,
    CPL_BY_FILENAME,
    CPL_MODULES,
    ENTERPRISE_EDITION_IDS,
    EDITION_ENTERPRISE,
    EDITION_PRO,
    GROUP_ADMINISTRATION,
    GROUP_IDS,
    GROUP_TITLES,
    GROUPS,
    HARDWARE_LABELS,
    MMC_BY_FILENAME,
    MMC_CONSOLES,
    MS_SETTINGS_PAGES,
    PRO_EDITION_IDS,
)

SNAPSHOT_SCHEMA = 1

# ---- destination kinds -----------------------------------------------------

DEST_MS_SETTINGS = "ms-settings"
DEST_CONTROL_PANEL = "control-panel-canonical"
DEST_CONTROL_PANEL_CLSID = "control-panel-clsid"
DEST_CPL = "control-panel-cpl"
DEST_MMC = "mmc-console"

DESTINATION_KINDS = (DEST_MS_SETTINGS, DEST_CONTROL_PANEL,
                     DEST_CONTROL_PANEL_CLSID, DEST_CPL, DEST_MMC)

# Preference order inside one topic. Modern first, then Microsoft's preferred
# classic launch contract, then the module, then the console.
_ERA_MODERN = "modern"
_ERA_CLASSIC = "classic"
_ERA = {
    DEST_MS_SETTINGS: _ERA_MODERN,
    DEST_CONTROL_PANEL: _ERA_CLASSIC,
    DEST_CONTROL_PANEL_CLSID: _ERA_CLASSIC,
    DEST_CPL: _ERA_CLASSIC,
    DEST_MMC: _ERA_CLASSIC,
}
_ROUTE_RANK = {
    DEST_MS_SETTINGS: 0,
    DEST_CONTROL_PANEL: 1,
    DEST_CONTROL_PANEL_CLSID: 2,
    DEST_CPL: 3,
    DEST_MMC: 4,
}

# ---- availability states ---------------------------------------------------

AVAILABLE = "available"
CONDITIONAL = "conditional"
UNAVAILABLE = "unavailable"
UNKNOWN = "unknown"

AVAILABILITY_STATES = (AVAILABLE, CONDITIONAL, UNAVAILABLE, UNKNOWN)

# ---- integrity levels ------------------------------------------------------

INTEGRITY_MEDIUM = "medium"
INTEGRITY_HIGH = "high"
INTEGRITY_LEVELS = (INTEGRITY_MEDIUM, INTEGRITY_HIGH)

# ---- sources ---------------------------------------------------------------

SOURCE_MICROSOFT = "microsoft-catalog"
SOURCE_LOCAL = "local-registration"
SOURCE_THIRD_PARTY = "third-party-registration"
SOURCES = (SOURCE_MICROSOFT, SOURCE_LOCAL, SOURCE_THIRD_PARTY)

# ---- record flags ----------------------------------------------------------

FLAG_CLASSIC = "classic"


@dataclass(frozen=True)
class CatalogRecord:
    """One user-addressable Windows management destination.

    Every field the spec's "Each item will have" list names is here and none is
    optional at read time: `availability_reason` is never empty, including when
    `availability` is AVAILABLE.
    """

    id: str
    title: str
    # Curated aliases from the data tables -- what a person would type.
    aliases: tuple = ()
    # Vocabulary harvested from the installed Windows search index (source 2).
    # Kept apart from `aliases` for two reasons: it is derived rather than
    # chosen, so it is noisy ("item", "main", "view"), and search must be able
    # to rank a deliberate alias above an incidental one. Merging the two would
    # make "startup" reach BitLocker's registered name "Secure Startup" before
    # it reaches Startup apps, which is exactly the acceptance criterion.
    search_terms: tuple = ()
    destination_kind: str = DEST_MS_SETTINGS
    destination_target: str = ""
    category: str = GROUP_ADMINISTRATION
    availability: str = AVAILABLE
    availability_reason: str = ""
    integrity: str = INTEGRITY_MEDIUM
    source: str = SOURCE_MICROSOFT
    topic: str = ""
    flags: tuple = ()
    # Set by deduplication when a modern page supersedes this classic control.
    superseded_by: str | None = None
    # Why this classic control survived deduplication at all.
    retained_reason: str = ""
    # Other ids that reach the same destination and were folded into this one.
    alternate_routes: tuple = ()
    # Free-form provenance strings; the audit trail for every claim above.
    evidence: tuple = ()

    @property
    def category_title(self) -> str:
        return GROUP_TITLES[self.category]

    @property
    def visible(self) -> bool:
        """A record is never hidden. Kept as a name for what the GUI asks."""
        return True

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "aliases": list(self.aliases),
            "searchTerms": list(self.search_terms),
            "destination": {"kind": self.destination_kind,
                            "target": self.destination_target},
            "category": self.category,
            "categoryTitle": self.category_title,
            "availability": self.availability,
            "availabilityReason": self.availability_reason,
            "integrity": self.integrity,
            "source": self.source,
            "topic": self.topic,
            "flags": list(self.flags),
            "supersededBy": self.superseded_by,
            "retainedReason": self.retained_reason,
            "alternateRoutes": list(self.alternate_routes),
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class Rejection:
    """Something found, considered, and not admitted -- with the reason.

    The spec's acceptance criteria require every locally registered Control
    Panel item to be "represented or carry an exclusion reason". This is the
    second half of that sentence, and it is data rather than a log line so a
    test can assert on it.
    """

    what: str
    kind: str
    reason: str


@dataclass(frozen=True)
class WindowsEnvironment:
    """The gating facts about this Windows, and nothing that identifies it."""

    product_name: str = "unknown"
    edition_id: str = "unknown"
    installation_type: str = "Client"
    build: int = 0
    ubr: int = 0
    display_version: str = ""
    major: int = 0
    minor: int = 0

    @property
    def version_label(self) -> str:
        label = f"build {self.build}"
        if self.display_version:
            label += f" ({self.display_version})"
        return label

    def as_dict(self) -> dict:
        return {
            "productName": self.product_name,
            "editionId": self.edition_id,
            "installationType": self.installation_type,
            "build": self.build,
            "ubr": self.ubr,
            "displayVersion": self.display_version,
            "major": self.major,
            "minor": self.minor,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WindowsEnvironment":
        return cls(
            product_name=str(data.get("productName", "unknown")),
            edition_id=str(data.get("editionId", "unknown")),
            installation_type=str(data.get("installationType", "Client")),
            build=int(data.get("build", 0) or 0),
            ubr=int(data.get("ubr", 0) or 0),
            display_version=str(data.get("displayVersion", "")),
            major=int(data.get("major", 0) or 0),
            minor=int(data.get("minor", 0) or 0),
        )


@dataclass(frozen=True)
class SystemSettingsEvidence:
    """What the local AllSystemSettings index proves, not what it launches.

    `page_ids` answers "does this build have that Settings page". `page_terms`
    and `target_terms` are the local search vocabulary, keyed by the two things
    an index row can point at: a modern page id, and a classic deep-link target.
    """

    present: bool = False
    index_name: str = ""
    entry_count: int = 0
    page_ids: frozenset = frozenset()
    page_terms: dict = field(default_factory=dict)
    target_terms: dict = field(default_factory=dict)
    absence_reason: str = ""


# =========================================================================
# SCRUBBING
#
# Fixtures are committed. Nothing that names this machine or this person may
# travel with them, and "I did not put any in" is not a control -- a registry
# value can contain a profile path nobody expected. So every string entering a
# snapshot goes through one function, and the tests assert the function is not
# bypassed by checking the committed fixtures themselves.
# =========================================================================

_SID_RE = re.compile(r"S-1-(?:5|12)-\d+(?:-\d+)+", re.I)
_UNC_RE = re.compile(r"\\\\[A-Za-z0-9_.-]+\\")


def _scrub(value: Any) -> Any:
    """Replace machine- and user-identifying text with stable tokens."""
    if not isinstance(value, str) or not value:
        return value
    text = value
    for env_name, token in (("USERPROFILE", "%USERPROFILE%"),
                            ("LOCALAPPDATA", "%LOCALAPPDATA%"),
                            ("APPDATA", "%APPDATA%"),
                            ("ProgramFiles", "%ProgramFiles%"),
                            ("ProgramFiles(x86)", "%ProgramFiles(x86)%"),
                            ("ProgramData", "%ProgramData%"),
                            ("SystemRoot", "%SystemRoot%"),
                            ("WINDIR", "%SystemRoot%")):
        actual = os.environ.get(env_name)
        if actual and len(actual) > 3:
            text = re.sub(re.escape(actual), token, text, flags=re.I)
    users_root = os.environ.get("SystemDrive", "C:") + "\\Users\\"
    text = re.sub(re.escape(users_root) + r"[^\\/:*?\"<>|]+",
                  "%USERPROFILE%", text, flags=re.I)
    for env_name in ("COMPUTERNAME", "USERNAME", "USERDOMAIN"):
        actual = os.environ.get(env_name)
        if actual and len(actual) >= 3:
            token = "%" + env_name + "%"
            text = re.sub(r"\b" + re.escape(actual) + r"\b", token, text,
                          flags=re.I)
    text = _SID_RE.sub("S-1-5-21-REDACTED", text)
    text = _UNC_RE.sub(r"\\\\%COMPUTERNAME%\\", text)
    return text


# =========================================================================
# CAPTURE -- the only code here that touches the machine. Read-only, always.
# =========================================================================

_CP_NAMESPACE_KEY = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer"
                     r"\ControlPanel\NameSpace")
_CURRENT_VERSION_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
_CLSID_KEY = r"SOFTWARE\Classes\CLSID"


def capture_environment() -> dict:
    """Read the version and edition facts the availability resolver needs."""
    import winreg

    values = {}
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _CURRENT_VERSION_KEY,
                             0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
    except OSError:
        return WindowsEnvironment().as_dict()
    try:
        for name in ("ProductName", "EditionID", "InstallationType",
                     "CurrentBuildNumber", "UBR", "DisplayVersion",
                     "CurrentMajorVersionNumber", "CurrentMinorVersionNumber"):
            try:
                values[name] = winreg.QueryValueEx(key, name)[0]
            except OSError:
                values[name] = None
    finally:
        winreg.CloseKey(key)

    def as_int(name):
        try:
            return int(values.get(name) or 0)
        except (TypeError, ValueError):
            return 0

    return WindowsEnvironment(
        product_name=_scrub(str(values.get("ProductName") or "unknown")),
        edition_id=str(values.get("EditionID") or "unknown"),
        installation_type=str(values.get("InstallationType") or "Client"),
        build=as_int("CurrentBuildNumber"),
        ubr=as_int("UBR"),
        display_version=str(values.get("DisplayVersion") or ""),
        major=as_int("CurrentMajorVersionNumber"),
        minor=as_int("CurrentMinorVersionNumber"),
    ).as_dict()


def _immersive_control_panel_dir() -> str:
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or ""
    return os.path.join(windir, "ImmersiveControlPanel", "Settings")


def _load_indirect_string_resolver():
    """A resolver for `@module,-id` strings, or None where unavailable.

    SHLoadIndirectString is a read; it loads a string resource out of a DLL.
    It cannot start a process. The `@{package?ms-resource://...}` form does not
    resolve outside the owning package's context, and that is fine: those rows
    contribute their filename vocabulary instead, and nothing pretends to a
    title it could not read.
    """
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:                                       # noqa: BLE001
        return None
    try:
        shlwapi = ctypes.WinDLL("shlwapi", use_last_error=True)
        shlwapi.SHLoadIndirectString.argtypes = [
            wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, ctypes.c_void_p]
        shlwapi.SHLoadIndirectString.restype = ctypes.HRESULT
    except Exception:                                       # noqa: BLE001
        return None

    def resolve(reference):
        if not reference or not reference.startswith("@"):
            return ""
        buffer = ctypes.create_unicode_buffer(512)
        try:
            shlwapi.SHLoadIndirectString(reference, buffer, 512, None)
        except OSError:
            return ""
        except Exception:                                   # noqa: BLE001
            return ""
        return buffer.value or ""

    return resolve


def capture_system_settings_index() -> dict:
    """Locate and read the installed AllSystemSettings index.

    Absence is a normal answer, not an error: a Server Core installation has no
    ImmersiveControlPanel at all. The snapshot then records why, and the
    availability resolver declines to use page presence as evidence rather than
    concluding every modern page is missing.
    """
    directory = _immersive_control_panel_dir()
    if not directory or not os.path.isdir(directory):
        return {"present": False, "indexName": "", "entries": [],
                "absenceReason": ("%WINDIR%\\ImmersiveControlPanel\\Settings "
                                  "does not exist on this installation")}
    matches = sorted(glob.glob(os.path.join(directory,
                                            "AllSystemSettings_*.xml")))
    if not matches:
        return {"present": False, "indexName": "", "entries": [],
                "absenceReason": ("no AllSystemSettings_*.xml under "
                                  "%WINDIR%\\ImmersiveControlPanel\\Settings")}
    path = matches[0]
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        return {"present": False, "indexName": os.path.basename(path),
                "entries": [],
                "absenceReason": f"the settings index would not parse: {exc}"}

    resolve = _load_indirect_string_resolver()
    entries = []
    for node in root:
        description_ref = node.findtext("SettingInformation/Description") or ""
        text = ""
        if resolve and description_ref.startswith("@") \
                and not description_ref.startswith("@{"):
            text = resolve(description_ref)
        entries.append({
            "filename": _scrub(node.findtext("Filename") or ""),
            "pageId": node.findtext("SettingIdentity/PageID") or "",
            "groupId": node.findtext("SettingIdentity/GroupID") or "",
            "deepLink": _scrub(node.findtext(
                "ApplicationInformation/DeepLink") or ""),
            "descriptionRef": _scrub(description_ref),
            "descriptionText": _scrub(text),
            "condition": _scrub(node.findtext("SettingIdentity/Condition")
                                or ""),
            "includeWithFeature": node.get("IncludeWithFeature") or "",
            "excludeWithFeature": node.get("ExcludeWithFeature") or "",
        })
    # The index filename carries a fixed Windows GUID, not a machine id, but it
    # is recorded as a bare basename so nothing about this disk travels.
    return {"present": True, "indexName": os.path.basename(path),
            "entries": entries, "absenceReason": ""}


def capture_control_panel_namespace() -> list:
    """Read registered Control Panel namespace entries and canonical names."""
    import winreg

    seen = {}
    for view_name, view in (("64", winreg.KEY_WOW64_64KEY),
                            ("32", winreg.KEY_WOW64_32KEY)):
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _CP_NAMESPACE_KEY,
                                 0, winreg.KEY_READ | view)
        except OSError:
            continue
        try:
            count = winreg.QueryInfoKey(key)[0]
            for index in range(count):
                name = winreg.EnumKey(key, index)
                if not name.startswith("{"):
                    # "DelegateFolders" and friends are containers, not items.
                    continue
                clsid = name.upper()
                entry = seen.setdefault(clsid, {
                    "clsid": clsid, "registeredName": "", "canonicalName": "",
                    "localizedStringRef": "", "module": "", "views": []})
                if view_name not in entry["views"]:
                    entry["views"].append(view_name)
                try:
                    sub = winreg.OpenKey(key, name)
                except OSError:
                    continue
                try:
                    value = winreg.QueryValueEx(sub, "")[0]
                    if value and not entry["registeredName"]:
                        entry["registeredName"] = _scrub(str(value))
                except OSError:
                    pass
                finally:
                    winreg.CloseKey(sub)
        finally:
            winreg.CloseKey(key)

    for clsid, entry in seen.items():
        try:
            clsid_key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, _CLSID_KEY + "\\" + clsid, 0,
                winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
        except OSError:
            continue
        try:
            for value_name, field_name in (
                    ("System.ApplicationName", "canonicalName"),
                    ("LocalizedString", "localizedStringRef"),
                    ("InfoTip", None)):
                if field_name is None:
                    continue
                try:
                    value = winreg.QueryValueEx(clsid_key, value_name)[0]
                except OSError:
                    continue
                entry[field_name] = _scrub(str(value))
        finally:
            winreg.CloseKey(clsid_key)
        reference = entry.get("localizedStringRef") or ""
        if reference.startswith("@"):
            module = reference[1:].split(",")[0]
            entry["module"] = _scrub(module)

    return [seen[clsid] for clsid in sorted(seen)]


def _system32_names(pattern: str) -> list:
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or ""
    directory = os.path.join(windir, "System32")
    if not os.path.isdir(directory):
        return []
    return sorted((os.path.basename(path)
                   for path in glob.glob(os.path.join(directory, pattern))),
                  key=str.lower)


def capture_cpl_modules() -> list:
    """Every .cpl present in System32. Presence only -- admission is a rule."""
    return _system32_names("*.cpl")


def capture_mmc_consoles() -> list:
    """Every .msc present in System32. Presence only -- admission is a rule."""
    return _system32_names("*.msc")


def capture_snapshot() -> dict:
    """One read-only pass over this machine, as plain JSON-safe data."""
    return {
        "schema": SNAPSHOT_SCHEMA,
        "catalogRevision": CATALOG_REVISION,
        "environment": capture_environment(),
        "systemSettingsIndex": capture_system_settings_index(),
        "controlPanelNamespace": capture_control_panel_namespace(),
        "cplModules": capture_cpl_modules(),
        "mmcConsoles": capture_mmc_consoles(),
    }


FIXTURE_FILES = {
    "environment": "environment.json",
    "systemSettingsIndex": "system_settings_index.json",
    "controlPanelNamespace": "control_panel_namespace.json",
    "cplModules": "cpl_modules.json",
    "mmcConsoles": "mmc_consoles.json",
}


def default_fixture_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "control_catalog")


def write_fixtures(directory: str | None = None,
                   snapshot: dict | None = None) -> dict:
    """Capture (or accept) a snapshot and write it out as fixture files.

    Split per source so a reviewer can read one adapter's input without paging
    through a megabyte of settings-index rows.
    """
    directory = directory or default_fixture_dir()
    snapshot = snapshot if snapshot is not None else capture_snapshot()
    os.makedirs(directory, exist_ok=True)
    written = {}
    for section, filename in FIXTURE_FILES.items():
        payload = {"schema": SNAPSHOT_SCHEMA,
                   "catalogRevision": snapshot.get("catalogRevision",
                                                   CATALOG_REVISION),
                   "section": section,
                   "data": snapshot[section]}
        path = os.path.join(directory, filename)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=1, sort_keys=True)
            handle.write("\n")
        written[section] = path
    return written


def load_snapshot(directory: str | None = None) -> dict:
    """Read a snapshot back out of a fixture directory. Touches no registry."""
    directory = directory or default_fixture_dir()
    snapshot = {"schema": SNAPSHOT_SCHEMA, "catalogRevision": CATALOG_REVISION}
    for section, filename in FIXTURE_FILES.items():
        path = os.path.join(directory, filename)
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        snapshot[section] = payload["data"]
        snapshot["catalogRevision"] = payload.get("catalogRevision",
                                                  CATALOG_REVISION)
    return snapshot


# =========================================================================
# VOCABULARY
# =========================================================================

_STOPWORDS = frozenset({
    "aaa", "and", "the", "for", "with", "settings", "setting", "page",
    "pages", "group", "groups", "classic", "immutable", "overview", "system",
    "windows", "microsoft", "dll", "exe", "cpl", "msc", "runwizard",
    "control", "rundll", "rundll32", "shell32", "windir", "systemroot",
    "systemsettings", "name", "your", "this", "from", "that", "when", "com",
})

_CAMEL_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")
_GUID_RE = re.compile(r"\{[0-9A-Fa-f-]{36}\}")


def split_terms(text: str) -> tuple:
    """CamelCase and punctuation into lowercase search terms.

    "AAA_SettingsGroupAutoplayDefaults" -> ("autoplay", "defaults")

    This is the honest half of the local vocabulary. The rich half -- the
    localized Description strings -- is mostly `@{package?ms-resource://...}`,
    which does not resolve outside the owning package, so the identifier is what
    is actually readable offline and it is used rather than invented.
    """
    if not text:
        return ()
    text = _GUID_RE.sub(" ", text)
    terms = []
    for chunk in re.split(r"[^A-Za-z0-9]+", text):
        for piece in _CAMEL_RE.findall(chunk):
            term = piece.lower()
            if len(term) < 3 or term in _STOPWORDS or term.isdigit():
                continue
            if term not in terms:
                terms.append(term)
    return tuple(terms)


def normalize_deep_link(deep_link: str) -> str:
    """A settings-index DeepLink reduced to the destination it names.

    Returns "cpl:main.cpl", "mmc:services.msc", "canonical:microsoft.system",
    "clsid:{...}" or "" when the row points at nothing this catalog holds.
    """
    if not deep_link:
        return ""
    text = deep_link.strip()
    lowered = text.lower()
    match = re.search(r"([A-Za-z0-9_.-]+\.msc)", text)
    if match:
        return "mmc:" + match.group(1).lower()
    match = re.search(r"([A-Za-z0-9_.-]+\.cpl)", text)
    if match:
        return "cpl:" + match.group(1).lower()
    guid = _GUID_RE.search(text)
    if guid and ("shell:::" in lowered or lowered.startswith("{")):
        return "clsid:" + guid.group(0).upper()
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z0-9]+)+", text):
        return "canonical:" + lowered
    return ""


def read_system_settings_index(section: dict) -> SystemSettingsEvidence:
    """Adapter 2 -- search vocabulary and page-presence evidence.

    Deliberately returns no records. See the module docstring: the spec gives
    this source exactly two jobs and "1,131 destinations" is not one of them.
    """
    if not section or not section.get("present"):
        return SystemSettingsEvidence(
            present=False,
            index_name=str((section or {}).get("indexName", "")),
            absence_reason=str((section or {}).get("absenceReason",
                                                   "no settings index")))
    page_terms: dict = {}
    target_terms: dict = {}
    page_ids = set()
    entries = section.get("entries") or []
    for entry in entries:
        page_id = entry.get("pageId") or ""
        terms = list(split_terms(entry.get("filename") or ""))
        for extra in (entry.get("groupId") or "",
                      entry.get("descriptionText") or ""):
            for term in split_terms(extra):
                if term not in terms:
                    terms.append(term)
        if page_id:
            page_ids.add(page_id)
            bucket = page_terms.setdefault(page_id, [])
            for term in terms:
                if term not in bucket:
                    bucket.append(term)
        target = normalize_deep_link(entry.get("deepLink") or "")
        if target:
            bucket = target_terms.setdefault(target, [])
            for term in terms:
                if term not in bucket:
                    bucket.append(term)
    return SystemSettingsEvidence(
        present=True,
        index_name=str(section.get("indexName", "")),
        entry_count=len(entries),
        page_ids=frozenset(page_ids),
        page_terms={key: tuple(sorted(value))
                    for key, value in page_terms.items()},
        target_terms={key: tuple(sorted(value))
                      for key, value in target_terms.items()},
    )


# =========================================================================
# AVAILABILITY
#
# One resolver, one order, every record. The order is not arbitrary: the
# strongest disqualifier wins so the reason names the true cause. gpedit.msc
# SHIPS on Home and its snap-in refuses to load; checking the file first would
# report it available, and checking edition first reports the actual reason.
# =========================================================================

def _edition_satisfies(gate: str, environment: WindowsEnvironment) -> bool:
    edition = environment.edition_id
    if gate == EDITION_PRO:
        return edition in PRO_EDITION_IDS
    if gate == EDITION_ENTERPRISE:
        return edition in ENTERPRISE_EDITION_IDS
    return True


def _edition_label(gate: str) -> str:
    return "Windows Pro or higher" if gate == EDITION_PRO \
        else "Windows Enterprise or Education"


def _resolve_availability(environment: WindowsEnvironment, *,
                          min_build: int = 0, max_build: int | None = None,
                          requires: tuple = (),
                          page_id: str | None = None,
                          evidence: SystemSettingsEvidence | None = None,
                          file_present: bool | None = None,
                          file_label: str = "",
                          available_reason: str = "") -> tuple:
    """(state, reason). The reason is never empty, in any state."""
    if min_build and environment.build and environment.build < min_build:
        return (UNAVAILABLE,
                f"introduced in Windows build {min_build}; this installation "
                f"is {environment.version_label}")
    if max_build is not None and environment.build \
            and environment.build > max_build:
        return (UNAVAILABLE,
                f"removed after Windows build {max_build}; this installation "
                f"is {environment.version_label}")
    for gate in requires:
        if gate.startswith("edition:") and not _edition_satisfies(
                gate, environment):
            return (UNAVAILABLE,
                    f"requires {_edition_label(gate)}; this installation "
                    f"reports {environment.product_name} "
                    f"(EditionID {environment.edition_id})")
    if file_present is False:
        return (UNAVAILABLE,
                f"{file_label} is not installed on this Windows "
                f"({environment.product_name}, {environment.version_label})")
    if page_id and evidence is not None and evidence.present \
            and page_id not in evidence.page_ids:
        return (UNAVAILABLE,
                f"the installed Windows settings index does not list page "
                f"{page_id}, so this page is not in {environment.version_label}")
    for gate in requires:
        if gate.startswith(("hardware:", "feature:")):
            label = HARDWARE_LABELS.get(gate, gate.split(":", 1)[1])
            return (CONDITIONAL,
                    f"opens on this build, but does nothing useful without "
                    f"{label}; presence of {label} is not something a catalog "
                    f"can determine and is not guessed here")
    if page_id and evidence is not None and evidence.present:
        return (AVAILABLE,
                f"present in the installed Windows settings index as page "
                f"{page_id}")
    if file_present is True:
        return (AVAILABLE, f"{file_label} is installed on this Windows")
    if available_reason:
        return (AVAILABLE, available_reason)
    if evidence is not None and not evidence.present:
        return (AVAILABLE,
                f"documented for Windows build {min_build} and later; the "
                f"local settings index could not corroborate it "
                f"({evidence.absence_reason})")
    return (AVAILABLE,
            f"documented for Windows build {min_build} and later; this "
            f"installation is {environment.version_label}")


# =========================================================================
# ADAPTERS
# =========================================================================

def _merge_aliases(*groups) -> tuple:
    merged = []
    for group in groups:
        for alias in group or ():
            alias = alias.strip().lower()
            if alias and alias not in merged:
                merged.append(alias)
    return tuple(merged)


def ms_settings_records(environment: WindowsEnvironment,
                        evidence: SystemSettingsEvidence
                        ) -> tuple:
    """Adapter 1 -- Microsoft's published, build-gated ms-settings: catalog."""
    records = []
    for page in MS_SETTINGS_PAGES:
        state, reason = _resolve_availability(
            environment, min_build=page.min_build, max_build=page.max_build,
            requires=page.requires, page_id=page.page_id, evidence=evidence)
        local_terms = evidence.page_terms.get(page.page_id or "", ())
        record_evidence = [
            f"Microsoft ms-settings: URI inventory, catalog revision "
            f"{CATALOG_REVISION}",
        ]
        if page.page_id:
            record_evidence.append(
                f"declared page id {page.page_id}"
                + (" corroborated by the local settings index"
                   if evidence.present and page.page_id in evidence.page_ids
                   else " not corroborated by the local settings index"))
        records.append(CatalogRecord(
            id="eos.settings." + page.uri.split(":", 1)[1],
            title=page.title,
            aliases=_merge_aliases(page.aliases),
            search_terms=_merge_aliases(local_terms),
            destination_kind=DEST_MS_SETTINGS,
            destination_target=page.uri,
            category=page.group,
            availability=state,
            availability_reason=reason,
            # The spec is explicit: "Windows Settings must run at Medium
            # integrity." No ms-settings destination is ever marked high.
            integrity=INTEGRITY_MEDIUM,
            source=SOURCE_MICROSOFT,
            topic=page.topic,
            evidence=tuple(record_evidence),
        ))
    return tuple(records)


def _is_third_party(entry: dict) -> bool:
    canonical = (entry.get("canonicalName") or "")
    module = (entry.get("module") or "").lower()
    if canonical.startswith("Microsoft."):
        return False
    if module.startswith("%systemroot%") or module.startswith("%windir%"):
        return False
    if not module and not canonical:
        # Registered by Windows with neither a canonical name nor an out-of-box
        # module reference; treat as local rather than accusing it of being
        # third-party on no evidence.
        return False
    return True


def control_panel_records(namespace: list,
                          environment: WindowsEnvironment,
                          evidence: SystemSettingsEvidence) -> tuple:
    """Adapter 3 -- registered Control Panel namespace items.

    Every registered CLSID becomes a record. An item this project has never
    heard of is still admitted -- that is how a third-party control appears
    automatically -- it simply lands in Administration with the name Windows
    registered for it and whatever vocabulary the local index attached to it.
    """
    records = []
    for entry in namespace or ():
        clsid = (entry.get("clsid") or "").upper()
        canonical = entry.get("canonicalName") or ""
        registered = entry.get("registeredName") or ""
        rule = CONTROL_PANEL_BY_CANONICAL.get(canonical)
        note = CONTROL_PANEL_CLSID_NOTES.get(clsid)
        third_party = _is_third_party(entry)

        if canonical:
            kind = DEST_CONTROL_PANEL
            target = canonical
            record_id = "eos.control." + canonical.lower()
        else:
            kind = DEST_CONTROL_PANEL_CLSID
            target = clsid
            record_id = "eos.control.clsid." + clsid.strip("{}").lower()

        local_terms = _merge_aliases(
            evidence.target_terms.get("clsid:" + clsid, ()),
            evidence.target_terms.get("canonical:" + canonical.lower(), ())
            if canonical else ())

        terms = _merge_aliases(local_terms, split_terms(registered),
                               split_terms(canonical))
        retained = ""
        if rule is not None:
            title = rule.title
            category = rule.group
            topic = rule.topic
            aliases = _merge_aliases(rule.aliases)
            integrity = rule.integrity
            requires = rule.requires
            retained = rule.classic_reason
        elif note is not None:
            title, category, topic, note_aliases = note
            aliases = _merge_aliases(note_aliases)
            integrity = INTEGRITY_MEDIUM
            requires = ()
        else:
            title = registered or canonical or clsid
            category = GROUP_ADMINISTRATION
            topic = ("registered:" + (canonical.lower() if canonical
                                      else clsid.lower()))
            aliases = ()
            integrity = INTEGRITY_MEDIUM
            requires = ()

        state, reason = _resolve_availability(
            environment, requires=requires, evidence=evidence,
            available_reason=("registered in this machine's Control Panel "
                              "namespace" + (f" with canonical name "
                                             f"{canonical}" if canonical
                                             else "")))
        if not canonical:
            state, reason = (CONDITIONAL,
                             "registered in the Control Panel namespace but "
                             "publishes no canonical name, so it has no "
                             "documented launch contract of its own")

        record_evidence = [
            "HKLM\\" + _CP_NAMESPACE_KEY + "\\" + clsid,
        ]
        if canonical:
            record_evidence.append(
                "canonical name System.ApplicationName=" + canonical)
        if entry.get("module"):
            record_evidence.append("registered module " + entry["module"])
        if rule is None and note is None:
            record_evidence.append(
                "no capability rule in control_catalog_data; admitted on its "
                "registration alone and grouped under Administration")

        records.append(CatalogRecord(
            id=record_id,
            title=title,
            aliases=aliases,
            search_terms=terms,
            destination_kind=kind,
            destination_target=target,
            category=category,
            availability=state,
            availability_reason=reason,
            integrity=integrity,
            source=SOURCE_THIRD_PARTY if third_party else SOURCE_LOCAL,
            topic=topic,
            retained_reason=retained,
            evidence=tuple(record_evidence),
        ))
    return tuple(records)


def cpl_records(present_files: list, environment: WindowsEnvironment,
                evidence: SystemSettingsEvidence) -> tuple:
    """Adapter 4a -- .cpl modules, admitted only by capability rule."""
    present = {name.lower() for name in (present_files or ())}
    records = []
    rejections = []
    for module in CPL_MODULES:
        key = module.filename.lower()
        state, reason = _resolve_availability(
            environment, requires=module.requires,
            file_present=key in present,
            file_label=module.filename, evidence=evidence)
        records.append(CatalogRecord(
            id="eos.cpl." + key.rsplit(".", 1)[0],
            title=module.title,
            aliases=_merge_aliases(module.aliases),
            search_terms=_merge_aliases(
                evidence.target_terms.get("cpl:" + key, ())),
            destination_kind=DEST_CPL,
            destination_target=module.filename,
            category=module.group,
            availability=state,
            availability_reason=reason,
            integrity=module.integrity,
            source=SOURCE_LOCAL,
            topic=module.topic,
            retained_reason=module.classic_reason,
            evidence=("capability rule: " + module.reason,
                      "%SystemRoot%\\System32\\" + module.filename
                      + (" present" if key in present else " absent")),
        ))
    for name in sorted(present, key=str.lower):
        if name not in CPL_BY_FILENAME:
            rejections.append(Rejection(
                what="%SystemRoot%\\System32\\" + name,
                kind=DEST_CPL,
                reason=("present on disk but no capability rule admits it; "
                        "the Control Center does not list executables it "
                        "cannot describe")))
    return tuple(records), tuple(rejections)


def mmc_records(present_files: list, environment: WindowsEnvironment,
                evidence: SystemSettingsEvidence) -> tuple:
    """Adapter 4b -- MMC consoles, admitted only by capability rule."""
    present = {name.lower() for name in (present_files or ())}
    records = []
    rejections = []
    for console in MMC_CONSOLES:
        key = console.filename.lower()
        state, reason = _resolve_availability(
            environment, requires=console.requires,
            file_present=key in present,
            file_label=console.filename, evidence=evidence)
        records.append(CatalogRecord(
            id="eos.mmc." + key.rsplit(".", 1)[0],
            title=console.title,
            aliases=_merge_aliases(console.aliases),
            search_terms=_merge_aliases(
                evidence.target_terms.get("mmc:" + key, ())),
            destination_kind=DEST_MMC,
            destination_target=console.filename,
            category=console.group,
            availability=state,
            availability_reason=reason,
            integrity=console.integrity,
            source=SOURCE_LOCAL,
            topic=console.topic,
            evidence=("capability rule: " + console.reason,
                      "%SystemRoot%\\System32\\" + console.filename
                      + (" present" if key in present else " absent")),
        ))
    for name in sorted(present, key=str.lower):
        if name not in MMC_BY_FILENAME:
            rejections.append(Rejection(
                what="%SystemRoot%\\System32\\" + name,
                kind=DEST_MMC,
                reason=("present on disk but no capability rule admits it; an "
                        "MMC console is admitted for what it manages, not for "
                        "existing")))
    return tuple(records), tuple(rejections)


# =========================================================================
# DEDUPLICATION
#
# Two stages, because there are two different duplications.
#
#   ONE DESTINATION, SEVERAL ROUTES. control.exe /name Microsoft.Sound and
#   rundll32 shell32.dll,Control_RunDLL mmsys.cpl open the same window. Two
#   results for one window is the "Duplicate search results that lead to the
#   same page" the spec excludes, so the routes fold into one record and the
#   folded ids are kept in alternate_routes rather than thrown away.
#
#   ONE TOPIC, TWO ERAS. Display exists as a modern page and as desk.cpl.
#   The spec says prefer the modern page and retain materially different
#   classic controls. "Materially different" is a claim, so it must be stated
#   in the data table with its reason; a classic control with no such reason is
#   a duplicate and is dropped WITH a rejection naming what replaced it.
# =========================================================================

def deduplicate(records: tuple) -> tuple:
    """(records, rejections) -- modern preferred, classic retained on merit."""
    by_topic: dict = {}
    for record in records:
        by_topic.setdefault(record.topic, []).append(record)

    kept = []
    rejections = []
    for topic in sorted(by_topic):
        group = by_topic[topic]
        eras: dict = {}
        for record in group:
            eras.setdefault(_ERA[record.destination_kind], []).append(record)

        winners = {}
        for era, members in eras.items():
            members = sorted(members,
                             key=lambda r: (_ROUTE_RANK[r.destination_kind],
                                            r.id))
            primary, *others = members
            folded = tuple(other.id for other in others)
            merged_aliases = _merge_aliases(
                primary.aliases, *(other.aliases for other in others))
            merged_terms = _merge_aliases(
                primary.search_terms, *(other.search_terms for other in others))
            # The primary's own reason wins. Falling back to a folded route's
            # reason only matters when the preferred route declared none.
            retained = primary.retained_reason or next(
                (o.retained_reason for o in others if o.retained_reason), "")
            for other in others:
                rejections.append(Rejection(
                    what=other.id, kind="duplicate-route",
                    reason=(f"reaches the same destination as {primary.id}; "
                            f"folded in as an alternate route rather than "
                            f"listed twice")))
            winners[era] = replace(primary, aliases=merged_aliases,
                                   search_terms=merged_terms,
                                   alternate_routes=folded,
                                   retained_reason=retained)

        modern = winners.get(_ERA_MODERN)
        classic = winners.get(_ERA_CLASSIC)
        if modern is not None:
            kept.append(modern)
        if classic is None:
            continue
        if modern is None:
            kept.append(classic)
            continue
        if not classic.retained_reason:
            rejections.append(Rejection(
                what=classic.id, kind="duplicate-page",
                reason=(f"the modern page {modern.id} reaches the same "
                        f"destination and no materially different capability "
                        f"is declared for the classic control")))
            continue
        kept.append(replace(
            classic,
            flags=_merge_aliases(classic.flags, (FLAG_CLASSIC,)),
            superseded_by=modern.id))
    return tuple(sorted(kept, key=lambda r: r.id)), tuple(rejections)


# =========================================================================
# THE CATALOG
# =========================================================================

@dataclass(frozen=True)
class Catalog:
    revision: str
    environment: WindowsEnvironment
    records: tuple
    rejections: tuple
    evidence: SystemSettingsEvidence

    @property
    def by_id(self) -> dict:
        return {record.id: record for record in self.records}

    def get(self, record_id: str):
        return self.by_id.get(record_id)

    def in_group(self, group_id: str) -> tuple:
        return tuple(record for record in self.records
                     if record.category == group_id)

    def groups(self) -> tuple:
        """(group_id, title, records) in the spec's declared order."""
        return tuple((group_id, title, self.in_group(group_id))
                     for group_id, title in GROUPS)

    def search(self, term: str, *, include_unavailable: bool = True) -> tuple:
        """Rank records against one search term. Nothing disappears.

        The ladder is deliberate and its order is the interesting part: a
        CURATED alias outranks a title substring, and both outrank anything
        harvested from the Windows index. "startup" must reach Startup apps,
        not BitLocker -- whose registered name really is "Secure Startup" --
        and that only works if a term someone chose beats a term someone's
        registry happened to contain.

        `include_unavailable=False` is the GUI's "Show unavailable" being off;
        the default is on, because the spec requires unsupported controls to
        stay visible with a reason.
        """
        needle = (term or "").strip().lower()
        if not needle:
            return ()
        scored = []
        for record in self.records:
            if not include_unavailable and record.availability == UNAVAILABLE:
                continue
            title = record.title.lower()
            score = None
            if needle == title:
                score = 0
            elif needle in record.aliases:
                score = 1
            elif title.startswith(needle):
                score = 2
            elif needle in title:
                score = 3
            elif any(needle == word for alias in record.aliases
                     for word in alias.split()):
                score = 4
            elif any(needle in alias for alias in record.aliases):
                score = 5
            elif needle in record.search_terms:
                score = 6
            elif needle in record.destination_target.lower():
                score = 7
            elif needle in record.id.lower():
                score = 8
            elif any(needle in item for item in record.search_terms):
                score = 9
            if score is None:
                continue
            # A superseded classic control sorts after its modern page at the
            # same relevance, so "display" reaches the modern page first.
            scored.append((score, 1 if record.superseded_by else 0,
                           record.title.lower(), record.id, record))
        scored.sort(key=lambda item: item[:4])
        return tuple(item[4] for item in scored)

    def as_dict(self) -> dict:
        return {
            "revision": self.revision,
            "environment": self.environment.as_dict(),
            "records": [record.as_dict() for record in self.records],
            "rejections": [{"what": r.what, "kind": r.kind, "reason": r.reason}
                           for r in self.rejections],
        }


def build_catalog(snapshot: dict) -> Catalog:
    """Snapshot in, immutable catalog out. Reads nothing but its argument."""
    environment = WindowsEnvironment.from_dict(snapshot.get("environment")
                                               or {})
    evidence = read_system_settings_index(
        snapshot.get("systemSettingsIndex") or {})

    records = list(ms_settings_records(environment, evidence))
    records += list(control_panel_records(
        snapshot.get("controlPanelNamespace") or [], environment, evidence))
    cpl, cpl_rejections = cpl_records(
        snapshot.get("cplModules") or [], environment, evidence)
    mmc, mmc_rejections = mmc_records(
        snapshot.get("mmcConsoles") or [], environment, evidence)
    records += list(cpl) + list(mmc)

    deduped, dedup_rejections = deduplicate(tuple(records))
    _assert_well_formed(deduped)
    return Catalog(
        revision=str(snapshot.get("catalogRevision") or CATALOG_REVISION),
        environment=environment,
        records=deduped,
        rejections=tuple(cpl_rejections) + tuple(mmc_rejections)
        + tuple(dedup_rejections),
        evidence=evidence,
    )


def _assert_well_formed(records: tuple) -> None:
    """Structural invariants, checked where they are cheapest to check.

    A malformed record is a programming error in a data table, not a runtime
    condition, so this raises rather than degrading. It runs on every build --
    including the GUI's -- because the cost is a few hundred string comparisons
    and the alternative is a category typo reaching a user as an empty group.
    """
    seen = set()
    for record in records:
        if record.id in seen:
            raise ValueError(f"duplicate catalog id: {record.id}")
        seen.add(record.id)
        if record.category not in GROUP_IDS:
            raise ValueError(f"{record.id}: unknown category "
                             f"{record.category!r}")
        if record.availability not in AVAILABILITY_STATES:
            raise ValueError(f"{record.id}: unknown availability "
                             f"{record.availability!r}")
        if not record.availability_reason:
            raise ValueError(f"{record.id}: availability with no reason")
        if record.integrity not in INTEGRITY_LEVELS:
            raise ValueError(f"{record.id}: unknown integrity "
                             f"{record.integrity!r}")
        if record.source not in SOURCES:
            raise ValueError(f"{record.id}: unknown source {record.source!r}")
        if record.destination_kind not in DESTINATION_KINDS:
            raise ValueError(f"{record.id}: unknown destination kind "
                             f"{record.destination_kind!r}")
        if not record.destination_target:
            raise ValueError(f"{record.id}: destination with no target")


def build_catalog_from_fixtures(directory: str | None = None) -> Catalog:
    """The offline path the tests use. No registry, no filesystem probing."""
    return build_catalog(load_snapshot(directory))


def build_live_catalog() -> Catalog:
    """The online path. One read-only capture, then the same pure pipeline."""
    return build_catalog(capture_snapshot())


if __name__ == "__main__":                                  # pragma: no cover
    import sys

    if "--capture" in sys.argv:
        for section, path in write_fixtures().items():
            print(f"wrote {section}: {path}")
        raise SystemExit(0)
    catalog = build_catalog_from_fixtures()
    print(f"revision {catalog.revision}  "
          f"{catalog.environment.product_name} "
          f"{catalog.environment.version_label}")
    print(f"{len(catalog.records)} records, "
          f"{len(catalog.rejections)} rejections, "
          f"{catalog.evidence.entry_count} settings-index rows")
    for group_id, title, members in catalog.groups():
        print(f"  {title:<24} {len(members)}")
