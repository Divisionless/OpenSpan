"""Parser, injection-plan, consumer and lifetime checks for script_engine.py.

No Tk root is built, no hook is installed, no key is injected, no window is
touched and no config outside a temporary directory is written.  Every effect
is behind an injected collaborator, so the suite exercises the decisions and
performs none of them.

    python win\\test_script_engine.py
"""

import ast
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from config_store import ConfigStore
from keyboard_interception import (ChordModifiers, KeyChord, KeySequence,
                                   KeyboardRouter, KeyboardRoutingVerdictKind,
                                   RawKeyboardEvent)
import script_engine as se
from script_engine import (VK_BY_KEY, KeyStroke, ScriptConsumer, ScriptEngine,
                           ScriptRunner, load_directory, parse_script,
                           plan_send)
from script_scope import (Action, Binding, BindingSet, ResolutionVerdict,
                          ScopeLevel, ScreenFacts, Verb, WindowFacts)
from settings_service import FeatureRegistry, SettingsService


fails = []


def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        if detail:
            print("      " + str(detail))
        fails.append(name)


CODE = WindowFacts("code", "esoteric-path - Visual Studio Code",
                   "Chrome_WidgetWin_1")
NOTEPAD = WindowFacts("notepad", "Untitled - Notepad", "Notepad")
DESK = ScreenFacts("9f2c4a1b7e0d3355", True)
CTRL_ALT_H = KeySequence.parse("Ctrl+Alt+H").first
CTRL_ALT_J = KeySequence.parse("Ctrl+Alt+J").first


# ---- the format parses to the model -----------------------------------------

GOOD = """\
# A worked example: three scopes, a nested one, and a fall-through.
scope os
  Ctrl+Alt+H -> window tile left-half
  Ctrl+Alt+K -> send Ctrl+S

scope screen primary
  priority 5
  Ctrl+Alt+H -> window center

scope screen 9f2c4a1b7e0d3355 window process=code title~"Visual Studio"
  Ctrl+Alt+H -> pass
  Ctrl+Alt+J -> send Ctrl+K, then Ctrl+D ; window restore

scope window class=Notepad
  Ctrl+Alt+L -> catalog ms-settings-display
"""

parsed = parse_script(GOOD, "good.eos")
check("a well-formed script parses with no problems",
      not parsed.problems, [item.describe() for item in parsed.problems])
check("every binding line became a binding",
      len(parsed.bindings) == 6, len(parsed.bindings))
check("declaration order is recorded in order",
      [item.order for item in parsed.bindings] == list(range(6)))
check("the source file and line are recorded on every binding",
      all(item.source == "good.eos" for item in parsed.bindings)
      and [item.line for item in parsed.bindings] == [3, 4, 8, 11, 12, 15],
      [(item.line, str(item.chord)) for item in parsed.bindings])

levels = [item.scope.level for item in parsed.bindings]
check("scope headers set the level of everything under them",
      levels == [ScopeLevel.OS, ScopeLevel.OS, ScopeLevel.SCREEN,
                 ScopeLevel.WINDOW, ScopeLevel.WINDOW, ScopeLevel.WINDOW],
      levels)

nested = parsed.bindings[3].scope
check("a scope naming a screen and a window nests both constraints",
      nested.screen_key == "9f2c4a1b7e0d3355" and nested.process == "code"
      and nested.title_contains == "Visual Studio"
      and nested.level is ScopeLevel.WINDOW and nested.specificity == 3,
      nested.describe())
check("a quoted value keeps its spaces",
      parsed.bindings[3].scope.title_contains == "Visual Studio")
check("priority applies to the bindings under its header",
      parsed.bindings[2].priority == 5)
check("a new scope header resets priority to zero",
      parsed.bindings[3].priority == 0 and parsed.bindings[0].priority == 0)
check("pass parses to the single fall-through action",
      parsed.bindings[3].is_pass)
check("two actions separated by a spaced semicolon both parse",
      len(parsed.bindings[4].actions) == 2
      and parsed.bindings[4].actions[0].verb is Verb.SEND
      and parsed.bindings[4].actions[1].verb is Verb.WINDOW
      and parsed.bindings[4].actions[1].name == "restore")
check("a send payload is a fully parsed key sequence, not text",
      len(parsed.bindings[4].actions[0].payload.chords) == 2)
check("window tile resolves its zone at parse time",
      parsed.bindings[0].actions[0].payload is not None
      and parsed.bindings[0].actions[0].name == "tile")
check("catalog keeps the record id",
      parsed.bindings[5].actions[0].verb is Verb.CATALOG
      and parsed.bindings[5].actions[0].argument == "ms-settings-display")
check("a comment line and a trailing comment are both ignored",
      not parse_script("# only a comment\nscope os  # here too\n",
                       "c.eos").problems)

implicit = parse_script("Ctrl+Alt+H -> window center\n", "i.eos")
check("a binding before any header belongs to the implicit os scope",
      len(implicit.bindings) == 1
      and implicit.bindings[0].scope.level is ScopeLevel.OS)

check("parsing is deterministic",
      parse_script(GOOD, "good.eos") == parse_script(GOOD, "good.eos"))


# ---- everything wrong is reported, never raised ------------------------------

BAD_LINES = [
    ("scope elsewhere", "unknown scope keyword"),
    ("Ctrl+Alt+H->window center", "arrow with no spaces"),
    ("Ctrl+Alt+Nope -> window center", "unknown key"),
    ("Ctrl+K, then Ctrl+D -> window center", "multi-chord trigger"),
    ("Alt+F4 -> window center", "protected chord"),
    ("Ctrl+Alt+H -> fly away", "unknown verb"),
    ("Ctrl+Alt+H -> window tile sideways", "unknown zone"),
    ("Ctrl+Alt+H -> window refine sideways", "unknown direction"),
    ("Ctrl+Alt+H -> pass ; window center", "pass beside another action"),
    ("Ctrl+Alt+H -> send", "send with no keys"),
    ("Ctrl+Alt+H -> send Ctrl+Nope", "send of an unknown key"),
    ("Ctrl+Alt+H -> window", "window with no verb"),
    ("Ctrl+Alt+H -> window center extra", "too many arguments"),
    ("Ctrl+Alt+H -> catalog", "catalog with no id"),
    ("Ctrl+Alt+H -> catalog \"bad id\"", "catalog id with a space"),
    ("Ctrl+Alt+H -> ", "no action after the arrow"),
    ("scope screen notakey", "screen key that is not a stable key"),
    ("scope screen", "screen with no key"),
    ("scope window process", "criterion with no operator"),
    ("scope window title=Code", "title matched with = instead of ~"),
    ("scope window colour=red", "unknown criterion"),
    ("scope window", "window with no criteria"),
    ("scope os window process=code", "os combined with anything else"),
    ("scope window process=code process=x", "the same criterion twice"),
    ("priority high", "priority that is not a number"),
    ("priority", "priority with no value"),
    ("wibble", "an unknown directive"),
    ("Ctrl+Alt+H -> window tile \"left", "an unterminated quote"),
]

for line, why in BAD_LINES:
    outcome = parse_script(line + "\n", "bad.eos")
    ok = (len(outcome.problems) == 1 and not outcome.bindings
          and outcome.problems[0].line == 1
          and outcome.problems[0].column >= 1
          and outcome.problems[0].source == "bad.eos"
          and bool(outcome.problems[0].message))
    check(f"reported, not raised: {why}", ok,
          [item.describe() for item in outcome.problems] or "no problem at all")

located = parse_script("scope os\n  Ctrl+Alt+H -> fly away\n", "loc.eos")
check("a problem names the file, the line and the column",
      located.problems[0].describe().startswith("loc.eos:2:")
      and located.problems[0].column == 17,
      located.problems[0].describe())

recovered = parse_script(
    "scope os\nCtrl+Alt+H -> fly away\nCtrl+Alt+J -> window center\n", "r.eos")
check("one bad line drops only that binding; the rest of the file still runs",
      len(recovered.bindings) == 1 and len(recovered.problems) == 1
      and recovered.bindings[0].chord == CTRL_ALT_J)

check("a bad scope header drops the header, not the file",
      len(parse_script("scope nonsense\nCtrl+Alt+H -> window center\n",
                       "h.eos").bindings) == 1)

check("an empty script is not an error",
      parse_script("", "e.eos") == se.ParsedScript((), ()))


# ---- a malformed file disables only itself ----------------------------------

with tempfile.TemporaryDirectory() as directory:
    root = pathlib.Path(directory)
    (root / "a-good.eos").write_text(GOOD, encoding="utf-8")
    (root / "b-messy.eos").write_text(
        "scope os\nCtrl+Alt+P -> fly away\nCtrl+Alt+Q -> window center\n",
        encoding="utf-8")
    (root / "c-unreadable.eos").write_bytes(b"scope os\n\xff\xfe\x00rubbish\n")
    (root / "ignored.txt").write_text("Ctrl+Alt+Z -> window center\n",
                                      encoding="utf-8")
    loaded, files = load_directory(root)

    check("every .eos file is listed, in name order",
          [item.name for item in files]
          == ["a-good.eos", "b-messy.eos", "c-unreadable.eos"],
          [item.name for item in files])
    check("a file with a different extension is not a script",
          all("ignored" not in item.name for item in files))
    check("the undecodable file is disabled with a reason",
          files[2].enabled is False and files[2].bindings == 0
          and "cannot read" in files[2].disabled_reason,
          files[2].describe())
    check("the good file is unaffected by its neighbour's failure",
          files[0].enabled and files[0].bindings == 6
          and not files[0].problems)
    check("the messy file keeps its good binding and reports its bad one",
          files[1].enabled and files[1].bindings == 1
          and len(files[1].problems) == 1)
    check("the loaded set is every surviving binding across every file",
          len(loaded) == 7, len(loaded))
    check("declaration order continues across files",
          [item.order for item in loaded.bindings] == list(range(7)))
    check("loading is deterministic",
          load_directory(root)[0].bindings == loaded.bindings)

check("a directory that does not exist is no scripts, not an error",
      len(load_directory(pathlib.Path(tempfile.gettempdir())
                         / "esotericos-no-such-scripts-dir")[0]) == 0)


# ---- planning an injection (pure) -------------------------------------------

def plan(text, held=ChordModifiers.NONE):
    return [(stroke.vk, stroke.is_down, stroke.extended)
            for stroke in plan_send(KeySequence.parse(text), held)]


check("the virtual-key table agrees with the router's own spelling",
      VK_BY_KEY["A"] == 0x41 and VK_BY_KEY["F1"] == 0x70
      and VK_BY_KEY["Numpad4"] == 0x64 and VK_BY_KEY["Escape"] == 0x1B
      and VK_BY_KEY["Left"] == 0x25)
check("the OEM keys the router cannot name are still sendable",
      VK_BY_KEY["Grave"] == 0xC0 and VK_BY_KEY["Slash"] == 0xBF
      and VK_BY_KEY["Semicolon"] == 0xBA and VK_BY_KEY["Minus"] == 0xBD)

check("a plain chord presses its modifier, taps the key, and lets go",
      plan("Ctrl+S") == [(0xA2, True, False), (0x53, True, False),
                         (0x53, False, False), (0xA2, False, False)],
      plan("Ctrl+S"))
check("a modifier the user is holding but the chord does not want is released",
      plan("Ctrl+S", ChordModifiers.CTRL | ChordModifiers.ALT)
      == [(0xA4, False, False), (0x53, True, False), (0x53, False, False),
          (0xA4, True, False)],
      plan("Ctrl+S", ChordModifiers.CTRL | ChordModifiers.ALT))
check("a modifier the chord also wants is never released and re-tapped",
      (0xA2, False, False) not in plan("Ctrl+S", ChordModifiers.CTRL
                                       | ChordModifiers.ALT))
check("the physical modifier state is restored at the end",
      plan("A", ChordModifiers.CTRL)
      == [(0xA2, False, False), (0x41, True, False), (0x41, False, False),
          (0xA2, True, False)])
check("a two-chord send holds the shared modifier across both",
      plan("Ctrl+K, then Ctrl+D")
      == [(0xA2, True, False), (0x4B, True, False), (0x4B, False, False),
          (0x44, True, False), (0x44, False, False), (0xA2, False, False)],
      plan("Ctrl+K, then Ctrl+D"))
check("the Win key and the arrows are sent as extended keys",
      plan("Win+Left") == [(0x5B, True, True), (0x25, True, True),
                           (0x25, False, True), (0x5B, False, True)],
      plan("Win+Left"))
check("sending nothing plans nothing",
      plan_send(KeySequence([]), ChordModifiers.NONE) == ())
check("planning is deterministic",
      plan("Ctrl+Alt+Shift+Win+S", ChordModifiers.ALT)
      == plan("Ctrl+Alt+Shift+Win+S", ChordModifiers.ALT))

engine_source = pathlib.Path(__file__).with_name("script_engine.py").read_text(
    encoding="utf-8")
engine_tree = ast.parse(engine_source)
sender = next(node for node in ast.walk(engine_tree)
              if isinstance(node, ast.FunctionDef) and node.name == "send_strokes")
check("injected keys are tagged with the router's own signature, so a send "
      "cannot re-trigger the binding that sent it",
      "ESOTERICOS_EXTRA_INFO_SIGNATURE" in ast.dump(sender))


# ---- running a verb ----------------------------------------------------------

class FakeResult:
    def __init__(self, performed=True, reason="done"):
        self.performed = performed
        self.reason = reason


class FakeActions:
    def __init__(self):
        self.calls = []

    def _record(self, name, *args):
        self.calls.append((name, *args))
        return FakeResult()

    def tile_focused(self, zone):
        return self._record("tile", zone)

    def refine_focused(self, direction):
        return self._record("refine", direction)

    def restore_focused(self):
        return self._record("restore")

    def center_focused(self):
        return self._record("center")

    def apply_rules_now(self):
        return self._record("apply-rules")

    def save_preset(self, name):
        return self._record("save-preset", name)

    def restore_preset(self, name):
        return self._record("restore-preset", name)


sent = []
actions = FakeActions()
runner = ScriptRunner(actions=actions, sender=lambda strokes: sent.append(
    tuple(strokes)) or True, activator=lambda record: FakeResult(True, "opened"))

verbs = parse_script(
    "scope os\n"
    "Ctrl+Alt+A -> window tile left-half\n"
    "Ctrl+Alt+B -> window refine up\n"
    "Ctrl+Alt+C -> window restore ; window center ; window apply-rules\n"
    "Ctrl+Alt+D -> window save-preset desk ; window restore-preset desk\n"
    "Ctrl+Alt+E -> send Ctrl+S\n"
    "Ctrl+Alt+F -> catalog ms-settings-display\n"
    "Ctrl+Alt+G -> pass\n", "verbs.eos").bindings
outcomes = [runner.run(binding, ChordModifiers.CTRL | ChordModifiers.ALT)
            for binding in verbs]

check("every parsed verb reaches its WindowActions method",
      [name for name, *_ in actions.calls]
      == ["tile", "refine", "restore", "center", "apply-rules",
          "save-preset", "restore-preset"],
      actions.calls)
check("a preset name is passed through unchanged",
      actions.calls[5][1] == "desk" and actions.calls[6][1] == "desk")
check("send reaches the injector with a plan, not with text",
      len(sent) == 1 and all(isinstance(item, KeyStroke) for item in sent[0]))
check("send neutralises the modifiers that triggered the binding",
      (0xA4, False) in [(item.vk, item.is_down) for item in sent[0]])
check("catalog reaches the activator",
      outcomes[5].steps[0].performed and outcomes[5].steps[0].reason == "opened")
check("pass is a performed step that does nothing",
      outcomes[6].performed and outcomes[6].steps[0].step == "pass")
check("every outcome reports performed",
      all(outcome.performed for outcome in outcomes), outcomes)

barren = ScriptRunner(actions=None)
lonely = barren.run(parse_script("Ctrl+Alt+A -> window center\n",
                                 "x.eos").bindings[0])
check("a window verb with no WindowActions reports instead of raising",
      not lonely.performed
      and "not available" in lonely.steps[0].reason, lonely)

class Exploder:
    def __call__(self, strokes):
        raise OSError("SendInput exploded")


boom = ScriptRunner(actions=actions, sender=Exploder())
result = boom.run(parse_script("Ctrl+Alt+A -> send Ctrl+S\n",
                               "x.eos").bindings[0])
check("a verb that raises becomes a reported failure, not an exception",
      not result.performed and "SendInput exploded" in result.steps[0].reason,
      result)


# ---- the consumer ------------------------------------------------------------

class Probe:
    def __init__(self, facts=CODE, screen=DESK, explode=False):
        self.facts = facts
        self.screen = screen
        self.explode = explode
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.explode:
            raise OSError("no foreground window")
        return self.facts, self.screen


class Recorder:
    def __init__(self):
        self.runs = []

    def run(self, binding, held=ChordModifiers.NONE):
        self.runs.append((binding, held))


def event(key="H", down=True, vk=0x48):
    return RawKeyboardEvent(vk, 0, down, False, 0, key)


CONSUMER_SET = BindingSet(parse_script(
    "scope window process=code\n"
    "Ctrl+Alt+H -> window center\n"
    "Ctrl+Alt+J -> pass\n"
    "scope os\n"
    "Ctrl+Alt+M -> window restore\n", "c.eos").bindings)

both = ChordModifiers.CTRL | ChordModifiers.ALT
probe = Probe()
recorder = Recorder()
consumer = ScriptConsumer(recorder, CONSUMER_SET, probe=probe)

check("the consumer registers at a priority ahead of the shipped defaults",
      consumer.priority == se.DEFAULT_PRIORITY and se.DEFAULT_PRIORITY < 50)
check("a key-up is never claimed and never probes the desk",
      consumer.process_key_event(event(down=False), both).kind
      is KeyboardRoutingVerdictKind.PASS_THROUGH and probe.calls == 0)
check("a chord no binding mentions passes through without probing the desk",
      consumer.process_key_event(event("Z", vk=0x5A), both).kind
      is KeyboardRoutingVerdictKind.PASS_THROUGH and probe.calls == 0)
check("the bare key of a bound chord, pressed alone, is not claimed",
      consumer.process_key_event(event(), ChordModifiers.NONE).kind
      is KeyboardRoutingVerdictKind.PASS_THROUGH and probe.calls == 0)

verdict = consumer.process_key_event(event(), both)
check("a chord whose scope matches is swallowed with a deferred action",
      verdict.kind is KeyboardRoutingVerdictKind.SWALLOW_WITH_ACTION
      and verdict.consumer_id == consumer.consumer_id
      and probe.calls == 1 and not recorder.runs)
verdict.action()
check("the deferred action runs the winning binding with the held modifiers",
      len(recorder.runs) == 1 and recorder.runs[0][1] == both
      and recorder.runs[0][0].chord == CTRL_ALT_H)

elsewhere = ScriptConsumer(Recorder(), CONSUMER_SET, probe=Probe(NOTEPAD, DESK))
check("a claimed chord whose scope does not match is not swallowed",
      elsewhere.process_key_event(event(), both).kind
      is KeyboardRoutingVerdictKind.PASS_THROUGH)
check("an os-scoped binding still matches from a window that matches nothing",
      elsewhere.process_key_event(event("M", vk=0x4D), both).kind
      is KeyboardRoutingVerdictKind.SWALLOW_WITH_ACTION)
check("an explicit pass with nothing behind it reaches the OS",
      ScriptConsumer(Recorder(), CONSUMER_SET, probe=Probe()
                     ).process_key_event(event("J", vk=0x4A), both).kind
      is KeyboardRoutingVerdictKind.PASS_THROUGH)

guarded = ScriptConsumer(Recorder(), BindingSet([Binding(
    KeyChord(ChordModifiers.CTRL | ChordModifiers.ALT, "Delete"),
    actions=(Action(Verb.WINDOW, "center"),))]), probe=Probe())
check("a protected chord is refused by the consumer even when bound",
      guarded.process_key_event(
          RawKeyboardEvent(0x2E, 0, True, False, 0, "Delete"), both).kind
      is KeyboardRoutingVerdictKind.PASS_THROUGH)

blind = ScriptConsumer(Recorder(), CONSUMER_SET, probe=Probe(explode=True))
verdict = blind.process_key_event(event("M", vk=0x4D), both)
check("a probe that fails does not raise and does not stop os-level bindings",
      verdict.kind is KeyboardRoutingVerdictKind.SWALLOW_WITH_ACTION
      and blind.probe_failures == 1)
check("a probe that fails cannot satisfy a window-scoped binding",
      blind.process_key_event(event(), both).kind
      is KeyboardRoutingVerdictKind.PASS_THROUGH)

se._SCREEN_CACHE[1234] = ScreenFacts("deadbeefdeadbeef", True)
se.reset_screen_cache()
check("the EDID lookup is cached off the hook thread and can be reset",
      not se._SCREEN_CACHE
      and "attached_identities" not in ast.dump(next(
          item for item in ast.walk(engine_tree)
          if isinstance(item, ast.FunctionDef)
          and item.name == "_screen_for_window")))

check("the same event twice gives the same verdict",
      consumer.process_key_event(event(), both).kind
      is consumer.process_key_event(event(), both).kind)

swapped = ScriptConsumer(Recorder(), CONSUMER_SET, probe=Probe())
swapped.set_bindings(BindingSet())
check("reloading to an empty set stops the consumer claiming anything",
      swapped.process_key_event(event(), both).kind
      is KeyboardRoutingVerdictKind.PASS_THROUGH)


# ---- lifetime ----------------------------------------------------------------

class FakeService:
    def __init__(self, installed=False):
        self.is_hook_installed = installed
        self.router = KeyboardRouter()
        self.consumers = []
        self.starts = 0
        self.stops = 0

    def register_consumer(self, consumer):
        self.consumers.append(consumer)

    def unregister_consumer(self, consumer_id):
        self.consumers = [item for item in self.consumers
                          if item.consumer_id != consumer_id]

    def start(self):
        self.starts += 1
        self.is_hook_installed = True

    def stop(self):
        self.stops += 1
        self.is_hook_installed = False


with tempfile.TemporaryDirectory() as directory:
    root = pathlib.Path(directory)
    scripts = root / "scripts"
    store = ConfigStore(root / "config-root")
    settings = SettingsService(store, FeatureRegistry([se.FEATURE_DECLARATION]))
    service = FakeService()
    engine = ScriptEngine(store=store, settings_service=settings,
                          service=service, directory=scripts,
                          actions=FakeActions(), probe=Probe())

    check("construction installs no hook and registers no consumer",
          not service.is_hook_installed and not service.consumers
          and service.starts == 0)
    check("construction reads nothing and creates no script directory",
          not scripts.exists() and engine.scripts() == ()
          and len(engine.bindings) == 0)

    scripts.mkdir()
    (scripts / "desk.eos").write_text(GOOD, encoding="utf-8")
    (scripts / "typo.eos").write_text("Ctrl+Alt+Z -> fly away\n",
                                      encoding="utf-8")
    reloaded = engine.reload()

    check("reload reports the directory, the files and the problems",
          reloaded.directory == str(scripts) and reloaded.bindings == 6
          and len(reloaded.files) == 2 and len(reloaded.problems) == 1
          and not reloaded.ok, reloaded)
    check("the script listing is what a Scripts surface would show",
          [item.describe() for item in engine.scripts()]
          == ["desk.eos: 6 binding(s)", "typo.eos: 0 binding(s), 1 problem(s)"],
          [item.describe() for item in engine.scripts()])
    check("problems are reported through the engine, located",
          engine.problems()[0].describe().startswith("typo.eos:1:"),
          engine.problems()[0].describe())
    check("describe_bindings explains every live binding",
          len(engine.describe_bindings()) == 6
          and "desk.eos:3" in engine.describe_bindings()[0],
          engine.describe_bindings()[:1])

    explained = engine.explain("Ctrl+Alt+H", CODE, DESK)
    check("explain answers what a chord would do here, without pressing it",
          explained.verdict is ResolutionVerdict.HANDLE
          and explained.binding.scope.level is ScopeLevel.SCREEN
          and len(explained.passed_over) == 1, explained.describe())
    check("explain on a window that matches nothing falls to the widest scope",
          engine.explain("Ctrl+Alt+H", NOTEPAD, ScreenFacts("x", False)
                         ).binding.scope.level is ScopeLevel.OS)
    check("explain is deterministic",
          engine.explain("Ctrl+Alt+H", CODE, DESK)
          == engine.explain("Ctrl+Alt+H", CODE, DESK))

    started = engine.start()
    check("start registers the consumer and installs the hook it now owns",
          started.performed and started.reason == "started"
          and service.starts == 1 and len(service.consumers) == 1
          and engine.is_running, started)
    check("start refuses to start twice",
          engine.start().reason == "already running" and service.starts == 1)
    stopped = engine.stop()
    check("stop unregisters the consumer and stops the hook it owned",
          stopped.performed and service.stops == 1
          and not service.consumers and not engine.is_running)
    check("stop refuses when it is not running",
          not engine.stop().performed)

    shared = FakeService(installed=True)
    joiner = ScriptEngine(store=store, settings_service=settings,
                          service=shared, directory=scripts,
                          actions=FakeActions(), probe=Probe())
    joined = joiner.start()
    check("when another owner already holds the hook, the engine joins it",
          joined.performed and joined.reason == "joined the running keyboard hook"
          and shared.starts == 0 and len(shared.consumers) == 1)
    joiner.stop()
    check("stopping a joined engine unregisters but never stops the shared hook",
          shared.stops == 0 and shared.is_hook_installed
          and not shared.consumers)

    settings.set_setting(se.FEATURE_ID, se.PRIORITY_SETTING, 70)
    late = ScriptEngine(store=store, settings_service=settings,
                        service=FakeService(), directory=scripts,
                        actions=FakeActions(), probe=Probe())
    late.start()
    check("the consumer priority is a declared, persisted setting",
          late.consumer.priority == 70)
    late.stop()
    settings.set_setting(se.FEATURE_ID, se.PRIORITY_SETTING,
                         se.DEFAULT_PRIORITY)


check("the feature is declared for the settings service, hooks disclosed",
      se.FEATURE_DECLARATION.id == se.FEATURE_ID
      and se.FEATURE_DECLARATION.section in ("Features", "Shortcuts")
      and se.FEATURE_DECLARATION.default_enabled is False
      and se.FEATURE_DECLARATION.input_hooks
      and "WH_KEYBOARD_LL" in se.FEATURE_DECLARATION.input_hooks[0])
check("the feature declares no shortcuts of its own -- scripts declare theirs",
      not se.FEATURE_DECLARATION.default_shortcuts)

registry = FeatureRegistry()
ScriptEngine.ensure_declaration(registry)
ScriptEngine.ensure_declaration(registry)
check("joining an existing registry is idempotent",
      len(registry.features) == 1)


# ---- structural promises -----------------------------------------------------

NATIVE = {"WinDLL", "SetWindowsHookExW", "SetWindowsHookExA", "SendInput",
          "RegisterHotKey", "GetForegroundWindow", "read_text", "mkdir"}
module_calls = {getattr(node.func, "id", "") or getattr(node.func, "attr", "")
                for statement in engine_tree.body
                if not isinstance(statement, (ast.FunctionDef, ast.ClassDef))
                for node in ast.walk(statement) if isinstance(node, ast.Call)}
check("nothing native, and nothing that reads a file, runs at import time",
      not (NATIVE & module_calls), sorted(NATIVE & module_calls))
check("the engine installs no hook of its own -- the service owns the one hook",
      "SetWindowsHookEx" not in engine_source
      and "RegisterHotKey" not in engine_source)
# The docstring names WH_MOUSE_LL to state the limit; the code must not use it.
engine_code = engine_source.replace(ast.get_docstring(engine_tree) or "", "")
check("the engine installs no mouse hook; v1 is keyboard only",
      "WH_MOUSE_LL" not in engine_code and "WM_MOUSE" not in engine_code
      and "WH_MOUSE_LL" in engine_source)
check("ctypes is imported inside functions, never at module scope",
      "ctypes" not in {alias.name for statement in engine_tree.body
                       if isinstance(statement, ast.Import)
                       for alias in statement.names})
check("the parser catches its own errors instead of raising at a caller",
      any(isinstance(node, ast.Try) for node in ast.walk(
          next(item for item in ast.walk(engine_tree)
               if isinstance(item, ast.FunctionDef)
               and item.name == "parse_script"))))
check("the consumer cannot raise inside the hook: probing is guarded",
      any(isinstance(node, ast.Try) for node in ast.walk(
          next(item for item in ast.walk(engine_tree)
               if isinstance(item, ast.FunctionDef)
               and item.name == "facts"))))


print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
