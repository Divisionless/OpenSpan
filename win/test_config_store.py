"""Contract checks for the standalone versioned configuration store."""

import json
import os
from pathlib import Path
import tempfile
from unittest import mock

import config_store
from config_store import ConfigStore


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


def write_document(root, document):
    target = Path(root) / "config" / config_store.DEFAULT_CONFIG_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document, encoding="utf-8")
    return target


class Migration:
    def __init__(self, from_version, marker, order):
        self.from_version = from_version
        self.marker = marker
        self.order = order

    def apply(self, root):
        self.order.append(self.marker)
        root.setdefault("migrationMarkers", []).append(self.marker)


with tempfile.TemporaryDirectory() as directory:
    store = ConfigStore(directory)
    check("missing file uses safe defaults",
          store.load_result.using_defaults
          and not store.load_result.loaded_from_backup
          and store.get_feature_enabled("missing") is None)
    check("missing file does not write eagerly", not store.config_file.exists())

with tempfile.TemporaryDirectory() as env_directory, \
        tempfile.TemporaryDirectory() as explicit_directory:
    with mock.patch.dict(os.environ, {"ESOTERICOS_DATA_DIR": env_directory}):
        env_store = ConfigStore()
        explicit_store = ConfigStore(explicit_directory)
    check("environment selects the portable data root",
          env_store.root_directory == Path(env_directory))
    check("explicit root takes precedence over the environment",
          explicit_store.root_directory == Path(explicit_directory))

with tempfile.TemporaryDirectory() as frozen_directory:
    # Frozen builds must anchor on the executable: __file__ then points inside
    # a PyInstaller bundle that is deleted on exit, taking the config with it.
    fake_exe = Path(frozen_directory) / "EsotericOS.exe"
    fake_exe.write_bytes(b"")
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ESOTERICOS_DATA_DIR", None)
        with mock.patch.object(config_store.sys, "frozen", True, create=True), \
                mock.patch.object(config_store.sys, "executable", str(fake_exe)):
            frozen_store = ConfigStore()
    check("a frozen build anchors on the executable, not the bundle",
          frozen_store.root_directory == Path(frozen_directory).resolve())
    explicit_store.save()
    check("save creates config, data, and log directories",
          all(path.is_dir() for path in
              (explicit_store.config_directory, explicit_store.data_directory,
               explicit_store.log_directory)))
    check("store owns the requested new filename",
          explicit_store.config_file.name == "esotericos_config.json")

with tempfile.TemporaryDirectory() as directory:
    store = ConfigStore(directory)
    store.save()
    raw = json.loads(store.config_file.read_text(encoding="utf-8"))
    raw["futureSection"] = {"novel": "keep-me"}
    raw["features"]["known"] = {"futureField": 42}
    store.config_file.write_text(json.dumps(raw), encoding="utf-8")
    reloaded = ConfigStore(directory)
    reloaded.set_feature_enabled("another", True)
    after = json.loads(reloaded.config_file.read_text(encoding="utf-8"))
    check("unknown top-level fields survive a round trip",
          after["futureSection"] == {"novel": "keep-me"})
    check("unknown nested fields survive a round trip",
          after["features"]["known"]["futureField"] == 42)

with tempfile.TemporaryDirectory() as directory:
    store = ConfigStore(directory)
    with mock.patch.object(config_store.os, "replace",
                           wraps=config_store.os.replace) as replace:
        store.save()
    source, destination = replace.call_args.args
    check("save commits through a sibling temporary file",
          Path(source) == Path(f"{store.config_file}.tmp")
          and Path(destination) == store.config_file)
    check("atomic save leaves no temporary file behind",
          store.config_file.exists() and not Path(source).exists())

with tempfile.TemporaryDirectory() as directory:
    store = ConfigStore(directory)
    store.set_feature_enabled("recovery", False)
    store.config_file.write_text("{ not valid json", encoding="utf-8")
    recovered = ConfigStore(directory)
    check("corrupt input recovers from last-good",
          recovered.load_result.loaded_from_backup
          and recovered.get_feature_enabled("recovery") is False)
    check("recovery reports the last valid copy",
          any("restored the last valid copy" in problem
              for problem in recovered.load_result.problems))
    check("corrupt input is preserved",
          len(list(recovered.config_directory.glob("config.corrupt-*.json"))) == 1)

with tempfile.TemporaryDirectory() as directory:
    target = write_document(directory, '["not an object"]')
    store = ConfigStore(directory)
    check("unusable input without backup uses safe defaults",
          store.load_result.using_defaults
          and not store.load_result.loaded_from_backup)
    check("non-object input reports both cause and fallback",
          any("root is not a JSON object" in p for p in store.load_result.problems)
          and any("no valid backup" in p for p in store.load_result.problems))
    check("unusable original remains untouched",
          target.read_text() == '["not an object"]')

with tempfile.TemporaryDirectory() as directory:
    future = '{ "schemaVersion": 999, "fromTheFuture": true }'
    target = write_document(directory, future)
    store = ConfigStore(directory)
    store.set_feature_enabled("would-clobber", True)
    check("newer schema runs on defaults", store.load_result.using_defaults)
    check("newer schema is write-protected and untouched",
          target.read_text(encoding="utf-8") == future)

with tempfile.TemporaryDirectory() as directory:
    original_version = ConfigStore.CURRENT_SCHEMA_VERSION
    ConfigStore.CURRENT_SCHEMA_VERSION = 2
    try:
        original = '{"schemaVersion": 0, "features": {}, "legacy": "old"}'
        target = write_document(directory, original)
        order = []
        store = ConfigStore(
            directory,
            migrations=[Migration(0, "zero-to-one", order),
                        Migration(1, "one-to-two", order)])
        backups = list(store.backup_directory.glob("config-v0-*.json"))
        check("migration chain runs in version order",
              order == ["zero-to-one", "one-to-two"]
              and store._root["schemaVersion"] == 2)
        check("migration reports success",
              any("migrated to schema v2" in p
                  for p in store.load_result.problems))
        check("pre-migration backup preserves the original",
              len(backups) == 1
              and backups[0].read_text(encoding="utf-8") == original
              and target.read_text(encoding="utf-8") == original)
    finally:
        ConfigStore.CURRENT_SCHEMA_VERSION = original_version

with tempfile.TemporaryDirectory() as directory:
    write_document(directory, '{"schemaVersion": 0, "features": {}}')
    store = ConfigStore(directory)
    check("missing migration path uses safe defaults",
          store.load_result.using_defaults
          and any("No migration path from schema v0" in p
                  for p in store.load_result.problems))

with tempfile.TemporaryDirectory() as directory:
    store = ConfigStore(directory)
    store.set_feature_enabled("exported", False)
    export_path = Path(directory) / "exported.json"
    store.export(export_path)
    store.reset_all()
    store.import_(export_path)
    check("export and import round-trip",
          store.get_feature_enabled("exported") is False)
    bad_path = Path(directory) / "bad.json"
    bad_path.write_text("not json at all", encoding="utf-8")
    try:
        store.import_(bad_path)
    except ValueError as exc:
        import_error = str(exc)
    else:
        import_error = ""
    check("invalid import raises an actionable collected error",
          import_error.startswith("Import failed:")
          and "invalid JSON" in import_error)

with tempfile.TemporaryDirectory() as directory:
    store = ConfigStore(directory)
    store.set_feature_enabled("first", False)
    store.set_feature_enabled("second", True)
    store.set_feature_setting("first", "count", 250)
    store.set_feature_setting("first", "object", {"nested": [1, True, None]})
    store.set_shortcut_overrides("tile.left", ["Ctrl+Alt+H"])
    hardware = store.get_hardware_section()
    hardware["machine"] = {"display": "local"}
    store.save()
    reloaded = ConfigStore(directory)
    check("feature enabled accessor round-trips booleans",
          reloaded.get_feature_enabled("first") is False)
    check("feature setting accessor round-trips JSON values",
          reloaded.get_feature_setting("first", "count", 0) == 250
          and reloaded.get_feature_setting("first", "object", {})
          == {"nested": [1, True, None]})
    check("shortcut accessor round-trips overrides",
          reloaded.get_shortcut_overrides("tile.left") == ["Ctrl+Alt+H"])
    check("hardware accessor owns the machine-specific section",
          reloaded.get_hardware_section() == {"machine": {"display": "local"}})
    reloaded.reset_feature("first")
    check("reset feature clears only its target",
          reloaded.get_feature_enabled("first") is None
          and reloaded.get_feature_enabled("second") is True)
    reloaded.set_shortcut_overrides("tile.left", None)
    check("null shortcut overrides restore defaults",
          reloaded.get_shortcut_overrides("tile.left") is None)
    reloaded.reset_all()
    check("reset all restores the complete safe default",
          reloaded.load_result.using_defaults is False
          and reloaded.get_feature_enabled("second") is None
          and reloaded.get_hardware_section() == {})

with tempfile.TemporaryDirectory() as directory:
    target = write_document(
        directory,
        '{"schemaVersion": 1, "features": '
        '{"bad": {"settings": {"count": "many"}}}}')
    store = ConfigStore(directory)
    check("feature setting returns fallback on bad data",
          store.get_feature_setting("bad", "count", 12) == 12)
    check("bad setting data is not rewritten during load",
          '"many"' in target.read_text(encoding="utf-8"))

source = Path(config_store.__file__).read_text(encoding="utf-8")
reserved_runtime_names = (
    "openspan_config.json",
    "openspan_settings.json",
    "openspan_keymap.json",
    "bt_prefs.json",
    "mode.txt",
)
check("module never names a legacy runtime configuration file",
      not any(name in source for name in reserved_runtime_names))
