"""Pure-core and structural checks for keyboard_interception.py."""

import ast
import pathlib

import keyboard_interception as keyboard


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


def event(vk, key, is_down, is_injected=False, extra_info=0):
    return keyboard.RawKeyboardEvent(
        vk, 0, is_down, is_injected, extra_info, key)


class Consumer:
    def __init__(self, consumer_id, priority, evaluate):
        self.consumer_id = consumer_id
        self.priority = priority
        self._evaluate = evaluate
        self.events = []

    def process_key_event(self, raw_event, modifiers):
        self.events.append((raw_event, modifiers))
        return self._evaluate(raw_event, modifiers)


# Faithful translations of KeyboardRouterTests.cs.
router = keyboard.KeyboardRouter()
check("physical modifiers begin released",
      not router.win_held and not router.shift_held)
router.route_key_event(event(0x5B, "Win", True))
check("left Win physical state tracks key-down",
      router.l_win and router.win_held)
router.route_key_event(event(0xA0, "Shift", True))
check("left Shift physical state tracks key-down",
      router.l_shift and router.shift_held)
check("active modifiers combine physical state",
      router.active_modifiers ==
      keyboard.ChordModifiers.WIN | keyboard.ChordModifiers.SHIFT)
router.route_key_event(event(0x5B, "Win", False))
router.route_key_event(event(0xA0, "Shift", False))
check("physical modifier state tracks key-up",
      not router.win_held and not router.shift_held)

router = keyboard.KeyboardRouter()
always_swallow = Consumer(
    "Test", 1,
    lambda _event, _mods: keyboard.KeyboardRoutingVerdict.swallow("Test"))
router.register_consumer(always_swallow)
router.route_key_event(event(0xA2, "Ctrl", True))
router.route_key_event(event(0xA4, "Alt", True))
protected = [router.route_key_event(event(0x2E, "Delete", True)).kind]
router.reset()
router.route_key_event(event(0x5B, "Win", True))
protected.append(router.route_key_event(event(0x4C, "L", True)).kind)
router.reset()
router.route_key_event(event(0xA4, "Alt", True))
protected.append(router.route_key_event(event(0x73, "F4", True)).kind)
router.reset()
router.route_key_event(event(0x5B, "Win", True))
router.route_key_event(event(0xA2, "Ctrl", True))
router.route_key_event(event(0xA0, "Shift", True))
protected.append(router.route_key_event(event(0x42, "B", True)).kind)
router.reset()
protected.append(router.route_key_event(event(0x7B, "F12", True)).kind)
check("protected chords always pass through",
      protected == [keyboard.KeyboardRoutingVerdictKind.PASS_THROUGH] * 5)

router = keyboard.KeyboardRouter()
router.register_consumer(always_swallow)
injected_verdict = router.route_key_event(event(0x53, "S", True, True))
signed_verdict = router.route_key_event(event(
    0x53, "S", True, False,
    keyboard.KeyboardRouter.ESOTERICOS_EXTRA_INFO_SIGNATURE))
check("injected and EsotericOS-signed input never recurse",
      injected_verdict.kind is keyboard.KeyboardRoutingVerdictKind.PASS_THROUGH
      and signed_verdict.kind is
      keyboard.KeyboardRoutingVerdictKind.PASS_THROUGH)

router = keyboard.KeyboardRouter()
takeover = Consumer(
    "Takeover", 1,
    lambda raw, _mods: (keyboard.KeyboardRoutingVerdict.swallow("Takeover")
                        if raw.canonical_key == "S" else
                        keyboard.KeyboardRoutingVerdict.pass_through()))
router.register_consumer(takeover)
router.route_key_event(event(0x5B, "Win", True))
router.route_key_event(event(0xA0, "Shift", True))
down_verdict = router.route_key_event(event(0x53, "S", True))
mask_needed = router.should_mask_win_release
router.clear_consumers()
up_verdict = router.route_key_event(event(0x53, "S", False))
check("swallowed key-down retains key-up ownership",
      down_verdict.kind is keyboard.KeyboardRoutingVerdictKind.SWALLOW
      and mask_needed
      and up_verdict.kind is keyboard.KeyboardRoutingVerdictKind.SWALLOW
      and up_verdict.consumer_id == "Takeover")

triggered = {"S": False, "W": False}


def route_takeover(raw, modifiers):
    if (modifiers == keyboard.ChordModifiers.WIN |
            keyboard.ChordModifiers.SHIFT
            and raw.canonical_key in triggered and raw.is_down):
        key = raw.canonical_key
        return keyboard.KeyboardRoutingVerdict.swallow_with_action(
            lambda: triggered.__setitem__(key, True), "SystemTakeover")
    return keyboard.KeyboardRoutingVerdict.pass_through()


router = keyboard.KeyboardRouter()
router.register_consumer(Consumer("SystemTakeover", 10, route_takeover))
router.route_key_event(event(0x5B, "Win", True))
router.route_key_event(event(0xA0, "Shift", True))
s_verdict = router.route_key_event(event(0x53, "S", True))
s_verdict.action()
w_verdict = router.route_key_event(event(0x57, "W", True))
w_verdict.action()
check("Win+Shift+S and Win+Shift+W route to system takeover",
      s_verdict.kind is
      keyboard.KeyboardRoutingVerdictKind.SWALLOW_WITH_ACTION
      and w_verdict.kind is
      keyboard.KeyboardRoutingVerdictKind.SWALLOW_WITH_ACTION
      and triggered == {"S": True, "W": True})


# Chord parsing/matching and explicit dispatch-order coverage from the source.
sequence = keyboard.KeySequence.parse(
    "Windows+Shift+3, then Meta+Option+Numpad 4")
check("chord parsing canonicalizes aliases and display spelling",
      str(sequence) == "Win+Shift+3, then Win+Alt+Numpad 4"
      and sequence.first == keyboard.KeyChord(
          keyboard.ChordModifiers.WIN | keyboard.ChordModifiers.SHIFT, "3"))
check("sequence prefix matching is strict",
      keyboard.KeySequence.parse("Win+Left").is_prefix_of(
          keyboard.KeySequence.parse("Win+Left, then Win+Up"))
      and not sequence.is_prefix_of(sequence))
parsed, invalid = keyboard.KeySequence.try_parse("Win+Shift")
check("bare modifier final token is rejected", not parsed and invalid is None)

order = []
router = keyboard.KeyboardRouter()
router.register_consumer(Consumer(
    "low", 20,
    lambda _raw, _mods: (order.append("low") or
                         keyboard.KeyboardRoutingVerdict.swallow("low"))))
router.register_consumer(Consumer(
    "high", 1,
    lambda _raw, _mods: (order.append("high") or
                         keyboard.KeyboardRoutingVerdict.swallow("high"))))
dispatch = router.route_key_event(event(0x41, "A", True))
check("consumers dispatch in ascending priority and stop at first claim",
      order == ["high"] and dispatch.consumer_id == "high")


# Pure process-owner policy checks: no native hook is installed.
owner_a = object()
owner_b = object()
claimed_a = keyboard.SingleHookOwnerPolicy.claim(owner_a)
blocked_b = not keyboard.SingleHookOwnerPolicy.claim(owner_b)
keyboard.SingleHookOwnerPolicy.release(owner_a)
claimed_b = keyboard.SingleHookOwnerPolicy.claim(owner_b)
keyboard.SingleHookOwnerPolicy.release(owner_b)
check("single-owner policy permits exactly one process hook owner",
      claimed_a and blocked_b and claimed_b)


# Faithful structural translation of SingleHookEnforcementTests.cs, scoped to
# this standalone process module (the existing portal is a separate pipeline).
path = pathlib.Path(__file__).with_name("keyboard_interception.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)
top_imports = [node for node in tree.body
               if isinstance(node, (ast.Import, ast.ImportFrom))]
check("ctypes has no module-level import",
      all(not (isinstance(node, ast.Import)
              and any(alias.name == "ctypes" for alias in node.names))
          and not (isinstance(node, ast.ImportFrom)
                   and node.module == "ctypes")
          for node in top_imports))
install_calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "SetWindowsHookExW"]
parents = {}
for parent in ast.walk(tree):
    for child in ast.iter_child_nodes(parent):
        parents[child] = parent


def enclosing_function(node):
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return None


check("repository module contains exactly one WH_KEYBOARD_LL install site",
      len(install_calls) == 1
      and enclosing_function(install_calls[0]) == "_thread_main")
start = next(node for node in ast.walk(tree)
             if isinstance(node, ast.FunctionDef) and node.name == "start")
thread_targets = [keyword.value for node in ast.walk(start)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)
                  and node.func.attr == "Thread"
                  for keyword in node.keywords if keyword.arg == "target"]
import_time_service_calls = [node for statement in tree.body
                             if not isinstance(statement,
                                               (ast.FunctionDef, ast.ClassDef,
                                                ast.If))
                             for node in ast.walk(statement)
                             if isinstance(node, ast.Call)
                             and isinstance(node.func, ast.Name)
                             and node.func.id ==
                             "KeyboardInterceptionService"]
check("hook installation is reachable only through explicit start",
      any(isinstance(target, ast.Attribute)
          and target.attr == "_thread_main" for target in thread_targets)
      and not import_time_service_calls)
