"""EsotericOS as a module host: the application's side of the plugin contract.

``plugin_system`` has been complete for a while -- manifest discovery,
capability negotiation, guarded activation, fault capture, clean deactivate --
and nothing ever gave it a host. This is that host, and the agent monitor is
the first thing to run on it.

HOW A MODULE PUTS SOMETHING ON SCREEN, given the bridge grants only commands,
events and settings and there is no UI capability:

    it registers a command named "report" whose handler RETURNS rows.

That is not a workaround, it is the bridge working as designed. Every handler
passed through ``PluginHostBridge`` is wrapped in ``_guard``, which returns the
handler's result and turns any exception into a fault instead of a crash. So
the host can call a module on a timer, take what comes back, and draw it
itself -- and a module that raises simply returns ``None`` and is shown as
faulted.

Doug's decision, 2026-08-11: the host renders, the module never touches Tk.
That is the only version of "an optional module can fail without taking the
host down" that is actually true. A module holding a Tk frame can hang the UI
thread, throw during a paint, or leave a half-built widget behind, and no
amount of exception handling around it makes the window safe again.

The host also refuses to let a module make the panel unreadable: rows are
capped, text is truncated and flattened to one line each. A module gets to be
wrong without being loud.
"""

from __future__ import annotations

import pathlib
import sys
import threading

import plugin_system as ps


REPORT_ACTION = "report"

# What one module may put on screen. Generous enough for anything honest,
# small enough that a runaway module cannot push the rest of the panel off.
MAX_ROWS = 12
MAX_LABEL = 40
MAX_VALUE = 160


def bundled_root():
    """Where the modules that ship with EsotericOS live.

    Frozen builds unpack added data beside the bootloader, which is what
    ``sys._MEIPASS`` points at -- read-only, and correct for discovery. It is
    NOT correct for anything the app writes: that anchors on sys.executable,
    per the frozen-path rule this project learned the hard way.
    """
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return pathlib.Path(base) / "modules"
        return pathlib.Path(sys.executable).parent / "modules"
    return pathlib.Path(__file__).resolve().parent / "modules"


def _one_line(text, limit):
    clean = " ".join(str(text).splitlines()).strip()
    return clean[:limit] + "…" if len(clean) > limit else clean


def normalise_rows(rows):
    """Whatever a module returned -> a list of (label, value) the host can draw.

    Accepts a bare string, a sequence of strings, or a sequence of pairs, and
    refuses anything else rather than guessing. Returning the wrong shape is a
    module bug, and a module bug must read as one instead of as an empty panel.
    """
    if rows is None:
        return None
    if isinstance(rows, str):
        rows = [rows]
    try:
        items = list(rows)
    except TypeError:
        raise TypeError("report() must return a string or a sequence of rows")
    out = []
    for item in items[:MAX_ROWS]:
        if isinstance(item, str):
            out.append((_one_line(item, MAX_LABEL + MAX_VALUE), ""))
            continue
        try:
            label, value = item
        except (TypeError, ValueError):
            raise TypeError(
                "each report() row must be a string or a (label, value) pair")
        out.append((_one_line(label, MAX_LABEL), _one_line(value, MAX_VALUE)))
    return out


class _Registration:
    """A disposable handle. ``_dispose_registration`` calls .dispose()."""

    __slots__ = ("_undo",)

    def __init__(self, undo):
        self._undo = undo

    def dispose(self):
        undo, self._undo = self._undo, None
        if undo is not None:
            undo()


class ModuleHost:
    """Discovery, activation and reporting for the modules EsotericOS ships.

    The five methods ``PluginHostBridge`` calls on a host -- register_command,
    subscribe, get_setting, set_setting, log -- are implemented here and
    nowhere else, so the whole surface a module can reach is one class.
    """

    def __init__(self, root=None, settings=None, enabled=None, on_log=None):
        self.root = pathlib.Path(root) if root is not None else bundled_root()
        self._settings = settings if settings is not None else {}
        self._enabled = enabled if enabled is not None else {}
        self._on_log = on_log
        self._commands = {}
        self._subscribers = {}
        self._records = []
        self._lock = threading.RLock()

    # ---- the host interface the bridge calls -------------------------------

    def register_command(self, command_id, title, handler):
        with self._lock:
            self._commands[command_id] = (title, handler)
        return _Registration(lambda: self._commands.pop(command_id, None))

    def subscribe(self, event_type, handler):
        with self._lock:
            self._subscribers.setdefault(event_type, []).append(handler)

        def undo():
            with self._lock:
                handlers = self._subscribers.get(event_type) or []
                if handler in handlers:
                    handlers.remove(handler)
        return _Registration(undo)

    def get_setting(self, key, fallback=None):
        return self._settings.get(key, fallback)

    def set_setting(self, key, value):
        self._settings[key] = value

    def log(self, level, plugin_id, message):
        if self._on_log is not None:
            self._on_log(f"[module {plugin_id}] {level}: {message}")

    # ---- what the app drives ----------------------------------------------

    def is_enabled(self, plugin_id):
        """None means "no opinion" -- the manifest default then decides."""
        return self._enabled.get(plugin_id)

    def set_enabled(self, plugin_id, value):
        self._enabled[plugin_id] = bool(value)

    def discover(self):
        catalog = ps.PluginCatalog(self.root, is_enabled=self.is_enabled)
        self._records = catalog.discover()
        return self._records

    @property
    def records(self):
        return list(self._records)

    def record(self, plugin_id):
        return next((r for r in self._records if r.reporting_id == plugin_id),
                    None)

    def start(self):
        """Activate every module discovery says should load.

        activate() already contains its own failure: a module that raises on
        import or in activate() lands in FAULTED and the others carry on. So
        there is deliberately no try/except wrapped around this.
        """
        if not self._records:
            self.discover()
        return ps.activate_all(self._records, self)

    def stop(self):
        for record in self._records:
            if record.state is ps.PluginState.ACTIVE:
                ps.deactivate(record)

    def enable(self, plugin_id):
        """Turn a module on and activate it now, without a restart."""
        self.set_enabled(plugin_id, True)
        self.discover()
        record = self.record(plugin_id)
        if record is not None and record.is_loadable:
            ps.activate(record, self)
        return record

    def disable(self, plugin_id):
        """Turn a module off and unload it now.

        Deactivation is what disposes its registrations, so its report command
        disappears with it and the panel stops showing stale numbers.
        """
        record = self.record(plugin_id)
        if record is not None and record.state is ps.PluginState.ACTIVE:
            ps.deactivate(record)
        self.set_enabled(plugin_id, False)
        self.discover()
        return self.record(plugin_id)

    def report(self, plugin_id):
        """Ask one module for its rows. Never raises.

        Returns None when the module is not active, registered no report
        command, faulted inside it (the guard already caught that and returned
        None), or handed back a shape the host cannot draw.
        """
        try:
            command_id = ps.compose_command_id(plugin_id, REPORT_ACTION)
        except ValueError:
            return None
        with self._lock:
            entry = self._commands.get(command_id)
        if entry is None:
            return None
        _title, handler = entry
        rows = handler()          # already guarded by PluginHostBridge
        try:
            return normalise_rows(rows)
        except TypeError as exc:
            self.log("error", plugin_id, f"report() returned {exc}")
            return None

    def reports(self):
        """Every active module's rows, in discovery order: [(record, rows)]."""
        out = []
        for record in self._records:
            if record.state is not ps.PluginState.ACTIVE:
                continue
            out.append((record, self.report(record.reporting_id)))
        return out
