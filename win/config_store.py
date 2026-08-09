"""Single-file, versioned configuration storage for EsotericOS.

The store is separate from the product's existing runtime configuration files;
the document must contain no secrets.  Everything except ``hardware`` is
intended to remain portable between machines.
"""

from __future__ import annotations

from dataclasses import dataclass
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from typing import Any, Iterable


CURRENT_SCHEMA_VERSION = 1
DEFAULT_CONFIG_FILENAME = "esotericos_config.json"


@dataclass(frozen=True)
class ConfigLoadResult:
    """Outcome of loading the configuration document."""

    loaded_from_backup: bool
    using_defaults: bool
    problems: tuple[str, ...]


class ConfigStore:
    """Own and preserve one human-readable, versioned JSON document.

    The complete parsed document is retained so unknown fields survive a load
    and save.  Writes use a sibling temporary file followed by ``os.replace``.
    Corrupt input is preserved before the newest valid last-good copy is tried,
    and migrations always receive a backup of the pre-migration document.
    """

    CURRENT_SCHEMA_VERSION = CURRENT_SCHEMA_VERSION

    def __init__(self, root: os.PathLike[str] | str | None = None,
                 migrations: Iterable[Any] | None = None) -> None:
        env_root = os.environ.get("ESOTERICOS_DATA_DIR")
        if root is not None:
            data_root = Path(root)
        elif env_root and env_root.strip():
            data_root = Path(env_root)
        elif getattr(sys, "frozen", False):
            # Frozen: __file__ points inside the PyInstaller bundle, which is
            # wiped on exit. The exe sits at the product root -- anchor there,
            # exactly as openspan.py does.
            data_root = Path(sys.executable).resolve().parent
        else:
            data_root = Path(__file__).resolve().parent.parent

        self.root_directory = data_root
        self.config_directory = data_root / "config"
        self.data_directory = data_root / "data"
        self.log_directory = data_root / "logs"
        self.config_file = self.config_directory / DEFAULT_CONFIG_FILENAME
        self.backup_directory = self.config_directory / "backups"
        self._last_good_file = self.config_directory / "config.last-good.json"
        self._migrations = tuple(migrations or ())
        self._lock = threading.RLock()
        self._root = self._create_default()
        self._write_protected = False
        self.load_result = ConfigLoadResult(False, True, ())
        self._load()

    def _create_default(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.CURRENT_SCHEMA_VERSION,
            "features": {},
            "shortcuts": {},
            "hardware": {},
        }

    def _ensure_created(self) -> None:
        self.config_directory.mkdir(parents=True, exist_ok=True)
        self.data_directory.mkdir(parents=True, exist_ok=True)
        self.log_directory.mkdir(parents=True, exist_ok=True)

    def _load(self) -> None:
        with self._lock:
            problems: list[str] = []
            if not self.config_file.exists():
                self._root = self._create_default()
                self.load_result = ConfigLoadResult(False, True, ())
                return

            root = self._try_load_file(self.config_file, problems)
            if root is not None and self._finish_load(
                    root, problems, from_backup=False):
                return

            self._preserve_corrupt(self.config_file)
            if self._last_good_file.exists():
                last_good = self._try_load_file(self._last_good_file, problems)
                if last_good is not None:
                    problems.append(
                        "Configuration was corrupt; restored the last valid copy.")
                    if self._finish_load(last_good, problems, from_backup=True):
                        return

            problems.append(
                "Configuration was unusable and no valid backup was found; "
                "using safe defaults.")
            self._root = self._create_default()
            self.load_result = ConfigLoadResult(False, True, tuple(problems))

    @staticmethod
    def _try_load_file(path: Path, problems: list[str]) -> dict[str, Any] | None:
        try:
            with path.open("r", encoding="utf-8") as stream:
                root = json.load(stream)
        except json.JSONDecodeError as exc:
            problems.append(
                f"{path.name}: invalid JSON at {exc.lineno}:{exc.colno} — {exc.msg}")
            return None
        except (OSError, UnicodeError) as exc:
            problems.append(f"{path.name}: {exc}")
            return None
        if not isinstance(root, dict):
            problems.append(f"{path.name}: root is not a JSON object.")
            return None
        return root

    def _finish_load(self, root: dict[str, Any], problems: list[str],
                     *, from_backup: bool) -> bool:
        raw_version = root.get("schemaVersion", 0)
        if isinstance(raw_version, bool) or not isinstance(raw_version, int):
            problems.append("schemaVersion is not an integer.")
            return False
        version = raw_version

        if version > self.CURRENT_SCHEMA_VERSION:
            problems.append(
                f"Configuration schema v{version} is newer than this build "
                f"(v{self.CURRENT_SCHEMA_VERSION}); running with defaults and "
                "leaving the file untouched.")
            self._root = self._create_default()
            self._write_protected = True
            self.load_result = ConfigLoadResult(
                from_backup, True, tuple(problems))
            return True

        if version < self.CURRENT_SCHEMA_VERSION:
            self._backup_before_migration(version)
            while version < self.CURRENT_SCHEMA_VERSION:
                migration = next(
                    (item for item in self._migrations
                     if item.from_version == version), None)
                if migration is None:
                    problems.append(
                        f"No migration path from schema v{version}; "
                        "using safe defaults.")
                    return False
                migration.apply(root)
                version += 1
                root["schemaVersion"] = version
            problems.append(
                f"Configuration migrated to schema "
                f"v{self.CURRENT_SCHEMA_VERSION} (backup saved first).")

        self._root = root
        self.load_result = ConfigLoadResult(
            from_backup, False, tuple(problems))
        self._snapshot_last_good()
        if from_backup:
            self.save()
        return True

    def _preserve_corrupt(self, source: Path) -> None:
        try:
            self.config_directory.mkdir(parents=True, exist_ok=True)
            stamp = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copyfile(
                source, self.config_directory / f"config.corrupt-{stamp}.json")
        except OSError:
            pass

    def _backup_before_migration(self, from_version: int) -> None:
        try:
            self.backup_directory.mkdir(parents=True, exist_ok=True)
            stamp = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copyfile(
                self.config_file,
                self.backup_directory /
                f"config-v{from_version}-{stamp}.json")
        except OSError:
            pass

    def _snapshot_last_good(self) -> None:
        try:
            if self.config_file.exists():
                shutil.copyfile(self.config_file, self._last_good_file)
        except OSError:
            pass

    @staticmethod
    def _json_text(root: dict[str, Any]) -> str:
        return json.dumps(root, indent=2, ensure_ascii=False, allow_nan=False)

    def save(self) -> None:
        """Atomically persist the whole document unless it is write-protected."""
        with self._lock:
            if self._write_protected:
                return
            self._ensure_created()
            temporary = Path(f"{self.config_file}.tmp")
            try:
                with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                    stream.write(self._json_text(self._root))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.config_file)
            finally:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
            self._snapshot_last_good()

    def get_feature_enabled(self, feature_id: str) -> bool | None:
        with self._lock:
            features = self._root.get("features")
            feature = features.get(feature_id) if isinstance(features, dict) else None
            enabled = feature.get("enabled") if isinstance(feature, dict) else None
            return enabled if isinstance(enabled, bool) else None

    def set_feature_enabled(self, feature_id: str,
                            enabled: bool | None) -> None:
        if enabled is not None and not isinstance(enabled, bool):
            raise TypeError("enabled must be bool or None")
        with self._lock:
            feature = self._get_or_create_feature(feature_id)
            if enabled is None:
                feature.pop("enabled", None)
            else:
                feature["enabled"] = enabled
            self.save()

    @staticmethod
    def _matches_fallback_type(value: Any, fallback: Any) -> bool:
        if fallback is None:
            return True
        if isinstance(fallback, bool):
            return isinstance(value, bool)
        if isinstance(fallback, int):
            return isinstance(value, int) and not isinstance(value, bool)
        if isinstance(fallback, float):
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        return isinstance(value, type(fallback))

    def get_feature_setting(self, feature_id: str, key: str,
                            fallback: Any = None) -> Any:
        with self._lock:
            features = self._root.get("features")
            feature = features.get(feature_id) if isinstance(features, dict) else None
            settings = feature.get("settings") if isinstance(feature, dict) else None
            if not isinstance(settings, dict) or key not in settings:
                return fallback
            value = settings[key]
            if value is None or not self._matches_fallback_type(value, fallback):
                return fallback
            return copy.deepcopy(value)

    def set_feature_setting(self, feature_id: str, key: str,
                            value: Any) -> None:
        # Round-trip first both validates JSON compatibility and detaches caller data.
        normalized = json.loads(json.dumps(value, allow_nan=False))
        with self._lock:
            feature = self._get_or_create_feature(feature_id)
            settings = feature.get("settings")
            if not isinstance(settings, dict):
                settings = {}
                feature["settings"] = settings
            settings[key] = normalized
            self.save()

    def get_shortcut_overrides(self, command_id: str) -> list[str] | None:
        with self._lock:
            shortcuts = self._root.get("shortcuts")
            sequences = shortcuts.get(command_id) if isinstance(shortcuts, dict) else None
            if (not isinstance(sequences, list)
                    or not all(isinstance(item, str) for item in sequences)):
                return None
            return list(sequences)

    def set_shortcut_overrides(self, command_id: str,
                               sequences: Iterable[str] | None) -> None:
        normalized = None if sequences is None else list(sequences)
        if normalized is not None and not all(
                isinstance(item, str) for item in normalized):
            raise TypeError("shortcut sequences must all be strings")
        with self._lock:
            shortcuts = self._root.get("shortcuts")
            if not isinstance(shortcuts, dict):
                shortcuts = {}
                self._root["shortcuts"] = shortcuts
            if normalized is None:
                shortcuts.pop(command_id, None)
            else:
                shortcuts[command_id] = normalized
            self.save()

    def get_hardware_section(self) -> dict[str, Any]:
        with self._lock:
            hardware = self._root.get("hardware")
            if not isinstance(hardware, dict):
                hardware = {}
                self._root["hardware"] = hardware
            return hardware

    def reset_feature(self, feature_id: str) -> None:
        with self._lock:
            features = self._root.get("features")
            if isinstance(features, dict):
                features.pop(feature_id, None)
            self.save()

    def reset_all(self) -> None:
        with self._lock:
            self._root = self._create_default()
            self._write_protected = False
            self.save()

    def export(self, path: os.PathLike[str] | str) -> None:
        with self._lock:
            Path(path).write_text(
                self._json_text(self._root), encoding="utf-8", newline="\n")

    def import_(self, path: os.PathLike[str] | str) -> None:
        problems: list[str] = []
        root = self._try_load_file(Path(path), problems)
        if root is None:
            raise ValueError(f"Import failed: {'; '.join(problems)}")
        with self._lock:
            self._write_protected = False
            if not self._finish_load(root, problems, from_backup=False):
                raise ValueError(f"Import failed: {'; '.join(problems)}")
            self.save()

    def _get_or_create_feature(self, feature_id: str) -> dict[str, Any]:
        features = self._root.get("features")
        if not isinstance(features, dict):
            features = {}
            self._root["features"] = features
        feature = features.get(feature_id)
        if not isinstance(feature, dict):
            feature = {}
            features[feature_id] = feature
        return feature


class _ProbeMigration:
    from_version = 0

    def apply(self, root: dict[str, Any]) -> None:
        root["probeMigration"] = "applied"


def _probe() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = ConfigStore(directory)
        print(f"fresh: defaults={store.load_result.using_defaults}")
        store.set_feature_enabled("probe", True)
        store.set_feature_setting("probe", "message", "saved")
        print("saved: enabled=True message=saved")
        store.config_file.write_text("{ deliberately corrupt", encoding="utf-8")
        recovered = ConfigStore(directory)
        print(
            "recovered: "
            f"backup={recovered.load_result.loaded_from_backup} "
            f"message={recovered.get_feature_setting('probe', 'message')}")
        recovered.config_file.write_text(
            '{"schemaVersion": 0, "features": {}, "probe": "v0"}',
            encoding="utf-8")
        migrated = ConfigStore(directory, migrations=[_ProbeMigration()])
        print(
            "migrated: "
            f"schema={migrated._root['schemaVersion']} "
            f"marker={migrated._root['probeMigration']}")
        print("probe: PASS")


if __name__ == "__main__":
    _probe()
