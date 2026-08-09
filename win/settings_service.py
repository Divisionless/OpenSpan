"""Unified, UI-free settings orchestration for EsotericOS.

The service owns declarations and policy only.  Persistence stays in
``ConfigStore`` and shortcut spelling stays in ``keyboard_interception``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import copy
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from config_store import ConfigStore
from keyboard_interception import KeySequence


SECTIONS = ("Features", "Shortcuts", "Configuration", "Status")


def _json_copy(value: Any) -> Any:
    """Validate a declared value as portable JSON and detach it."""
    return json.loads(json.dumps(value, allow_nan=False))


def _matches_declared_type(value: Any, default: Any) -> bool:
    if default is None:
        return True
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, type(default))


@dataclass(frozen=True, slots=True)
class FeatureDeclaration:
    """Everything the settings service needs to know about one feature.

    ``default_shortcuts`` maps command ids to one or more sequence strings.
    An empty tuple declares a command which is unbound by default.
    ``input_hooks`` contains human-readable hook descriptions, not hook
    objects; the settings service never installs or owns a hook.
    """

    id: str
    title: str
    section: str
    default_enabled: bool
    default_shortcuts: Mapping[str, Iterable[str]] = field(default_factory=dict)
    default_settings: Mapping[str, Any] = field(default_factory=dict)
    input_hooks: Iterable[str] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("feature id must be a non-empty string")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("feature title must be a non-empty string")
        if self.section not in SECTIONS:
            raise ValueError(
                f"section must be one of {', '.join(SECTIONS)}")
        if not isinstance(self.default_enabled, bool):
            raise TypeError("default_enabled must be bool")
        if not isinstance(self.default_shortcuts, Mapping):
            raise TypeError("default_shortcuts must map command ids to sequences")
        if not isinstance(self.default_settings, Mapping):
            raise TypeError("default_settings must be a mapping")

        shortcuts: dict[str, tuple[str, ...]] = {}
        for command_id, raw_sequences in self.default_shortcuts.items():
            if not isinstance(command_id, str) or not command_id.strip():
                raise ValueError("shortcut command ids must be non-empty strings")
            if isinstance(raw_sequences, str):
                sequences = (raw_sequences,)
            else:
                sequences = tuple(raw_sequences)
            if not all(isinstance(item, str) for item in sequences):
                raise TypeError("shortcut sequences must all be strings")
            for sequence in sequences:
                KeySequence.parse(sequence)
            shortcuts[command_id] = sequences

        settings: dict[str, Any] = {}
        for key, default in self.default_settings.items():
            if not isinstance(key, str) or not key:
                raise ValueError("setting keys must be non-empty strings")
            settings[key] = _json_copy(default)

        hooks = tuple(self.input_hooks)
        if not all(isinstance(item, str) and item.strip() for item in hooks):
            raise ValueError("input hook descriptions must be non-empty strings")

        object.__setattr__(self, "id", self.id.strip())
        object.__setattr__(self, "title", self.title.strip())
        object.__setattr__(self, "default_shortcuts", shortcuts)
        object.__setattr__(self, "default_settings", settings)
        object.__setattr__(self, "input_hooks", hooks)


class FeatureRegistry:
    """Ordered collection of validated feature declarations."""

    def __init__(self, features: Iterable[FeatureDeclaration] = ()) -> None:
        self._features: dict[str, FeatureDeclaration] = {}
        self._command_owners: dict[str, str] = {}
        for feature in features:
            self.register(feature)

    def register(self, feature: FeatureDeclaration) -> None:
        if not isinstance(feature, FeatureDeclaration):
            raise TypeError("feature must be a FeatureDeclaration")
        if feature.id in self._features:
            raise ValueError(f"feature '{feature.id}' is already registered")
        for command_id in feature.default_shortcuts:
            owner = self._command_owners.get(command_id)
            if owner is not None:
                raise ValueError(
                    f"shortcut command '{command_id}' is already owned by '{owner}'")
        self._features[feature.id] = feature
        for command_id in feature.default_shortcuts:
            self._command_owners[command_id] = feature.id

    def get(self, feature_id: str) -> FeatureDeclaration:
        try:
            return self._features[feature_id]
        except KeyError:
            raise KeyError(f"unknown feature '{feature_id}'") from None

    @property
    def features(self) -> tuple[FeatureDeclaration, ...]:
        return tuple(self._features.values())

    def in_section(self, section: str) -> tuple[FeatureDeclaration, ...]:
        if section not in SECTIONS:
            raise ValueError(f"unknown section '{section}'")
        return tuple(item for item in self._features.values()
                     if item.section == section)


@dataclass(frozen=True, slots=True)
class FeatureState:
    id: str
    title: str
    section: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class ShortcutCollision:
    """One pair of different commands whose bindings cannot coexist."""

    kind: str
    first_command: str
    first_sequence: str
    second_command: str
    second_sequence: str

    @property
    def message(self) -> str:
        if self.kind == "identical":
            return (f"{self.first_command} and {self.second_command} both claim "
                    f"{self.first_sequence}")
        return (f"{self.first_command} ({self.first_sequence}) conflicts with "
                f"{self.second_command} ({self.second_sequence}): one sequence "
                "is a strict prefix of the other")


@dataclass(frozen=True, slots=True)
class HookDisclosure:
    feature_id: str
    title: str
    hooks: tuple[str, ...]


class SettingsService:
    """Pure orchestration over a ``ConfigStore`` and ``FeatureRegistry``."""

    def __init__(self, store: ConfigStore, registry: FeatureRegistry) -> None:
        if not isinstance(store, ConfigStore):
            raise TypeError("store must be a ConfigStore")
        if not isinstance(registry, FeatureRegistry):
            raise TypeError("registry must be a FeatureRegistry")
        self.store = store
        self.registry = registry

    def get_feature_enabled(self, feature_id: str) -> bool:
        feature = self.registry.get(feature_id)
        stored = self.store.get_feature_enabled(feature_id)
        return feature.default_enabled if stored is None else stored

    def set_feature_enabled(self, feature_id: str, enabled: bool) -> None:
        self.registry.get(feature_id)
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be bool")
        self.store.set_feature_enabled(feature_id, enabled)

    # Short names are convenient for callers and retain the explicit names above.
    is_enabled = get_feature_enabled
    set_enabled = set_feature_enabled

    def get_setting(self, feature_id: str, key: str) -> Any:
        feature = self.registry.get(feature_id)
        try:
            fallback = feature.default_settings[key]
        except KeyError:
            raise KeyError(
                f"feature '{feature_id}' has no declared setting '{key}'") from None
        return self.store.get_feature_setting(
            feature_id, key, copy.deepcopy(fallback))

    def set_setting(self, feature_id: str, key: str, value: Any) -> None:
        feature = self.registry.get(feature_id)
        try:
            fallback = feature.default_settings[key]
        except KeyError:
            raise KeyError(
                f"feature '{feature_id}' has no declared setting '{key}'") from None
        normalized = _json_copy(value)
        if not _matches_declared_type(normalized, fallback):
            expected = "any JSON value" if fallback is None else type(fallback).__name__
            raise TypeError(f"setting '{key}' must have type {expected}")
        self.store.set_feature_setting(feature_id, key, normalized)

    def section_tree(self) -> dict[str, tuple[FeatureState, ...]]:
        return {
            section: tuple(
                FeatureState(item.id, item.title, item.section,
                             self.get_feature_enabled(item.id))
                for item in self.registry.in_section(section))
            for section in SECTIONS
        }

    def reset_feature(self, feature_id: str) -> None:
        self.registry.get(feature_id)
        self.store.reset_feature(feature_id)

    def reset_all(self) -> None:
        self.store.reset_all()

    @staticmethod
    def _validate_transfer_path(path: os.PathLike[str] | str) -> tuple[Path | None,
                                                                        list[str]]:
        try:
            target = Path(path)
        except (TypeError, ValueError) as exc:
            return None, [f"Invalid path: {exc}"]
        if not str(target).strip():
            return None, ["Invalid path: path is empty."]
        return target, []

    @staticmethod
    def _validate_import_document(path: Path) -> list[str]:
        problems: list[str] = []
        try:
            with path.open("r", encoding="utf-8") as stream:
                root = json.load(stream)
        except json.JSONDecodeError as exc:
            return [f"{path.name}: invalid JSON at {exc.lineno}:{exc.colno} — {exc.msg}"]
        except (OSError, UnicodeError) as exc:
            return [f"{path.name}: {exc}"]
        if not isinstance(root, dict):
            return [f"{path.name}: root is not a JSON object."]

        version = root.get("schemaVersion", 0)
        if isinstance(version, bool) or not isinstance(version, int):
            problems.append("schemaVersion is not an integer.")
        elif version > ConfigStore.CURRENT_SCHEMA_VERSION:
            problems.append(
                f"Configuration schema v{version} is newer than this build "
                f"(v{ConfigStore.CURRENT_SCHEMA_VERSION}).")

        if version == ConfigStore.CURRENT_SCHEMA_VERSION:
            for section in ("features", "shortcuts", "hardware"):
                if section in root and not isinstance(root[section], dict):
                    problems.append(f"{section} is not a JSON object.")
            features = root.get("features", {})
            if isinstance(features, dict):
                for feature_id, value in features.items():
                    if not isinstance(feature_id, str) or not isinstance(value, dict):
                        problems.append("features must map string ids to objects.")
                        break
                    enabled = value.get("enabled")
                    if enabled is not None and not isinstance(enabled, bool):
                        problems.append(
                            f"features.{feature_id}.enabled is not a boolean.")
                    settings = value.get("settings")
                    if settings is not None and not isinstance(settings, dict):
                        problems.append(
                            f"features.{feature_id}.settings is not a JSON object.")
            shortcuts = root.get("shortcuts", {})
            if isinstance(shortcuts, dict):
                for command_id, sequences in shortcuts.items():
                    if (not isinstance(command_id, str)
                            or not isinstance(sequences, list)
                            or not all(isinstance(item, str) for item in sequences)):
                        problems.append(
                            "shortcuts must map string command ids to string lists.")
                        break
                    for sequence in sequences:
                        try:
                            KeySequence.parse(sequence)
                        except ValueError as exc:
                            problems.append(
                                f"shortcuts.{command_id}: {exc}")
        return problems

    def export(self, path: os.PathLike[str] | str) -> list[str]:
        target, problems = self._validate_transfer_path(path)
        if problems:
            return problems
        assert target is not None
        if target.exists() and target.is_dir():
            return [f"Export failed: {target} is a directory."]
        if not target.parent.exists():
            return [f"Export failed: directory does not exist: {target.parent}"]
        try:
            self.store.export(target)
        except (OSError, TypeError, ValueError) as exc:
            return [f"Export failed: {exc}"]
        return []

    def import_(self, path: os.PathLike[str] | str) -> list[str]:
        target, problems = self._validate_transfer_path(path)
        if problems:
            return problems
        assert target is not None
        problems = self._validate_import_document(target)
        if problems:
            return problems
        try:
            self.store.import_(target)
        except (OSError, TypeError, ValueError) as exc:
            return [str(exc)]
        return []

    def effective_shortcuts(self) -> dict[str, tuple[str, ...]]:
        effective: dict[str, tuple[str, ...]] = {}
        for feature in self.registry.features:
            for command_id, defaults in feature.default_shortcuts.items():
                override = self.store.get_shortcut_overrides(command_id)
                effective[command_id] = tuple(defaults if override is None else override)
        return effective

    def shortcut_collisions(self) -> tuple[ShortcutCollision, ...]:
        bindings: list[tuple[str, KeySequence]] = []
        for command_id, raw_sequences in self.effective_shortcuts().items():
            seen: set[KeySequence] = set()
            for raw_sequence in raw_sequences:
                sequence = KeySequence.parse(raw_sequence)
                if sequence not in seen:
                    seen.add(sequence)
                    bindings.append((command_id, sequence))

        collisions: list[ShortcutCollision] = []
        for index, (first_command, first) in enumerate(bindings):
            for second_command, second in bindings[index + 1:]:
                if first_command == second_command:
                    continue
                if first == second:
                    kind = "identical"
                elif first.is_prefix_of(second) or second.is_prefix_of(first):
                    kind = "prefix"
                else:
                    continue
                collisions.append(ShortcutCollision(
                    kind, first_command, str(first), second_command, str(second)))
        return tuple(collisions)

    detect_shortcut_collisions = shortcut_collisions

    def hook_disclosure(self) -> tuple[HookDisclosure, ...]:
        return tuple(
            HookDisclosure(feature.id, feature.title, tuple(feature.input_hooks))
            for feature in self.registry.features if feature.input_hooks)


def _probe() -> None:
    registry = FeatureRegistry([
        FeatureDeclaration(
            "capture", "Screen Capture", "Features", True,
            {"capture.region": ("Win+Shift+S",)},
            {"include_pointer": True}, ("Low-level keyboard hook",)),
        FeatureDeclaration(
            "snip", "Quick Snip", "Shortcuts", True,
            {"snip.open": ("win+shift+s",)}),
        FeatureDeclaration(
            "window-layout", "Window Layout", "Features", False,
            {"layout.left": ("Ctrl+Alt+L",),
             "layout.corner": ("Ctrl+Alt+L, then Ctrl+Alt+U",)}),
        FeatureDeclaration(
            "diagnostics", "Diagnostics", "Status", True),
    ])

    with tempfile.TemporaryDirectory() as directory:
        store = ConfigStore(directory)
        service = SettingsService(store, registry)

        print("sections:")
        for section, features in service.section_tree().items():
            rendered = ", ".join(
                f"{item.id}={'on' if item.enabled else 'off'}" for item in features)
            print(f"  {section}: {rendered or '(empty)'}")

        print("collisions:")
        for collision in service.shortcut_collisions():
            print(f"  {collision.kind}: {collision.message}")

        service.set_feature_enabled("capture", False)
        print(f"flipped: capture={service.is_enabled('capture')}")
        service.reset_feature("capture")
        print(f"reset: capture={service.is_enabled('capture')}")

        exported = Path(directory) / "round-trip.json"
        service.set_feature_enabled("capture", False)
        export_problems = service.export(exported)
        service.set_feature_enabled("capture", True)
        import_problems = service.import_(exported)
        print(
            "round-trip: "
            f"export_problems={export_problems} "
            f"import_problems={import_problems} "
            f"capture={service.is_enabled('capture')}")
        print("probe: PASS")


if __name__ == "__main__":
    _probe()
