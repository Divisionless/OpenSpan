"""Contract, discovery, and runtime checks for the standalone plugin system."""

from __future__ import annotations

import ast
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import plugin_system as ps


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


def raises(exception_type, action, contains=None):
    try:
        action()
    except exception_type as exc:
        return contains is None or contains in str(exc)
    return False


GOOD_JSON = """
{
  "id": "hello-world",
  "displayName": "Hello World",
  "version": "1.0.0",
  "apiVersion": 1,
  "entryPoint": "plugin.py:Plugin",
  "requestedCapabilities": ["commands", "settings"]
}
"""


def valid_manifest(**changes):
    values = dict(
        id="hello-world",
        display_name="Hello World",
        version="1.0.0",
        api_version=1,
        entry_point="plugin.py:Plugin",
        requested_capabilities=(),
        enabled_by_default=True,
    )
    values.update(changes)
    return ps.PluginManifest(**values)


# ---- every PluginManifestTests case ------------------------------------------


parsed = ps.parse_manifest(GOOD_JSON)
manifest = parsed.manifest
check("well-formed manifest parses with every field",
      parsed.is_valid and manifest is not None
      and manifest.id == "hello-world"
      and manifest.display_name == "Hello World"
      and manifest.version == "1.0.0"
      and manifest.api_version == 1
      and manifest.entry_point == "plugin.py:Plugin"
      and manifest.requested_capabilities == ("commands", "settings"))

parsed = ps.parse_manifest(
    '{"id":"quiet-one","displayName":"Quiet","version":"0.1.0",'
    '"apiVersion":1,"entryPoint":"quiet.py"}')
check("requestedCapabilities is optional and grants nothing",
      parsed.is_valid and parsed.manifest is not None
      and parsed.manifest.requested_capabilities == ())

check("empty manifest text is reported rather than thrown",
      all(not result.is_valid and result.manifest is None
          and any("empty" in error.message for error in result.errors)
          for result in (ps.parse_manifest(""), ps.parse_manifest("   "))))

parsed = ps.parse_manifest('{"id": "hello-world",}')
check("malformed JSON is reported with a position",
      not parsed.is_valid and parsed.manifest is None
      and len(parsed.errors) == 1
      and parsed.errors[0].field == "plugin.json"
      and "Invalid JSON" in parsed.errors[0].message
      and "position" in parsed.errors[0].message)

parsed = ps.parse_manifest("[1, 2, 3]")
check("non-object manifest root is refused",
      not parsed.is_valid
      and any("must be a JSON object" in error.message
              for error in parsed.errors))

parsed = ps.parse_manifest(
    '{"id":"hello-world","displayName":"Hello","version":"1.0.0",'
    '"apiVersion":"1","entryPoint":"hello.py"}')
api_errors = [error for error in parsed.errors if error.field == "apiVersion"]
check("wrong field type is reported once rather than twice",
      len(api_errors) == 1 and "whole number" in api_errors[0].message)

parsed = ps.parse_manifest(
    '{"id":"hello-world","displayName":"Hello","version":"1.0.0",'
    '"apiVersion":1,"entryPoint":"hello.py",'
    '"requestedCapabilities":"commands"}')
check("non-list capability declaration is reported",
      any(error.field == "requestedCapabilities"
          and "array of strings" in error.message for error in parsed.errors))

parsed = ps.parse_manifest("{}")
check("every missing manifest field is reported in one pass",
      {"id", "displayName", "version", "apiVersion", "entryPoint"}
      <= {error.field for error in parsed.errors})

check("valid manifest has no validation errors",
      ps.validate_manifest(valid_manifest()) == ())

good_ids = ("hello-world", "md-preview2", "abc", "a1b")
check("well-formed plugin ids are accepted",
      all(ps.is_valid_plugin_id(item) for item in good_ids))

bad_ids = ("", "ab", "Hello-World", "1hello", "-hello", "hello-",
           "hello--world", "hello world", "hello_world", "hello.world",
           "../escape")
check("malformed plugin ids are refused",
      all(not ps.is_valid_plugin_id(item) for item in bad_ids))

errors = ps.validate_manifest(valid_manifest(id="a" * 65))
check("overlong plugin id names the length problem",
      len([error for error in errors if error.field == "id"]) == 1
      and "too long" in next(error.message for error in errors
                             if error.field == "id"))

good_versions = ("1.0.0", "0.0.1", "12.34.56", "1.2.0-beta.3",
                 "2.0.0-rc-1")
check("well-formed plugin versions are accepted",
      all(ps.is_valid_version(item) for item in good_versions))

bad_versions = ("1.0", "1", "1.0.0.0", "v1.0.0", "1.0.x", "1..0",
                "1.0.0-", "1.0.0 beta")
check("malformed plugin versions are refused",
      all(not ps.is_valid_version(item) for item in bad_versions))

good_entries = ("plugin.py", "a.py", "hello.world.py", "plugin.py:Plugin")
check("bare Python entry points are accepted",
      all(ps.is_valid_entry_point(item) for item in good_entries))

bad_entries = ("", ".py", "plugin", "plugin.exe", "bin/plugin.py",
               r"bin\plugin.py", r"..\..\evil.py", r"C:\evil.py",
               "..py", "plugin.py:_Private", "plugin.py:Bad:Name")
check("entry points that could escape the folder are refused",
      all(not ps.is_valid_entry_point(item) for item in bad_entries))

errors = ps.validate_manifest(valid_manifest(
    requested_capabilities=("commands", "filesystem")))
capability_error = next(error for error in errors
                        if error.field == "requestedCapabilities")
check("unknown capability is refused and known names are listed",
      "filesystem" in capability_error.message
      and all(item in capability_error.message for item in ps.ALL_CAPABILITIES))

errors = ps.validate_manifest(valid_manifest(
    requested_capabilities=("commands", "commands")))
check("duplicated capability is reported",
      any(error.field == "requestedCapabilities"
          and "more than once" in error.message for error in errors))

errors = ps.validate_manifest(valid_manifest(api_version=0))
check("non-positive API version is refused by manifest validation",
      any(error.field == "apiVersion" for error in errors))

future = valid_manifest(api_version=ps.CURRENT_API_VERSION + 5)
check("future API is a valid manifest refused separately by negotiation",
      ps.validate_manifest(future) == ()
      and not ps.is_api_compatible(future.api_version))

errors = ps.validate_against_folder(valid_manifest(), "some-other-folder")
check("manifest id disagreement with folder is refused",
      len(errors) == 1 and errors[0].field == "id"
      and "some-other-folder" in errors[0].message)

check("manifest id matching folder is accepted",
      ps.validate_against_folder(valid_manifest(), "hello-world") == ())

check("manifest errors render as field then fix",
      str(ps.PluginManifestError("id", "Missing.")) == "id: Missing.")


# ---- enablement contract (injected callable supersedes old config lists) ------


check("absent enablement override uses enabled manifest default",
      ps.resolve_enablement("hello-world", True, lambda _id: None)
      is ps.PluginEnablementVerdict.ENABLED_BY_DEFAULT)

check("absent enablement override uses disabled manifest default",
      ps.resolve_enablement("hello-world", False, lambda _id: None)
      is ps.PluginEnablementVerdict.DISABLED_BY_DEFAULT)

check("injected false overrides enabled manifest default",
      ps.resolve_enablement("hello-world", True, lambda _id: False)
      is ps.PluginEnablementVerdict.DISABLED_BY_OVERRIDE)

check("injected true overrides disabled manifest default",
      ps.resolve_enablement("hello-world", False, lambda _id: True)
      is ps.PluginEnablementVerdict.ENABLED_BY_OVERRIDE)

check("enabled verdicts have no exclusion sentence",
      all(ps.describe_exclusion(verdict) is None for verdict in (
          ps.PluginEnablementVerdict.ENABLED_BY_DEFAULT,
          ps.PluginEnablementVerdict.ENABLED_BY_OVERRIDE)))

check("disabled verdicts say why",
      all(ps.describe_exclusion(verdict) for verdict in (
          ps.PluginEnablementVerdict.DISABLED_BY_DEFAULT,
          ps.PluginEnablementVerdict.DISABLED_BY_OVERRIDE)))


# ---- every PluginNamingTests case --------------------------------------------


check("plugin command is namespaced under its plugin",
      ps.compose_command_id("hello-world", "run")
      == "esotericos.plugin.hello-world.run")

check("plugin setting is scoped to its plugin",
      ps.compose_setting_key("hello-world", "greeting")
      == "plugins.hello-world.greeting")

good_actions = ("run", "open-notes", "a", "step2")
check("well-formed command actions are accepted",
      all(ps.is_valid_command_action(item) for item in good_actions))

bad_actions = ("", "Run", "2run", "-run", "run-", "run--fast",
               "run.fast", "run fast", "../../exit")
check("malformed command actions are refused",
      all(not ps.is_valid_command_action(item) for item in bad_actions))

check("overlong command action is refused",
      not ps.is_valid_command_action("a" * 49))

check("command name escaping namespace raises with rule",
      raises(ValueError,
             lambda: ps.compose_command_id("hello-world", "esotericos.exit"),
             "lowercase letters"))

check("bad plugin id cannot compose shared names",
      raises(ValueError, lambda: ps.compose_command_id("Bad Id", "run"))
      and raises(ValueError,
                 lambda: ps.compose_setting_key("../escape", "key")))

good_keys = ("greeting", "last_run", "retry-count", "A1")
check("well-formed setting keys are accepted",
      all(ps.is_valid_setting_key(item) for item in good_keys))

bad_keys = ("", "1key", "_key", "my.key", "my key",
            "plugins.other.key")
check("malformed setting keys are refused",
      all(not ps.is_valid_setting_key(item) for item in bad_keys))

check("overlong setting key is refused",
      not ps.is_valid_setting_key("k" * 65))

check("composed names always carry their prefixes",
      ps.compose_command_id("hello-world", "run").startswith(ps.COMMAND_PREFIX)
      and ps.compose_setting_key("hello-world", "greeting").startswith(
          ps.SETTINGS_PREFIX))


# ---- every PluginVersionTests case -------------------------------------------


check("host API window is coherent",
      ps.MINIMUM_SUPPORTED_API_VERSION >= 1
      and ps.MINIMUM_SUPPORTED_API_VERSION <= ps.CURRENT_API_VERSION)

check("exact API match is compatible without a warning",
      ps.is_api_compatible(ps.CURRENT_API_VERSION)
      and ps.describe_api_incompatibility(ps.CURRENT_API_VERSION) is None)

check("oldest supported API is compatible without a warning",
      ps.is_api_compatible(ps.MINIMUM_SUPPORTED_API_VERSION)
      and ps.describe_api_incompatibility(
          ps.MINIMUM_SUPPORTED_API_VERSION) is None)

too_old = ps.MINIMUM_SUPPORTED_API_VERSION - 1
too_old_reason = ps.describe_api_incompatibility(too_old)
check("one below minimum is refused as too old",
      not ps.is_api_compatible(too_old) and too_old_reason is not None
      and "no longer supports" in too_old_reason
      and str(ps.MINIMUM_SUPPORTED_API_VERSION) in too_old_reason)

too_new = ps.CURRENT_API_VERSION + 1
too_new_reason = ps.describe_api_incompatibility(too_new)
check("one above current is refused as too new",
      not ps.is_api_compatible(too_new) and too_new_reason is not None
      and "Update EsotericOS" in too_new_reason
      and str(ps.CURRENT_API_VERSION) in too_new_reason)

check("nonsense API generations are refused without throwing",
      all(not ps.is_api_compatible(item)
          and ps.describe_api_incompatibility(item) is not None
          for item in (0, -1, -(2 ** 31), 2 ** 31 - 1)))

check("anything inside explicit API window is compatible",
      all(ps.is_api_compatible(item, 4, 2) for item in (2, 3, 4)))

check("anything outside explicit API window is refused",
      all(not ps.is_api_compatible(item, 4, 2) for item in (1, 0, 5, 99)))

check("older supported API is accepted without scolding",
      ps.is_api_compatible(2, 4, 2)
      and ps.describe_api_incompatibility(2, 4, 2) is None)

old_reason = ps.describe_api_incompatibility(1, 4, 2)
new_reason = ps.describe_api_incompatibility(5, 4, 2)
check("too-old and too-new API messages are distinct",
      old_reason is not None and new_reason is not None
      and "Ask the plugin author" in old_reason
      and "Update EsotericOS" in new_reason and old_reason != new_reason)

check("inverted API window accepts nothing",
      all(not ps.is_api_compatible(item, 2, 4) for item in range(-1, 7)))

check("single-generation API window accepts only that generation",
      ps.is_api_compatible(3, 3, 3)
      and not ps.is_api_compatible(2, 3, 3)
      and not ps.is_api_compatible(4, 3, 3))


# ---- catalog and guarded runtime additions -----------------------------------


PLUGIN_SOURCE = """
class Plugin:
    id = {plugin_id!r}
    api_version = {api_version}
    def activate(self, host):
        self.host = host
        {activation}
    def deactivate(self):
        {deactivation}
"""


def write_plugin(root, plugin_id, *, api_version=1, capabilities=(),
                 enabled_by_default=True, activation="pass",
                 deactivation="pass", source=None, write_entry=True):
    folder = root / plugin_id
    folder.mkdir()
    manifest = {
        "id": plugin_id,
        "displayName": plugin_id.replace("-", " ").title(),
        "version": "1.0.0",
        "apiVersion": api_version,
        "entryPoint": "plugin.py:Plugin",
        "requestedCapabilities": list(capabilities),
        "enabledByDefault": enabled_by_default,
    }
    (folder / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    if write_entry:
        body = source or PLUGIN_SOURCE.format(
            plugin_id=plugin_id, api_version=api_version,
            activation=activation, deactivation=deactivation)
        (folder / "plugin.py").write_text(body, encoding="utf-8")
    return folder


class Registration:
    def __init__(self):
        self.disposed = False

    def dispose(self):
        self.disposed = True


class Host:
    def __init__(self):
        self.commands = {}
        self.subscriptions = []
        self.settings = {}
        self.logs = []

    def register_command(self, command_id, title, handler):
        registration = Registration()
        self.commands[command_id] = (title, handler, registration)
        return registration

    def subscribe(self, event_type, handler):
        registration = Registration()
        self.subscriptions.append((event_type, handler, registration))
        return registration

    def get_setting(self, key, fallback=None):
        return self.settings.get(key, fallback)

    def set_setting(self, key, value):
        self.settings[key] = value

    def log(self, level, plugin_id, message):
        self.logs.append((level, plugin_id, message))


with tempfile.TemporaryDirectory() as temporary:
    root = pathlib.Path(temporary)
    check("missing plugin root discovers an empty catalog",
          ps.discover(root / "absent") == [])

    write_plugin(root, "zulu-plugin")
    write_plugin(root, "alpha-plugin")
    (root / "missing-manifest").mkdir()
    records = ps.discover(root)
    check("catalog discovers folders in stable case-insensitive order",
          [record.reporting_id for record in records]
          == ["alpha-plugin", "missing-manifest", "zulu-plugin"])
    missing = records[1]
    check("folder without plugin.json is retained as a refusal",
          missing.state is ps.PluginState.REFUSED
          and "No plugin.json" in (missing.reason or ""))


with tempfile.TemporaryDirectory() as temporary:
    root = pathlib.Path(temporary)
    marker = root / "imported.marker"
    source = f'''
from pathlib import Path
Path({str(marker)!r}).write_text("imported", encoding="utf-8")
class Plugin:
    id = "structural-plugin"
    api_version = 1
    def activate(self, host): pass
    def deactivate(self): pass
'''.lstrip()
    write_plugin(root, "structural-plugin", source=source)
    records = ps.discover(root)
    check("discovery never imports plugin code",
          len(records) == 1 and records[0].is_loadable and not marker.exists())
    host = Host()
    check("plugin code imports only on explicit activate",
          ps.activate(records[0], host) and marker.read_text() == "imported")
    ps.deactivate(records[0])


with tempfile.TemporaryDirectory() as temporary:
    root = pathlib.Path(temporary)
    write_plugin(root, "wrong-api", api_version=ps.CURRENT_API_VERSION + 1,
                 write_entry=False)
    enablement_calls = []

    def should_not_run(plugin_id):
        enablement_calls.append(plugin_id)
        raise AssertionError("API check was not first")

    record = ps.discover(root, is_enabled=should_not_run)[0]
    check("API mismatch is rejected before enablement or entry lookup",
          record.state is ps.PluginState.REFUSED
          and "Built for plugin API" in (record.reason or "")
          and not enablement_calls)


with tempfile.TemporaryDirectory() as temporary:
    root = pathlib.Path(temporary)
    write_plugin(root, "default-off", enabled_by_default=False)
    write_plugin(root, "default-on", enabled_by_default=True)
    overrides = {"default-off": True, "default-on": False}
    records = ps.discover(root, is_enabled=overrides.get)
    by_id = {record.id: record for record in records}
    check("injected enablement overrides manifest defaults in catalog",
          by_id["default-off"].decision is ps.LoadDecision.LOAD
          and by_id["default-on"].decision is ps.LoadDecision.SKIP)


with tempfile.TemporaryDirectory() as temporary:
    root = pathlib.Path(temporary)
    for plugin_id, activation in (
            ("good-one", "pass"),
            ("raising-one", 'raise RuntimeError("activation boom")'),
            ("good-two", "pass")):
        write_plugin(root, plugin_id, activation=activation)
    host = Host()
    records = ps.activate_all(ps.discover(root), host)
    by_id = {record.id: record for record in records}
    check("raising plugin is fault-contained while others remain active",
          by_id["raising-one"].state is ps.PluginState.FAULTED
          and "RuntimeError" in (by_id["raising-one"].reason or "")
          and by_id["good-one"].state is ps.PluginState.ACTIVE
          and by_id["good-two"].state is ps.PluginState.ACTIVE)
    for record in records:
        ps.deactivate(record)


faults = []
host = Host()
bridge = ps.PluginHostBridge(
    "hello-world", (ps.COMMANDS,), host,
    lambda reason, exc: faults.append((reason, exc)))
check("capability set is declaration-and-host-grant intersection",
      ps.capability_intersection(
          (ps.COMMANDS, ps.SETTINGS), (ps.COMMANDS, ps.EVENTS))
      == (ps.COMMANDS,))
check("bridge refuses undeclared or ungranted capability clearly",
      raises(ps.PluginCapabilityDeniedError,
             lambda: bridge.set_setting("greeting", "hello"),
             "'settings' capability"))
check("capability query returns false for unknown names without throwing",
      bridge.has_capability(ps.COMMANDS)
      and not bridge.has_capability("filesystem"))

called = []
registration = bridge.register_command(
    "run", "Run\nnow", lambda: called.append(True))
command_title, command_handler, _ = host.commands[
    "esotericos.plugin.hello-world.run"]
command_handler()
check("granted command is namespaced, flattened, and callable",
      command_title == "Run now" and called == [True])
bridge.deactivate()
check("bridge deactivation releases registrations and rejects reuse",
      registration.disposed
      and raises(RuntimeError,
                 lambda: bridge.register_command("again", "Again", lambda: None),
                 "unloaded"))


with tempfile.TemporaryDirectory() as temporary:
    root = pathlib.Path(temporary)
    source = '''
class Plugin:
    id = "callback-plugin"
    api_version = 1
    def activate(self, host):
        host.register_command("explode", "Explode", self.explode)
    def explode(self):
        raise LookupError("callback boom")
    def deactivate(self):
        self.stopped = True
'''.lstrip()
    write_plugin(root, "callback-plugin", capabilities=(ps.COMMANDS,),
                 source=source)
    host = Host()
    record = ps.discover(root)[0]
    ps.activate(record, host)
    _, callback, registration = host.commands[
        "esotericos.plugin.callback-plugin.explode"]
    callback()
    check("throwing plugin callback faults plugin and releases registrations",
          record.state is ps.PluginState.FAULTED
          and "LookupError" in (record.reason or "")
          and registration.disposed)


with tempfile.TemporaryDirectory() as temporary:
    root = pathlib.Path(temporary)
    write_plugin(root, "shutdown-plugin",
                 deactivation='raise RuntimeError("shutdown boom")')
    record = ps.discover(root)[0]
    ps.activate(record, Host())
    check("deactivate is best-effort and records lifecycle failure",
          not ps.deactivate(record)
          and record.state is ps.PluginState.FAULTED
          and "shutdown boom" in (record.reason or ""))


# Structural contract: imports exist at module scope, but execution is confined to activate.
source_path = pathlib.Path(ps.__file__)
tree = ast.parse(source_path.read_text(encoding="utf-8"))
parents = {}
for node in ast.walk(tree):
    for child in ast.iter_child_nodes(node):
        parents[child] = node


def enclosing_function(node):
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return None


exec_calls = [node for node in ast.walk(tree)
              if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)
              and node.func.attr == "exec_module"]
check("activate is the only path that executes an imported plugin module",
      len(exec_calls) == 1 and enclosing_function(exec_calls[0]) == "activate")

check("module docstring states weak isolation and real process boundary",
      "not a sandbox or a security boundary" in (ast.get_docstring(tree) or "")
      and "out-of-process" in (ast.get_docstring(tree) or ""))
