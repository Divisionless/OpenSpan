"""Contract checks for the unified settings orchestration service."""

import json
from pathlib import Path
import tempfile

from config_store import ConfigStore
from settings_service import (
    FeatureDeclaration,
    FeatureRegistry,
    SECTIONS,
    SettingsService,
)


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


def feature(feature_id, title, *, section="Features", enabled=True,
            shortcuts=None, settings=None, hooks=()):
    return FeatureDeclaration(
        feature_id, title, section, enabled,
        shortcuts or {}, settings or {}, hooks)


with tempfile.TemporaryDirectory() as directory:
    registry = FeatureRegistry([
        feature("planned-on", "Planned On", enabled=True),
        feature("planned-off", "Planned Off", enabled=False),
    ])
    service = SettingsService(ConfigStore(directory), registry)
    check("registry defaults supply missing enablement",
          service.is_enabled("planned-on") is True
          and service.is_enabled("planned-off") is False)
    service.set_enabled("planned-on", False)
    service.set_enabled("planned-off", True)
    check("stored enablement overrides registry defaults",
          service.is_enabled("planned-on") is False
          and service.is_enabled("planned-off") is True)
    check("section tree keeps the old program's four section names",
          tuple(service.section_tree()) == SECTIONS)

with tempfile.TemporaryDirectory() as directory:
    registry = FeatureRegistry([
        feature("typed", "Typed", settings={
            "count": 4, "ratio": 1.5, "label": "normal", "flags": ["a"]})
    ])
    store = ConfigStore(directory)
    service = SettingsService(store, registry)
    check("typed setting uses its declared fallback",
          service.get_setting("typed", "count") == 4
          and service.get_setting("typed", "label") == "normal")
    service.set_setting("typed", "count", 9)
    service.set_setting("typed", "ratio", 2)
    service.set_setting("typed", "flags", ["b", "c"])
    check("typed setting stores compatible values",
          service.get_setting("typed", "count") == 9
          and service.get_setting("typed", "ratio") == 2
          and service.get_setting("typed", "flags") == ["b", "c"])
    store.set_feature_setting("typed", "count", "wrong type")
    check("typed setting falls back when stored data has the wrong type",
          service.get_setting("typed", "count") == 4)
    try:
        service.set_setting("typed", "count", True)
    except TypeError:
        wrong_type_rejected = True
    else:
        wrong_type_rejected = False
    check("typed setting rejects incompatible writes", wrong_type_rejected)

with tempfile.TemporaryDirectory() as directory:
    registry = FeatureRegistry([
        feature("first", "First", enabled=True, settings={"size": 10}),
        feature("second", "Second", enabled=False),
    ])
    store = ConfigStore(directory)
    service = SettingsService(store, registry)
    service.set_enabled("first", False)
    service.set_setting("first", "size", 20)
    service.set_enabled("second", True)
    service.reset_feature("first")
    check("per-feature reset restores only the target's defaults",
          service.is_enabled("first") is True
          and service.get_setting("first", "size") == 10
          and service.is_enabled("second") is True)
    service.reset_all()
    check("global reset restores every registry default",
          service.is_enabled("first") is True
          and service.is_enabled("second") is False)

with tempfile.TemporaryDirectory() as directory:
    registry = FeatureRegistry([feature("portable", "Portable")])
    store = ConfigStore(directory)
    service = SettingsService(store, registry)
    service.set_enabled("portable", False)
    target = Path(directory) / "settings.json"
    export_problems = service.export(target)
    service.set_enabled("portable", True)
    import_problems = service.import_(target)
    check("export and import delegate a valid round-trip",
          export_problems == [] and import_problems == []
          and service.is_enabled("portable") is False)

    invalid = Path(directory) / "invalid.json"
    invalid.write_text("not json", encoding="utf-8")
    service.set_enabled("portable", True)
    problems = service.import_(invalid)
    check("invalid import reports problems instead of raising",
          len(problems) == 1 and "invalid JSON" in problems[0])
    check("invalid import leaves current settings untouched",
          service.is_enabled("portable") is True)

    malformed = Path(directory) / "malformed.json"
    malformed.write_text(json.dumps({
        "schemaVersion": ConfigStore.CURRENT_SCHEMA_VERSION,
        "features": {}, "shortcuts": {"bad": ["Win+Shift"]},
        "hardware": {},
    }), encoding="utf-8")
    malformed_problems = service.import_(malformed)
    check("import validation uses the router's chord parser",
          len(malformed_problems) == 1
          and "Unknown key token" in malformed_problems[0])

with tempfile.TemporaryDirectory() as directory:
    registry = FeatureRegistry([
        feature("capture", "Capture", shortcuts={
            "capture.first": ("Win+Shift+S",),
            "capture.prefix": ("Ctrl+Alt+L",),
            "capture.none": ("Win+1",),
        }),
        feature("tools", "Tools", shortcuts={
            "tools.same": ("win+shift+s",),
            "tools.long": ("control+option+l, then Ctrl+Alt+U",),
            "tools.none": ("Win+2",),
        }),
    ])
    store = ConfigStore(directory)
    service = SettingsService(store, registry)
    collisions = service.shortcut_collisions()
    check("collision detection canonicalizes case differences",
          any(item.kind == "identical"
              and item.first_sequence == "Win+Shift+S"
              for item in collisions))
    check("collision detection canonicalizes modifier aliases",
          any(item.kind == "prefix"
              and item.first_sequence == "Ctrl+Alt+L"
              and item.second_sequence == "Ctrl+Alt+L, then Ctrl+Alt+U"
              for item in collisions))
    check("collision detection finds identical chords and strict prefixes",
          [item.kind for item in collisions] == ["identical", "prefix"])

    store.set_shortcut_overrides("tools.same", ["Win+3"])
    store.set_shortcut_overrides("tools.long", ["Ctrl+Alt+U"])
    check("collision detection reports the no-collision case",
          service.shortcut_collisions() == ())

with tempfile.TemporaryDirectory() as directory:
    registry = FeatureRegistry([
        feature("keyboard", "Keyboard", hooks=(
            "Low-level keyboard hook", "Input source watcher")),
        feature("ordinary", "Ordinary"),
        feature("pointer", "Pointer", hooks=("Low-level mouse hook",)),
    ])
    service = SettingsService(ConfigStore(directory), registry)
    disclosure = service.hook_disclosure()
    check("hook disclosure lists exactly features that request hooks",
          [(item.feature_id, item.hooks) for item in disclosure] == [
              ("keyboard", ("Low-level keyboard hook", "Input source watcher")),
              ("pointer", ("Low-level mouse hook",)),
          ])
