"""EsotericOS as a module host, and the agent monitor as its first module.

The claim this file has to earn is the one in the README: an optional module
can fail without taking the host down. So most of these checks are about
modules that misbehave -- raise on import, raise in activate, raise inside
report, return nonsense, return a screenful of noise -- because a host is only
as good as what it does with a bad module.

Doug's decision, 2026-08-11: the host renders, the module publishes. A module
never touches Tk, which is why none of this needs a display to test.
"""

import json
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import module_host  # noqa: E402
import plugin_system as ps  # noqa: E402


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


TMP = pathlib.Path(tempfile.mkdtemp(prefix="esos-modules-"))


def make(ident, body, *, caps=("commands",), enabled=True, entry="m.py:M"):
    d = TMP / ident
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.json").write_text(json.dumps({
        "id": ident, "displayName": ident.replace("-", " ").title(),
        "version": "1.0.0", "apiVersion": 1, "entryPoint": entry,
        # NOT "capabilities" -- the manifest field is requestedCapabilities,
        # and getting it wrong grants nothing rather than failing loudly.
        "requestedCapabilities": list(caps), "enabledByDefault": enabled,
    }), encoding="utf-8")
    # Every fixture declares id/api_version because activate() checks the
    # CODE's identity against the manifest before it will run anything.
    (d / "m.py").write_text(
        body.replace("class M:\n",
                     f'class M:\n    id = "{ident}"\n    api_version = 1\n'),
        encoding="utf-8")
    return d


GOOD = '''
class M:
    def activate(self, host):
        host.register_command("report", "Report", lambda: [("a", "1"), "bare"])
    def deactivate(self): pass
'''
RAISES_IN_REPORT = '''
class M:
    def activate(self, host):
        host.register_command("report", "Report", self.boom)
    def boom(self): raise RuntimeError("module is broken")
    def deactivate(self): pass
'''
RAISES_IN_ACTIVATE = '''
class M:
    def activate(self, host): raise RuntimeError("cannot start")
    def deactivate(self): pass
'''
RAISES_ON_IMPORT = '''
raise RuntimeError("bad module at import time")
class M:
    def activate(self, host): pass
    def deactivate(self): pass
'''
BAD_SHAPE = '''
class M:
    def activate(self, host):
        host.register_command("report", "Report", lambda: [object()])
    def deactivate(self): pass
'''
FLOOD = '''
class M:
    def activate(self, host):
        host.register_command("report", "Report",
                              lambda: [("x" * 500, "y" * 5000)] * 500)
    def deactivate(self): pass
'''
# A module whose code claims to be something other than its manifest. This is
# the check that stops a folder impersonating another module.
LIAR = '''
class M:
    def activate(self, host): pass
    def deactivate(self): pass
'''

make("good-one", GOOD)
make("bad-report", RAISES_IN_REPORT)
make("bad-activate", RAISES_IN_ACTIVATE)
make("bad-import", RAISES_ON_IMPORT)
make("bad-shape", BAD_SHAPE)
make("flooder", FLOOD)
make("switched-off", GOOD, enabled=False)

# Built by hand: make() keeps code and manifest honest, and this one must lie.
_liar = TMP / "impostor"
_liar.mkdir(parents=True, exist_ok=True)
(_liar / "plugin.json").write_text(json.dumps({
    "id": "impostor", "displayName": "Impostor", "version": "1.0.0",
    "apiVersion": 1, "entryPoint": "m.py:M",
    "requestedCapabilities": ["commands"], "enabledByDefault": True,
}), encoding="utf-8")
(_liar / "m.py").write_text(
    'class M:\n    id = "good-one"\n    api_version = 1\n'
    '    def activate(self, host): pass\n'
    '    def deactivate(self): pass\n', encoding="utf-8")

logs = []
host = module_host.ModuleHost(root=TMP, on_log=logs.append)
records = host.discover()
host.start()


def state(ident):
    r = host.record(ident)
    return None if r is None else r.state


# ---- the good case ---------------------------------------------------------

check("a well-formed module activates", state("good-one") is ps.PluginState.ACTIVE)
check("its report comes back normalised to (label, value) pairs",
      host.report("good-one") == [("a", "1"), ("bare", "")])

# ---- a module that is off --------------------------------------------------

check("a module disabled by its manifest never activates",
      state("switched-off") is not ps.PluginState.ACTIVE)
check("an inactive module reports nothing rather than stale rows",
      host.report("switched-off") is None)

# ---- modules that break, which is the point --------------------------------

check("a module that raises at import is faulted, not fatal",
      state("bad-import") in (ps.PluginState.FAULTED, ps.PluginState.REFUSED))
check("a module that raises in activate() is faulted, not fatal",
      state("bad-activate") is ps.PluginState.FAULTED)
check("a module that raises inside report() returns None, not an exception",
      host.report("bad-report") is None)
# Identity is checked in the CODE, not just the manifest, so a folder cannot
# claim to be a module it is not by editing one JSON field.
check("a module whose code claims another module's id is refused",
      state("impostor") is ps.PluginState.FAULTED
      and "good-one" in (host.record("impostor").reason or ""))
check("a module returning an undrawable shape returns None",
      host.report("bad-shape") is None)
check("the host says WHICH module returned the bad shape",
      any("bad-shape" in line for line in logs))

# The whole promise, stated as one check: every other module is unaffected.
check("one broken module does not stop the others activating",
      state("good-one") is ps.PluginState.ACTIVE
      and state("flooder") is ps.PluginState.ACTIVE)
check("and does not stop them reporting",
      host.report("good-one") == [("a", "1"), ("bare", "")])

# ---- a module cannot make the panel unreadable -----------------------------

flood = host.report("flooder")
check("a flood of rows is capped", len(flood) == module_host.MAX_ROWS)
check("an over-long label is truncated",
      all(len(label) <= module_host.MAX_LABEL + 1 for label, _ in flood))
check("an over-long value is truncated",
      all(len(value) <= module_host.MAX_VALUE + 1 for _, value in flood))

# ---- reports() only ever includes live modules -----------------------------

live = {r.reporting_id for r, _rows in host.reports()}
check("reports() covers active modules only",
      "good-one" in live and "switched-off" not in live
      and "bad-activate" not in live)

# ---- turning one off at runtime --------------------------------------------

host.disable("good-one")
check("disabling deactivates it", state("good-one") is not ps.PluginState.ACTIVE)
check("a disabled module's report command is gone, so no stale numbers",
      host.report("good-one") is None)
host.enable("good-one")
check("enabling brings it back without a restart",
      state("good-one") is ps.PluginState.ACTIVE
      and host.report("good-one") == [("a", "1"), ("bare", "")])

host.stop()
check("stop() deactivates everything that was running",
      all(r.state is not ps.PluginState.ACTIVE for r in host.records))

# ---- normalise_rows on its own ---------------------------------------------

check("a bare string is one row", module_host.normalise_rows("hi") == [("hi", "")])
check("None stays None", module_host.normalise_rows(None) is None)
for bad in (5, [("a", "b", "c")], [None]):
    try:
        module_host.normalise_rows(bad)
        raise AssertionError(f"{bad!r} should have been refused")
    except TypeError:
        pass
print("PASS undrawable shapes are refused rather than guessed at")

# ---- the real module that ships --------------------------------------------

real = module_host.ModuleHost(root=module_host.bundled_root(), on_log=logs.append)
real.discover()
ids = {r.reporting_id for r in real.records}
check("the agent monitor is discovered where the app will look for it",
      "agent-monitor" in ids)
rec = real.record("agent-monitor")
check("its manifest passes validation", rec.is_loadable, )
real.start()
check("it activates", rec.state is ps.PluginState.ACTIVE)

rows = real.report("agent-monitor")
check("it reports two rows, one per agent", rows is not None and len(rows) == 2)
check("labelled Codex and Claude", [label for label, _ in rows] == ["Codex", "Claude"])

# Doug, 2026-08-15: providers issue unannounced resets, so a published reset
# window is a claim and the meter is the measurement. They must not read as
# the same kind of fact.
codex_value = dict(rows)["Codex"]
if "%" in codex_value:
    check("the measured percentage is stated plainly, not hedged",
          "% used" in codex_value)
    check("a provider's reset window is marked as a claim, not stated as fact",
          "claims" in codex_value)

# An observed reset -- the meter actually falling -- is recorded and outranks
# any published date, because it is the only one witnessed on this machine.
watched = module_host.ModuleHost(root=module_host.bundled_root())
watched.discover()
watched.start()
inst = watched.record("agent-monitor")._instance
inst._host.set_setting("codex-last-percent", 90.0)
inst._note_reset(4.0)
check("a fall in the meter is recorded as an observed reset",
      inst._host.get_setting("codex-last-reset") is not None)
before = inst._host.get_setting("codex-last-reset")
inst._note_reset(6.0)          # a rise is not a reset
check("a rise in the meter is not mistaken for a reset",
      inst._host.get_setting("codex-last-reset") == before)
inst._host.set_setting("codex-last-percent", 50.0)
inst._note_reset(48.0)         # small wobble, under the threshold
check("ordinary sampling wobble is not a reset",
      inst._host.get_setting("codex-last-reset") == before)
watched.stop()
# Whether THIS machine has usage data is not this test's business; that every
# outcome is a sentence rather than a blank or a traceback, is.
check("each row says something, with or without local data",
      all(value.strip() for _label, value in rows))
real.stop()

# ---- the module must not be able to reach the UI ---------------------------
# Stated against the source, because the day someone adds "import tkinter"
# here is the day the isolation promise quietly stops being true.
src = (pathlib.Path(__file__).parent / "modules" / "agent-monitor"
       / "agent_monitor.py").read_text(encoding="utf-8")
check("the shipped module imports no UI toolkit",
      "tkinter" not in src and "import tk" not in src)
check("the shipped module starts no thread of its own",
      "threading" not in src and "Thread" not in src)

shutil.rmtree(TMP, ignore_errors=True)
