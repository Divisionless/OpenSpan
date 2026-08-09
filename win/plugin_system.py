"""Standalone plugin discovery, validation, and guarded activation.

Python plugin isolation is weak: importing a plugin executes arbitrary code with the
host process's full privileges, modules can retain references and start work the host
cannot reclaim, and removing a module from ``sys.modules`` does not guarantee unloading.
This module is not a sandbox or a security boundary; genuinely untrusted plugins require
out-of-process hosting with an operating-system-enforced boundary.

Discovery is deliberately separate from execution.  :class:`PluginCatalog` reads only
``plugin.json`` files and filesystem metadata.  Plugin source is imported solely by an
explicit :func:`activate` call, after manifest validation, API negotiation, enablement,
and capability resolution have all succeeded.
"""

from __future__ import annotations

import dataclasses
import enum
import importlib.util
import inspect
import itertools
import json
import pathlib
import sys
import types
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Protocol


# ---- version and capabilities -------------------------------------------------


CURRENT_API_VERSION = 1
MINIMUM_SUPPORTED_API_VERSION = 1

COMMANDS = "commands"
EVENTS = "events"
SETTINGS = "settings"
ALL_CAPABILITIES = (COMMANDS, EVENTS, SETTINGS)


def is_known_capability(capability: str) -> bool:
    """Return whether *capability* is a case-sensitive host capability name."""
    return capability in ALL_CAPABILITIES


def capability_intersection(
        requested: Iterable[str], host_granted: Iterable[str]) -> tuple[str, ...]:
    """Capabilities both declared by the plugin and granted by the host.

    Manifest order is preserved, which keeps diagnostics and list output stable.
    Unknown host grant names confer nothing.
    """
    granted = frozenset(host_granted)
    return tuple(item for item in requested
                 if item in granted and is_known_capability(item))


def is_api_compatible(
        plugin_api_version: int,
        host_current: int = CURRENT_API_VERSION,
        host_minimum_supported: int = MINIMUM_SUPPORTED_API_VERSION) -> bool:
    """Apply the inclusive API window; an inverted window accepts nothing."""
    return (host_minimum_supported <= host_current
            and host_minimum_supported <= plugin_api_version <= host_current)


def describe_api_incompatibility(
        plugin_api_version: int,
        host_current: int = CURRENT_API_VERSION,
        host_minimum_supported: int = MINIMUM_SUPPORTED_API_VERSION) -> str | None:
    """Return an actionable incompatibility sentence, or ``None`` when compatible."""
    if is_api_compatible(plugin_api_version, host_current,
                         host_minimum_supported):
        return None
    if plugin_api_version > host_current:
        return (f"Built for plugin API {plugin_api_version}, but this build of "
                f"EsotericOS speaks up to {host_current}. Update EsotericOS, or "
                "install a build of the plugin made for this version.")
    return (f"Built for plugin API {plugin_api_version}, which this build of "
            "EsotericOS no longer supports (oldest accepted is "
            f"{host_minimum_supported}). Ask the plugin author for an updated build.")


class PluginApiVersion:
    """Class-shaped access to the version contract for callers that prefer it."""

    CURRENT = CURRENT_API_VERSION
    MINIMUM_SUPPORTED = MINIMUM_SUPPORTED_API_VERSION
    is_compatible = staticmethod(is_api_compatible)
    describe_incompatibility = staticmethod(describe_api_incompatibility)


class PluginCapabilities:
    """Class-shaped access to the capability vocabulary."""

    COMMANDS = COMMANDS
    EVENTS = EVENTS
    SETTINGS = SETTINGS
    ALL = ALL_CAPABILITIES
    is_known = staticmethod(is_known_capability)
    intersect = staticmethod(capability_intersection)


# ---- manifest model and validation -------------------------------------------


MINIMUM_ID_LENGTH = 3
MAXIMUM_ID_LENGTH = 64
MAXIMUM_DISPLAY_NAME_LENGTH = 80
MAXIMUM_ACTION_LENGTH = 48
MAXIMUM_SETTING_KEY_LENGTH = 64

COMMAND_PREFIX = "esotericos.plugin."
SETTINGS_PREFIX = "plugins."


@dataclasses.dataclass(frozen=True, slots=True)
class PluginManifestError:
    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


@dataclasses.dataclass(frozen=True, slots=True)
class PluginManifest:
    """The versioned contents of one ``plugin.json``.

    ``entry_point`` is a bare Python filename, optionally followed by a public
    class name (for example ``plugin.py:Plugin``).  With no class suffix the
    module must contain exactly one public class implementing the lifecycle.
    """

    id: str
    display_name: str
    version: str
    api_version: int
    entry_point: str
    requested_capabilities: tuple[str, ...] = ()
    enabled_by_default: bool = True

    @property
    def entry_module(self) -> str:
        return self.entry_point.partition(":")[0]

    @classmethod
    def parse(cls, text: str | None) -> PluginManifestParseResult:
        return parse_manifest(text)


@dataclasses.dataclass(frozen=True, slots=True)
class PluginManifestParseResult:
    manifest: PluginManifest | None
    errors: tuple[PluginManifestError, ...]

    @property
    def is_valid(self) -> bool:
        return self.manifest is not None and not self.errors


def _read_string(
        root: Mapping[str, Any], field: str,
        errors: list[PluginManifestError], *, aliases: Sequence[str] = ()) -> str:
    found = next((name for name in (field, *aliases) if name in root), None)
    if found is None or root[found] is None:
        return ""
    value = root[found]
    if isinstance(value, str):
        return value
    errors.append(PluginManifestError(field, "Must be a quoted string."))
    return ""


def _read_int(root: Mapping[str, Any], field: str,
              errors: list[PluginManifestError]) -> int:
    if field not in root or root[field] is None:
        return 0
    value = root[field]
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    errors.append(PluginManifestError(
        field, "Must be a whole number, written without quotes."))
    return 0


def _read_bool(root: Mapping[str, Any], field: str,
               errors: list[PluginManifestError], default: bool) -> bool:
    if field not in root or root[field] is None:
        return default
    value = root[field]
    if isinstance(value, bool):
        return value
    errors.append(PluginManifestError(field, "Must be true or false."))
    return default


def _read_capabilities(root: Mapping[str, Any],
                       errors: list[PluginManifestError]) -> tuple[str, ...]:
    field = "requestedCapabilities"
    if field not in root or root[field] is None:
        return ()
    value = root[field]
    if not isinstance(value, list):
        errors.append(PluginManifestError(
            field, f'Must be an array of strings, e.g. ["{COMMANDS}"].'))
        return ()
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            result.append(item)
            continue
        errors.append(PluginManifestError(
            field, "Every entry must be a quoted string. Known capabilities: "
            + ", ".join(ALL_CAPABILITIES) + "."))
        break
    return tuple(result)


def parse_manifest(text: str | None) -> PluginManifestParseResult:
    """Parse and validate a manifest without throwing or stopping at one error."""
    if text is None or not text.strip():
        return PluginManifestParseResult(None, (PluginManifestError(
            "plugin.json", "The file is empty. It must contain a JSON object with "
            "id, displayName, version, apiVersion and entryPoint."),))
    try:
        root = json.loads(text)
    except (json.JSONDecodeError, UnicodeError) as exc:
        if isinstance(exc, json.JSONDecodeError):
            detail = f"line {exc.lineno}, position {exc.colno}: {exc.msg}"
        else:
            detail = str(exc)
        return PluginManifestParseResult(None, (PluginManifestError(
            "plugin.json", f"Invalid JSON at {detail}"),))
    if not isinstance(root, dict):
        return PluginManifestParseResult(None, (PluginManifestError(
            "plugin.json", 'The root of the file must be a JSON object, e.g. { "id": ... }.'),))

    shape_errors: list[PluginManifestError] = []
    manifest = PluginManifest(
        id=_read_string(root, "id", shape_errors),
        display_name=_read_string(root, "displayName", shape_errors),
        version=_read_string(root, "version", shape_errors),
        api_version=_read_int(root, "apiVersion", shape_errors),
        # Older draft names are read for migration; entryPoint is the public name.
        entry_point=_read_string(
            root, "entryPoint", shape_errors,
            aliases=("entryModule", "entryAssembly")),
        requested_capabilities=_read_capabilities(root, shape_errors),
        enabled_by_default=_read_bool(
            root, "enabledByDefault", shape_errors, True),
    )
    shaped_fields = {error.field for error in shape_errors}
    semantic = tuple(error for error in validate_manifest(manifest)
                     if error.field not in shaped_fields)
    return PluginManifestParseResult(
        manifest, tuple(shape_errors) + semantic)


def is_valid_plugin_id(plugin_id: str) -> bool:
    if not MINIMUM_ID_LENGTH <= len(plugin_id) <= MAXIMUM_ID_LENGTH:
        return False
    if not "a" <= plugin_id[0] <= "z" or plugin_id.endswith("-"):
        return False
    previous = ""
    for char in plugin_id[1:]:
        if not ("a" <= char <= "z" or "0" <= char <= "9" or char == "-"):
            return False
        if char == previous == "-":
            return False
        previous = char
    return True


def is_valid_version(version: str) -> bool:
    core, separator, suffix = version.partition("-")
    if separator and not suffix:
        return False
    if suffix and any(not (char.isascii() and char.isalnum())
                      and char not in ".-" for char in suffix):
        return False
    parts = core.split(".")
    return (len(parts) == 3
            and all(part and len(part) <= 9
                    and part.isascii() and part.isdigit() for part in parts))


def _split_entry_point(entry_point: str) -> tuple[str, str | None]:
    filename, separator, class_name = entry_point.partition(":")
    return filename, class_name if separator else None


def is_valid_entry_point(entry_point: str) -> bool:
    filename, class_name = _split_entry_point(entry_point)
    if not filename.lower().endswith(".py") or len(filename) <= 3:
        return False
    if ".." in filename or any(char in filename for char in '<>:"/\\|?*\0'):
        return False
    if pathlib.PurePath(filename).name != filename:
        return False
    if class_name is not None:
        if (not class_name or not class_name.isidentifier()
                or class_name.startswith("_") or ":" in class_name):
            return False
    return True


def _describe_id_problem(plugin_id: str) -> str:
    if len(plugin_id) < MINIMUM_ID_LENGTH:
        return (f"'{plugin_id}' is too short - ids are at least "
                f"{MINIMUM_ID_LENGTH} characters.")
    if len(plugin_id) > MAXIMUM_ID_LENGTH:
        return (f"'{plugin_id}' is too long - ids are at most "
                f"{MAXIMUM_ID_LENGTH} characters.")
    return (f"'{plugin_id}' is not a valid id. Use lowercase letters, digits "
            'and single hyphens, starting with a letter - e.g. "hello-world".')


def validate_manifest(manifest: PluginManifest) -> tuple[PluginManifestError, ...]:
    """Return every manifest problem in file order."""
    errors: list[PluginManifestError] = []
    if not manifest.id:
        errors.append(PluginManifestError(
            "id", 'Missing. Add a stable id matching the plugin folder, e.g. "hello-world".'))
    elif not is_valid_plugin_id(manifest.id):
        errors.append(PluginManifestError("id", _describe_id_problem(manifest.id)))

    if not manifest.display_name:
        errors.append(PluginManifestError(
            "displayName", 'Missing. Add the name to show in Settings, e.g. "Hello World".'))
    elif len(manifest.display_name) > MAXIMUM_DISPLAY_NAME_LENGTH:
        errors.append(PluginManifestError(
            "displayName", f"Too long ({len(manifest.display_name)} characters). Keep it to "
            f"{MAXIMUM_DISPLAY_NAME_LENGTH} or fewer so it fits the Settings list."))
    elif any(unicodedata.category(char) == "Cc" for char in manifest.display_name):
        errors.append(PluginManifestError(
            "displayName", "Contains control characters. Use plain text on one line."))

    if not manifest.version:
        errors.append(PluginManifestError(
            "version", 'Missing. Add the plugin version, e.g. "1.0.0".'))
    elif not is_valid_version(manifest.version):
        errors.append(PluginManifestError(
            "version", f"'{manifest.version}' is not a version. Use three numbers separated "
            'by dots, with an optional suffix - "1.0.0" or "1.2.0-beta.3".'))

    if manifest.api_version <= 0:
        errors.append(PluginManifestError(
            "apiVersion", "Missing or not a positive number. Set it to the plugin API "
            f"generation the plugin was built against - this host speaks {CURRENT_API_VERSION}."))

    if not manifest.entry_point:
        errors.append(PluginManifestError(
            "entryPoint", 'Missing. Name the plugin Python file, e.g. "plugin.py:Plugin".'))
    elif not is_valid_entry_point(manifest.entry_point):
        errors.append(PluginManifestError(
            "entryPoint", f"'{manifest.entry_point}' must be a bare .py filename, optionally "
            "followed by a public class after ':', and must sit beside plugin.json - no "
            "folders, '..', or drive letters."))

    seen: set[str] = set()
    for capability in manifest.requested_capabilities:
        if not is_known_capability(capability):
            errors.append(PluginManifestError(
                "requestedCapabilities", f"'{capability}' is not a capability this host "
                "grants. Known capabilities: " + ", ".join(ALL_CAPABILITIES) + "."))
        elif capability in seen:
            errors.append(PluginManifestError(
                "requestedCapabilities", f"'{capability}' is listed more than once."))
        else:
            seen.add(capability)
    return tuple(errors)


def validate_against_folder(
        manifest: PluginManifest, folder_name: str) -> tuple[PluginManifestError, ...]:
    if manifest.id == folder_name:
        return ()
    return (PluginManifestError(
        "id", f"Declared id '{manifest.id}' does not match the folder name "
        f"'{folder_name}'. Rename the folder or the id so they agree."),)


class PluginManifestValidator:
    MINIMUM_ID_LENGTH = MINIMUM_ID_LENGTH
    MAXIMUM_ID_LENGTH = MAXIMUM_ID_LENGTH
    MAXIMUM_DISPLAY_NAME_LENGTH = MAXIMUM_DISPLAY_NAME_LENGTH
    validate = staticmethod(validate_manifest)
    validate_against_folder = staticmethod(validate_against_folder)
    is_valid_id = staticmethod(is_valid_plugin_id)
    is_valid_version = staticmethod(is_valid_version)
    is_valid_entry_point = staticmethod(is_valid_entry_point)


# ---- safe shared names --------------------------------------------------------


def is_valid_command_action(action: str) -> bool:
    if not action or len(action) > MAXIMUM_ACTION_LENGTH:
        return False
    if not "a" <= action[0] <= "z" or action.endswith("-"):
        return False
    previous = ""
    for char in action[1:]:
        if not ("a" <= char <= "z" or "0" <= char <= "9" or char == "-"):
            return False
        if char == previous == "-":
            return False
        previous = char
    return True


def is_valid_setting_key(key: str) -> bool:
    return (0 < len(key) <= MAXIMUM_SETTING_KEY_LENGTH
            and key[0].isascii() and key[0].isalpha()
            and all(char.isascii() and (char.isalnum() or char in "-_")
                    for char in key))


def compose_command_id(plugin_id: str, action: str) -> str:
    if not is_valid_plugin_id(plugin_id):
        raise ValueError(f"'{plugin_id}' is not a valid plugin id.")
    if not is_valid_command_action(action):
        raise ValueError(
            f"'{action}' is not a usable command name. Use lowercase letters, digits "
            f"and single hyphens, starting with a letter and at most {MAXIMUM_ACTION_LENGTH} "
            'characters - e.g. "run" or "open-notes".')
    return f"{COMMAND_PREFIX}{plugin_id}.{action}"


def compose_setting_key(plugin_id: str, key: str) -> str:
    if not is_valid_plugin_id(plugin_id):
        raise ValueError(f"'{plugin_id}' is not a valid plugin id.")
    if not is_valid_setting_key(key):
        raise ValueError(
            f"'{key}' is not a usable setting name. Use letters, digits, hyphen and "
            f"underscore, starting with a letter and at most {MAXIMUM_SETTING_KEY_LENGTH} "
            "characters. Dots are not allowed.")
    return f"{SETTINGS_PREFIX}{plugin_id}.{key}"


class PluginNaming:
    COMMAND_PREFIX = COMMAND_PREFIX
    SETTINGS_PREFIX = SETTINGS_PREFIX
    MAXIMUM_ACTION_LENGTH = MAXIMUM_ACTION_LENGTH
    MAXIMUM_SETTING_KEY_LENGTH = MAXIMUM_SETTING_KEY_LENGTH
    is_valid_command_action = staticmethod(is_valid_command_action)
    is_valid_setting_key = staticmethod(is_valid_setting_key)
    compose_command_id = staticmethod(compose_command_id)
    compose_setting_key = staticmethod(compose_setting_key)


# ---- enablement and catalog ---------------------------------------------------


class PluginEnablementVerdict(enum.Enum):
    ENABLED_BY_DEFAULT = "enabled by manifest default"
    DISABLED_BY_DEFAULT = "disabled by manifest default"
    ENABLED_BY_OVERRIDE = "enabled by host override"
    DISABLED_BY_OVERRIDE = "disabled by host override"

    @property
    def enabled(self) -> bool:
        return self in (self.ENABLED_BY_DEFAULT, self.ENABLED_BY_OVERRIDE)


def resolve_enablement(
        plugin_id: str, enabled_by_default: bool,
        is_enabled: Callable[[str], bool | None] | None = None,
        ) -> PluginEnablementVerdict:
    """Resolve the injected override; ``None`` means use the manifest default."""
    override = None if is_enabled is None else is_enabled(plugin_id)
    if override is not None and not isinstance(override, bool):
        raise TypeError("is_enabled must return bool or None")
    if override is True:
        return PluginEnablementVerdict.ENABLED_BY_OVERRIDE
    if override is False:
        return PluginEnablementVerdict.DISABLED_BY_OVERRIDE
    return (PluginEnablementVerdict.ENABLED_BY_DEFAULT if enabled_by_default
            else PluginEnablementVerdict.DISABLED_BY_DEFAULT)


def describe_exclusion(verdict: PluginEnablementVerdict) -> str | None:
    if verdict is PluginEnablementVerdict.DISABLED_BY_OVERRIDE:
        return "Switched off by the host's enablement override."
    if verdict is PluginEnablementVerdict.DISABLED_BY_DEFAULT:
        return "Switched off by the manifest default."
    return None


class PluginEnablement:
    resolve = staticmethod(resolve_enablement)
    describe_exclusion = staticmethod(describe_exclusion)


class LoadDecision(enum.Enum):
    LOAD = "load"
    REFUSE = "refuse"
    SKIP = "skip"


class PluginState(enum.Enum):
    READY = "ready"
    REFUSED = "refused"
    SKIPPED = "skipped"
    ACTIVE = "active"
    FAULTED = "faulted"
    INACTIVE = "inactive"


@dataclasses.dataclass(slots=True)
class PluginRecord:
    """Discovery decision and contained runtime state for one plugin folder."""

    reporting_id: str
    directory: pathlib.Path
    manifest: PluginManifest | None
    problems: tuple[str, ...]
    decision: LoadDecision
    state: PluginState
    reason: str | None
    granted_capabilities: tuple[str, ...] = ()
    enablement: PluginEnablementVerdict | None = None
    _module_name: str | None = dataclasses.field(default=None, repr=False)
    _module: types.ModuleType | None = dataclasses.field(default=None, repr=False)
    _instance: Any = dataclasses.field(default=None, repr=False)
    _bridge: PluginHostBridge | None = dataclasses.field(default=None, repr=False)

    @property
    def id(self) -> str:
        return self.manifest.id if self.manifest and self.manifest.id else self.reporting_id

    @property
    def display_name(self) -> str:
        return (self.manifest.display_name
                if self.manifest and self.manifest.display_name else self.id)

    @property
    def is_loadable(self) -> bool:
        return self.decision is LoadDecision.LOAD

    @property
    def load_decision(self) -> LoadDecision:
        return self.decision

    def describe(self) -> str:
        version = f" {self.manifest.version}" if self.manifest else ""
        capabilities = (f" [{', '.join(self.granted_capabilities)}]"
                        if self.granted_capabilities else "")
        reason = f" - {self.reason}" if self.reason else ""
        return f"{self.id}{version}{capabilities}: {self.state.name}{reason}"


class PluginCatalog:
    """Read-only discovery over a plugin directory tree."""

    MANIFEST_FILE_NAME = "plugin.json"
    MAXIMUM_MANIFEST_BYTES = 64 * 1024

    def __init__(
            self, root: str | pathlib.Path,
            is_enabled: Callable[[str], bool | None] | None = None,
            host_capabilities: (Iterable[str]
                                | Callable[[str], Iterable[str] | None]) = ALL_CAPABILITIES):
        self.root = pathlib.Path(root)
        self.is_enabled = is_enabled
        self.host_capabilities = host_capabilities

    @staticmethod
    def resolve_root(config_directory: str | pathlib.Path) -> pathlib.Path:
        return pathlib.Path(config_directory) / "plugins"

    @staticmethod
    def count_candidate_folders(root: str | pathlib.Path) -> int:
        try:
            return sum(1 for item in pathlib.Path(root).iterdir() if item.is_dir())
        except OSError:
            return 0

    def discover(self) -> list[PluginRecord]:
        try:
            directories = sorted(
                (item for item in self.root.iterdir() if item.is_dir()),
                key=lambda item: (item.name.casefold(), item.name))
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
            return []
        return [self._read(directory) for directory in directories]

    def _refused(
            self, directory: pathlib.Path, manifest: PluginManifest | None,
            problems: Iterable[str]) -> PluginRecord:
        problem_tuple = tuple(problems)
        reporting_id = manifest.id if manifest and manifest.id else directory.name
        reason = " ".join(problem_tuple) or "Its plugin.json could not be used."
        return PluginRecord(reporting_id, directory, manifest, problem_tuple,
                            LoadDecision.REFUSE, PluginState.REFUSED, reason)

    def _read(self, directory: pathlib.Path) -> PluginRecord:
        manifest_path = directory / self.MANIFEST_FILE_NAME
        try:
            if not manifest_path.is_file():
                return self._refused(directory, None, (
                    "No plugin.json in this folder. Add one, or remove the folder.",))
            size = manifest_path.stat().st_size
            if size > self.MAXIMUM_MANIFEST_BYTES:
                return self._refused(directory, None, (
                    f"plugin.json is {size:,} bytes, which is far larger than a manifest "
                    "should be. It was not read.",))
            text = manifest_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            return self._refused(directory, None, (
                f"plugin.json could not be read: {exc}",))

        result = parse_manifest(text)
        problems = [str(error) for error in result.errors]
        if result.manifest is not None:
            for error in validate_against_folder(result.manifest, directory.name):
                rendered = str(error)
                if rendered not in problems:
                    problems.append(rendered)
        if result.manifest is None or problems:
            return self._refused(directory, result.manifest, problems)

        manifest = result.manifest

        # The API gate deliberately precedes enablement, grants, entry lookup, and code.
        mismatch = describe_api_incompatibility(manifest.api_version)
        if mismatch is not None:
            return self._refused(directory, manifest, (mismatch,))

        try:
            verdict = resolve_enablement(
                manifest.id, manifest.enabled_by_default, self.is_enabled)
        except BaseException as exc:
            return self._refused(directory, manifest, (
                f"Enablement lookup failed with {type(exc).__name__}: {exc}",))
        exclusion = describe_exclusion(verdict)
        if exclusion is not None:
            return PluginRecord(
                manifest.id, directory, manifest, (), LoadDecision.SKIP,
                PluginState.SKIPPED, exclusion, enablement=verdict)

        try:
            grants_source = self.host_capabilities
            granted_by_host = (grants_source(manifest.id)
                               if callable(grants_source) else grants_source)
            granted = capability_intersection(
                manifest.requested_capabilities, granted_by_host or ())
        except BaseException as exc:
            return self._refused(directory, manifest, (
                f"Capability resolution failed with {type(exc).__name__}: {exc}",))

        entry_file = directory / manifest.entry_module
        try:
            exists = entry_file.is_file()
        except OSError:
            exists = False
        if not exists:
            return self._refused(directory, manifest, (
                f"'{manifest.entry_module}' is not in the plugin folder. Copy the plugin "
                "source next to plugin.json.",))

        return PluginRecord(
            manifest.id, directory, manifest, (), LoadDecision.LOAD,
            PluginState.READY, None, granted, verdict)


def discover(
        root: str | pathlib.Path,
        is_enabled: Callable[[str], bool | None] | None = None,
        host_capabilities: (Iterable[str]
                            | Callable[[str], Iterable[str] | None]) = ALL_CAPABILITIES,
        ) -> list[PluginRecord]:
    return PluginCatalog(root, is_enabled, host_capabilities).discover()


# ---- runtime ------------------------------------------------------------------


class PluginCapabilityDeniedError(RuntimeError):
    def __init__(self, plugin_id: str, capability: str):
        self.plugin_id = plugin_id
        self.capability = capability
        super().__init__(
            f"Plugin '{plugin_id}' called an API that requires the '{capability}' "
            f"capability. Add \"{capability}\" to requestedCapabilities in its plugin.json.")


# C# contract name retained as a friendly alias.
PluginCapabilityDeniedException = PluginCapabilityDeniedError


class PluginLogLevel(enum.Enum):
    DEBUG = "debug"
    INFORMATION = "information"
    WARNING = "warning"
    ERROR = "error"


class Plugin(Protocol):
    id: str
    display_name: str
    api_version: int

    def activate(self, host: PluginHostBridge) -> None: ...
    def deactivate(self) -> None: ...


def _dispose_registration(registration: Any) -> None:
    if registration is None:
        return
    for name in ("dispose", "close", "unregister"):
        method = getattr(registration, name, None)
        if callable(method):
            method()
            return
    if callable(registration):
        registration()


class PluginHostBridge:
    """Capability-gated, plugin-scoped view of a host object."""

    MAXIMUM_LOGGED_MESSAGE_LENGTH = 1000
    MAXIMUM_COMMAND_TITLE_LENGTH = 120

    def __init__(
            self, plugin_id: str, granted_capabilities: Iterable[str], host: Any,
            on_fault: Callable[[str, BaseException], None]):
        self.plugin_id = plugin_id
        self.host_api_version = CURRENT_API_VERSION
        self.granted_capabilities = tuple(granted_capabilities)
        self._capabilities = frozenset(self.granted_capabilities)
        self._host = host
        self._on_fault = on_fault
        self._registrations: list[Any] = []
        self._active = True

    def has_capability(self, capability: str) -> bool:
        return capability in self._capabilities

    def _require_active(self) -> None:
        if not self._active:
            raise RuntimeError(
                f"Plugin '{self.plugin_id}' has been unloaded; its host object can no longer be used.")

    def _require_capability(self, capability: str) -> None:
        if capability not in self._capabilities:
            raise PluginCapabilityDeniedError(self.plugin_id, capability)

    def _guard(self, handler: Callable[..., Any], what: str) -> Callable[..., Any]:
        def guarded(*args: Any, **kwargs: Any) -> Any:
            if not self._active:
                return None
            try:
                result = handler(*args, **kwargs)
                if inspect.isawaitable(result):
                    raise TypeError("async plugin callbacks are unsupported by this synchronous API")
                return result
            except BaseException as exc:
                self._on_fault(
                    f"its {what} raised {type(exc).__name__}: {exc}", exc)
                return None
        return guarded

    def _track(self, registration: Any) -> Any:
        if not self._active:
            try:
                _dispose_registration(registration)
            finally:
                self._require_active()
        self._registrations.append(registration)
        return registration

    def register_command(
            self, action: str, title: str, handler: Callable[[], Any]) -> Any:
        self._require_active()
        self._require_capability(COMMANDS)
        if not isinstance(title, str) or not title.strip():
            raise ValueError("A command needs a title - it is what the user reads in Settings.")
        if not callable(handler):
            raise TypeError("handler must be callable")
        command_id = compose_command_id(self.plugin_id, action)
        clean_title = " ".join(title.splitlines())[:self.MAXIMUM_COMMAND_TITLE_LENGTH]
        registration = self._host.register_command(
            command_id, clean_title,
            self._guard(handler, f"command '{command_id}'"))
        return self._track(registration)

    def subscribe(
            self, event_type: Any, handler: Callable[[Any], Any]) -> Any:
        self._require_active()
        self._require_capability(EVENTS)
        if not callable(handler):
            raise TypeError("handler must be callable")
        name = getattr(event_type, "__name__", str(event_type))
        registration = self._host.subscribe(
            event_type, self._guard(handler, f"handler for {name}"))
        return self._track(registration)

    def get_setting(self, key: str, fallback: Any = None) -> Any:
        self._require_active()
        self._require_capability(SETTINGS)
        return self._host.get_setting(
            compose_setting_key(self.plugin_id, key), fallback)

    def set_setting(self, key: str, value: Any) -> None:
        self._require_active()
        self._require_capability(SETTINGS)
        self._host.set_setting(compose_setting_key(self.plugin_id, key), value)

    def log(self, level: str | PluginLogLevel, message: str) -> None:
        if not message:
            return
        clean = " ".join(str(message).splitlines())
        if len(clean) > self.MAXIMUM_LOGGED_MESSAGE_LENGTH:
            clean = clean[:self.MAXIMUM_LOGGED_MESSAGE_LENGTH] + "..."
        rendered_level = level.value if isinstance(level, PluginLogLevel) else level
        self._host.log(rendered_level, self.plugin_id, clean)

    def deactivate(self) -> None:
        """Release registrations best-effort and reject all further gated calls."""
        if not self._active and not self._registrations:
            return
        self._active = False
        registrations, self._registrations = self._registrations, []
        for registration in registrations:
            try:
                _dispose_registration(registration)
            except BaseException:
                pass


_MODULE_SERIAL = itertools.count(1)


def _fault_text(phase: str, exc: BaseException) -> str:
    detail = str(exc)
    suffix = f": {detail}" if detail else ""
    return f"{phase} raised {type(exc).__name__}{suffix}"


def _clear_runtime(record: PluginRecord) -> None:
    bridge, record._bridge = record._bridge, None
    if bridge is not None:
        bridge.deactivate()
    record._instance = None
    record._module = None
    if record._module_name is not None:
        sys.modules.pop(record._module_name, None)
        record._module_name = None


def _fault_from_callback(
        record: PluginRecord, reason: str, _exception: BaseException) -> None:
    if record.state is PluginState.FAULTED:
        return
    record.state = PluginState.FAULTED
    record.reason = reason + ". It is disabled until plugins are reloaded."
    bridge = record._bridge
    if bridge is not None:
        bridge.deactivate()
    instance = record._instance
    if instance is not None:
        try:
            instance.deactivate()
        except BaseException as exc:
            record.reason += " " + _fault_text("deactivate", exc) + "."
    _clear_runtime(record)


def _find_plugin_class(module: types.ModuleType,
                       class_name: str | None) -> type[Any]:
    if class_name is not None:
        candidate = getattr(module, class_name, None)
        if not isinstance(candidate, type):
            raise TypeError(f"entry point class '{class_name}' was not found")
        candidates = [candidate]
    else:
        candidates = [candidate for name, candidate in vars(module).items()
                      if (not name.startswith("_") and isinstance(candidate, type)
                          and candidate.__module__ == module.__name__
                          and callable(getattr(candidate, "activate", None))
                          and callable(getattr(candidate, "deactivate", None)))]
    if len(candidates) != 1:
        raise TypeError(
            f"module contains {len(candidates)} public plugin classes; exactly one is required")
    candidate = candidates[0]
    if not callable(getattr(candidate, "activate", None)) \
            or not callable(getattr(candidate, "deactivate", None)):
        raise TypeError(
            f"'{candidate.__name__}' must define activate(host) and deactivate()")
    return candidate


def activate(record: PluginRecord, host: Any) -> bool:
    """Explicitly import and activate one ready record behind a fault barrier."""
    if record.decision is not LoadDecision.LOAD or record.manifest is None:
        return False
    if record.state is PluginState.ACTIVE:
        return True
    if record.state not in (PluginState.READY, PluginState.INACTIVE):
        return False

    manifest = record.manifest
    filename, class_name = _split_entry_point(manifest.entry_point)
    path = record.directory / filename
    module_name = (f"_esotericos_plugin_{manifest.id.replace('-', '_')}_"
                   f"{next(_MODULE_SERIAL)}")
    record._module_name = module_name

    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"could not create an import spec for '{filename}'")
        module = importlib.util.module_from_spec(spec)
        record._module = module
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        plugin_class = _find_plugin_class(module, class_name)
        instance = plugin_class()
        record._instance = instance

        # Code identity is checked independently of what plugin.json claimed.
        declared_id = instance.id
        declared_api = instance.api_version
        if declared_id != manifest.id:
            raise ValueError(
                f"plugin code reports id '{declared_id}' but plugin.json says '{manifest.id}'")
        if declared_api != manifest.api_version:
            raise ValueError(
                f"plugin code targets API {declared_api} but plugin.json declares "
                f"{manifest.api_version}")
        mismatch = describe_api_incompatibility(declared_api)
        if mismatch is not None:
            raise RuntimeError(mismatch)

        bridge = PluginHostBridge(
            manifest.id, record.granted_capabilities, host,
            lambda reason, exc: _fault_from_callback(record, reason, exc))
        record._bridge = bridge
        result = instance.activate(bridge)
        if inspect.isawaitable(result):
            raise TypeError("async plugin activation is unsupported by this synchronous API")
    except BaseException as exc:
        record.state = PluginState.FAULTED
        record.reason = _fault_text("activation", exc)
        _clear_runtime(record)
        return False

    record.state = PluginState.ACTIVE
    record.reason = None
    return True


def activate_all(records: Iterable[PluginRecord], host: Any) -> list[PluginRecord]:
    """Activate every loadable record; one fault cannot stop the iteration."""
    result = list(records)
    for record in result:
        activate(record, host)
    return result


def deactivate(record: PluginRecord) -> bool:
    """Best-effort lifecycle shutdown; faults are recorded and never escape."""
    instance = record._instance
    if instance is None:
        _clear_runtime(record)
        if record.state is PluginState.ACTIVE:
            record.state = PluginState.INACTIVE
        return True
    ok = True
    try:
        result = instance.deactivate()
        if inspect.isawaitable(result):
            raise TypeError("async plugin deactivation is unsupported by this synchronous API")
    except BaseException as exc:
        ok = False
        record.state = PluginState.FAULTED
        record.reason = _fault_text("deactivate", exc)
    finally:
        _clear_runtime(record)
    if ok:
        record.state = PluginState.INACTIVE
        record.reason = "Deactivated."
    return ok


# ---- self-probe ---------------------------------------------------------------


class _ProbeHost:
    def register_command(self, _id: str, _title: str, _handler: Callable[[], Any]) -> None:
        return None

    def subscribe(self, _event_type: Any, _handler: Callable[[Any], Any]) -> None:
        return None

    def get_setting(self, _key: str, fallback: Any = None) -> Any:
        return fallback

    def set_setting(self, _key: str, _value: Any) -> None:
        return None

    def log(self, _level: str, _plugin_id: str, _message: str) -> None:
        return None


def _write_probe_fixture(
        root: pathlib.Path, plugin_id: str, *, api_version: int = 1,
        source: str | None = None, malformed: bool = False) -> None:
    folder = root / plugin_id
    folder.mkdir()
    if malformed:
        (folder / "plugin.json").write_text("{bad json", encoding="utf-8")
        return
    manifest = {
        "id": plugin_id,
        "displayName": plugin_id.replace("-", " ").title(),
        "version": "1.0.0",
        "apiVersion": api_version,
        "entryPoint": "plugin.py:Plugin",
        "requestedCapabilities": [],
    }
    (folder / "plugin.json").write_text(
        json.dumps(manifest), encoding="utf-8")
    (folder / "plugin.py").write_text(source or f'''
class Plugin:
    id = "{plugin_id}"
    api_version = {api_version}
    def activate(self, host):
        self.host = host
    def deactivate(self):
        pass
'''.lstrip(), encoding="utf-8")


def _probe() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        _write_probe_fixture(root, "valid-plugin")
        _write_probe_fixture(root, "bad-manifest", malformed=True)
        _write_probe_fixture(root, "wrong-api", api_version=CURRENT_API_VERSION + 1)
        _write_probe_fixture(root, "raises-on-activate", source='''
class Plugin:
    id = "raises-on-activate"
    api_version = 1
    def activate(self, host):
        raise RuntimeError("probe boom")
    def deactivate(self):
        pass
'''.lstrip())
        records = discover(root)
        activate_all(records, _ProbeHost())
        for record in records:
            print(record.describe())
        for record in records:
            deactivate(record)
    return 0


if __name__ == "__main__":
    raise SystemExit(_probe())
